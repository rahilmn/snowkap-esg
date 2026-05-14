"""L7 — CompanyAgent stateful intelligence (scaffold).

Per-tenant agent that maintains an in-process "what we currently
believe" state graph + subscribes to the L6 advisor queue + audits
every belief mutation through the L2/L3/L4 discipline gates.

Scope of this scaffold:
  - `Belief` — single named claim with confidence + provenance
  - `CompanyAgent.update_belief()` — audited state change
  - `CompanyAgent.subscribe_to_advisor_queue()` — filtered read of L6
    events for this tenant

Deferred (fresh-session work): domain belief model, LLM revision
logic, persistence, API surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from engine.audit import (
    append_decision,
    make_toulmin,
    read_advisor_queue,
)
from engine.governance.belief_schema import TypedBelief


@dataclass
class Belief:
    """A single named claim about a tenant.

    `confidence` mirrors the L2 `uncertainty` enum so the audit chain
    stays type-consistent: low | moderate | high | unverified.
    """
    name: str
    value: Any
    confidence: str       # low | moderate | high
    rationale: str
    actor: str
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


# Phase C — AccountAgent-style 5-state lifecycle (ported from Base Version)
STATE_INITIALIZING = "StageInitializing"
STATE_WATCHING = "StageWatching"
STATE_RECOMMENDING = "StageRecommending"
STATE_DISPATCHING = "StageDispatching"
STATE_RESOLVING = "StageResolving"

_VALID_STATES = frozenset({
    STATE_INITIALIZING, STATE_WATCHING, STATE_RECOMMENDING,
    STATE_DISPATCHING, STATE_RESOLVING,
})

# from_state -> set of allowed to_states
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_INITIALIZING: frozenset({STATE_WATCHING}),
    STATE_WATCHING:     frozenset({STATE_RECOMMENDING, STATE_RESOLVING}),
    STATE_RECOMMENDING: frozenset({STATE_DISPATCHING, STATE_WATCHING}),
    STATE_DISPATCHING:  frozenset({STATE_WATCHING}),
    STATE_RESOLVING:    frozenset({STATE_WATCHING}),
}

_REQUIRED_TOULMIN_KEYS = ("claim", "grounds", "warrant")


class InvalidTransition(RuntimeError):
    """Raised on an illegal state transition."""


class ToulminMissing(RuntimeError):
    """Raised when AgentAction is constructed without required Toulmin keys."""


@dataclass
class AgentAction:
    """A single action emitted by the agent in the course of its lifecycle.

    Required Toulmin chain (claim / grounds / warrant) makes every action
    auditable and L4-compatible.
    """
    agent_id: str
    action_type: str  # dispatch | recommend | transition | escalate
    payload: dict[str, Any] = field(default_factory=dict)
    toulmin_chain: dict[str, Any] = field(default_factory=dict)
    phase_k_tags: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def __post_init__(self) -> None:
        missing = [k for k in _REQUIRED_TOULMIN_KEYS if k not in self.toulmin_chain]
        if missing:
            raise ToulminMissing(
                f"AgentAction.toulmin_chain missing keys: {missing}"
            )


@dataclass
class CompanyAgent:
    """Stateful per-tenant intelligence agent.

    Construct one per tenant. Mutations route through `update_belief` so
    every state change is L2-tagged, L3-citation-capped, L4-auditable,
    and L6-advisor-aware.

    `auto_persist=True` (default) writes the snapshot to
    `data/agents/<tenant>/beliefs.json` after every belief mutation so
    the `GET /api/companies/{slug}/beliefs` endpoint sees live state
    without an explicit dump. Tests can set `auto_persist=False` to keep
    the file system clean during unit runs.

    Phase C addition — 5-state lifecycle (ported from Base Version's
    AccountAgent): `state` field + `transition_to()` method audited via
    `append_decision` with L2 tags. `record_action()` appends an
    `AgentAction` to `actions` log with required Toulmin.
    """
    tenant: str
    audit_dir: Path | None = None
    beliefs: dict[str, Belief] = field(default_factory=dict)
    auto_persist: bool = True
    state: str = STATE_INITIALIZING
    actions: list[AgentAction] = field(default_factory=list)
    lifecycle_started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    last_transition_at: str | None = None

    # ------------------------------------------------------------------
    # Phase C — state machine
    # ------------------------------------------------------------------

    def transition_to(self, new_state: str, *, actor: str, reason: str) -> AgentAction:
        """Move to a new state. Validates the transition + audits it.

        Raises InvalidTransition on an illegal edge.
        Records an AgentAction (action_type='transition') in `self.actions`.
        Emits an `append_decision` entry tagged with L2 schema.
        """
        if new_state not in _VALID_STATES:
            raise InvalidTransition(f"unknown state {new_state!r}")
        allowed = _VALID_TRANSITIONS.get(self.state, frozenset())
        if new_state not in allowed:
            raise InvalidTransition(
                f"illegal transition {self.state} -> {new_state} "
                f"(allowed: {sorted(allowed)})"
            )

        old_state = self.state
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

        is_manual = actor.startswith("manual:")
        toulmin = make_toulmin(
            claim=f"{self.tenant}: {old_state} -> {new_state}",
            grounds=[reason],
            warrant=f"transition triggered by {actor}",
        )
        try:
            append_decision(
                "tier_shift",
                company_slug=self.tenant,
                before={"state": old_state},
                after={"state": new_state},
                toulmin=toulmin,
                automated=not is_manual,
                tags={
                    "scope": "tenant",
                    "signal_type": "analyst_judgment",
                    "attribution": actor,
                    "uncertainty": "low",
                },
                extra={"event": "agent_state_transition"},
                base_data_dir=self.audit_dir,
            )
        except Exception:
            pass

        self.state = new_state
        self.last_transition_at = ts

        action = AgentAction(
            agent_id=self.tenant,
            action_type="transition",
            payload={"from": old_state, "to": new_state, "reason": reason},
            toulmin_chain=toulmin,
            phase_k_tags={
                "scope": "tenant",
                "signal_type": "analyst_judgment",
                "attribution": actor,
                "uncertainty": "low",
            },
            created_at=ts,
        )
        self.actions.append(action)
        if self.auto_persist:
            try:
                self.dump_to_disk()
            except OSError:
                pass
        return action

    def record_action(
        self,
        *,
        action_type: str,
        payload: dict[str, Any],
        toulmin: dict[str, Any],
        actor: str,
        uncertainty: str = "low",
    ) -> AgentAction:
        """Append an action to the action log (not a state transition).

        action_type ∈ {dispatch, recommend, escalate}. Toulmin required.
        """
        if action_type == "transition":
            raise ValueError("use transition_to() for state transitions")
        is_manual = actor.startswith("manual:")
        action = AgentAction(
            agent_id=self.tenant,
            action_type=action_type,
            payload=payload,
            toulmin_chain=toulmin,
            phase_k_tags={
                "scope": "tenant",
                "signal_type": "analyst_judgment",
                "attribution": actor,
                "uncertainty": uncertainty,
            },
        )
        self.actions.append(action)
        try:
            append_decision(
                "tier_shift",
                company_slug=self.tenant,
                before=None,
                after={"action_type": action_type, "payload": payload},
                toulmin=toulmin,
                automated=not is_manual,
                tags={
                    "scope": "tenant",
                    "signal_type": "analyst_judgment",
                    "attribution": actor,
                    "uncertainty": uncertainty,
                },
                extra={"event": "agent_action"},
                base_data_dir=self.audit_dir,
            )
        except Exception:
            pass
        if self.auto_persist:
            try:
                self.dump_to_disk()
            except OSError:
                pass
        return action

    def update_belief(
        self,
        *,
        name: str,
        value: Any,
        confidence: str,
        rationale: str,
        actor: str,
    ) -> None:
        """Update a named belief; audit + advisor-emit if uncertain.

        Args:
            name: stable belief identifier (e.g. "climate_transition_risk")
            value: free-form (string, number, dict — whatever the domain needs)
            confidence: low | moderate | high (NOT "unverified" — those route
                to the advisor queue via L6.route_unverified_to_advisor instead)
            rationale: one-line explanation, lands in Toulmin grounds
            actor: module slug or `manual:<email>` (L2 attribution rule)
        """
        if confidence == "unverified":
            raise ValueError(
                "CompanyAgent.update_belief refuses confidence='unverified'; "
                "use engine.audit.route_unverified_to_advisor instead"
            )

        before = self.beliefs.get(name)
        before_value = before.value if before else None

        # Audit through L2 tags + L3 Toulmin
        is_manual = actor.startswith("manual:")
        toulmin = make_toulmin(
            claim=f"belief({name}) := {value}",
            grounds=[rationale],
            warrant=f"actor={actor}, prior={before_value!r}",
            qualifier="value subject to advisor review" if confidence == "high" else "",
        )
        append_decision(
            "tier_shift",  # closest existing decision_type — belief value moved
            company_slug=self.tenant,
            before=before_value,
            after=value,
            toulmin=toulmin,
            automated=not is_manual,
            tags={
                "scope": "tenant",
                "signal_type": "analyst_judgment",
                "attribution": actor,
                "uncertainty": confidence,
            },
            extra={"belief_name": name},
            base_data_dir=self.audit_dir,
        )

        self.beliefs[name] = Belief(
            name=name,
            value=value,
            confidence=confidence,
            rationale=rationale,
            actor=actor,
        )
        if self.auto_persist:
            try:
                self.dump_to_disk()
            except OSError:
                # Persistence is best-effort; surfacing here would mask
                # a successful audit write. The next mutation will retry.
                pass

    def update_typed_belief(
        self,
        belief: TypedBelief,
        *,
        rationale: str,
        actor: str,
    ) -> None:
        """Type-safe variant of `update_belief`.

        Use this when the belief has a recognised domain shape (see
        `engine.governance.belief_schema`). The value is `belief.to_dict()`
        so it stays JSON-friendly while keeping the discriminator
        (`belief.kind`) inspectable by downstream consumers.

        Validation happens at `belief` construction time (band must be
        in the enum, exposure must be non-negative, etc.) so by the
        time it reaches the audit log it's already been sanitised.
        """
        if not isinstance(belief, TypedBelief):
            raise ValueError(
                f"update_typed_belief requires a TypedBelief subclass, "
                f"got {type(belief).__name__}"
            )
        # Reuse the free-form path so the audit + advisor wiring stays
        # in one place. Belief name = `<kind>:<discriminating-field>` so
        # multiple typed beliefs of the same kind don't collide.
        # FYCascadeSnapshot uses `<fy>:<primitive>` so each (year, primitive)
        # cell gets its own belief slot.
        fy = getattr(belief, "fy", None)
        primitive = getattr(belief, "primitive", None)
        if fy and primitive:
            discriminator = f"{fy}:{primitive}"
        else:
            discriminator = (
                getattr(belief, "topic", None)
                or getattr(belief, "scenario", None)
                or getattr(belief, "framework_id", None)
                or getattr(belief, "painpoint_topic", None)
                or ""
            )
        belief_name = (
            f"{belief.kind}:{discriminator}" if discriminator else belief.kind
        )
        self.update_belief(
            name=belief_name,
            value=belief.to_dict(),
            confidence=belief.confidence_band,
            rationale=rationale,
            actor=actor,
        )

    def revise_from_article(
        self,
        *,
        article: dict[str, Any],
        cascade_result: dict[str, Any] | None = None,
        company_revenue_cr: float = 0.0,
        forecaster_output: dict[str, Any] | None = None,
        apply: bool = False,
        actor: str = "company_agent",
    ) -> list[Any]:
        """Run the belief revision pass for a freshly-ingested article.

        When `apply=False` (default), returns the list of `BeliefProposal`
        objects WITHOUT mutating state — callers can review before
        committing. When `apply=True`, each proposal is applied via
        `update_typed_belief()` so the L4 audit trail records every
        change.

        Pulls recent advisor events for THIS tenant automatically via
        `subscribe_to_advisor_queue()` so the R4 confidence downshift
        rule fires when high-uncertainty events have accumulated.
        """
        # Lazy import to avoid a circular dep at module load
        from engine.governance.belief_revision import revise_from_article as _revise

        advisor_events = list(self.subscribe_to_advisor_queue())
        proposals = _revise(
            article=article,
            cascade_result=cascade_result,
            advisor_events=advisor_events,
            company_revenue_cr=company_revenue_cr,
            forecaster_output=forecaster_output,
        )

        if apply:
            for p in proposals:
                self.update_typed_belief(
                    p.belief,
                    rationale=p.rationale,
                    actor=actor,
                )

        return proposals

    def subscribe_to_advisor_queue(self) -> Iterator[dict[str, Any]]:
        """Read events from L6's advisor queue filtered to this tenant.

        Lazy iteration; reads the on-disk JSONL each call so newly-emitted
        events show up without restarting the agent.
        """
        for ev in read_advisor_queue(base_data_dir=self.audit_dir):
            if ev.get("company_slug") == self.tenant:
                yield ev

    # -------- Persistence (JSON dump/load) --------

    def beliefs_path(self) -> Path:
        """Where this tenant's belief snapshot lives.

        `<audit_dir or repo_root/data>/agents/<tenant>/beliefs.json`.
        Mirrors the layout `data/audit/` uses so cleanup tooling can
        sweep both with one cron.
        """
        if self.audit_dir is not None:
            base = self.audit_dir
        else:
            base = Path(__file__).resolve().parent.parent.parent / "data"
        return base / "agents" / self.tenant / "beliefs.json"

    def dump_to_disk(self) -> Path:
        """Persist current beliefs + 5-state lifecycle to JSON.

        Phase C: lifecycle fields (state, lifecycle_started_at,
        last_transition_at) are now persisted alongside beliefs so
        `load_from_disk` returns an agent in the same state it was
        last in. Older snapshots that pre-date the lifecycle fields
        load cleanly — the loader falls back to STATE_INITIALIZING.
        """
        import json
        path = self.beliefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tenant": self.tenant,
            "beliefs": {
                name: {
                    "name": b.name,
                    "value": b.value,
                    "confidence": b.confidence,
                    "rationale": b.rationale,
                    "actor": b.actor,
                    "updated_at": b.updated_at,
                }
                for name, b in self.beliefs.items()
            },
            # Phase C — lifecycle fields
            "state": self.state,
            "lifecycle_started_at": self.lifecycle_started_at,
            "last_transition_at": self.last_transition_at,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load_from_disk(cls, tenant: str, audit_dir: Path | None = None) -> "CompanyAgent":
        """Rehydrate a CompanyAgent's belief state + 5-state lifecycle from JSON.

        Returns a fresh agent with empty state when no snapshot exists.
        Tolerant of malformed entries (skips them rather than raising).
        Phase C: lifecycle fields rehydrate too — see `dump_to_disk` docstring.
        """
        import json
        agent = cls(tenant=tenant, audit_dir=audit_dir, auto_persist=False)
        path = agent.beliefs_path()
        if not path.exists():
            return agent
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return agent
        beliefs = payload.get("beliefs") or {}
        for name, b in beliefs.items():
            try:
                agent.beliefs[name] = Belief(
                    name=b["name"],
                    value=b["value"],
                    confidence=b["confidence"],
                    rationale=b.get("rationale", ""),
                    actor=b.get("actor", "unknown"),
                    updated_at=b.get("updated_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
            except (KeyError, TypeError):
                continue
        # Phase C — rehydrate lifecycle fields when present, default safely otherwise
        saved_state = payload.get("state")
        if saved_state in _VALID_STATES:
            agent.state = saved_state
        if payload.get("lifecycle_started_at"):
            agent.lifecycle_started_at = payload["lifecycle_started_at"]
        if payload.get("last_transition_at"):
            agent.last_transition_at = payload["last_transition_at"]
        return agent
