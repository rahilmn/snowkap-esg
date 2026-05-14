"""Auto-discovers all atomic knobs in the live ontology + scorer.

Walks each tunable predicate / dict / TTL block and produces a fully
populated Knob registry. The autoresearcher's experimenter samples
from this registry.

Skips blacklisted kinds (band thresholds, mandatory toggles, fallback
toggles) per the load-bearing-safety guardrail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from engine.autoresearcher.knobs import BLACKLIST, Knob, is_blacklisted
from engine.autoresearcher.knob_kinds.keyword_set import (
    KeywordSetKnob,
    KeywordSetState,
)
from engine.autoresearcher.knob_kinds.ontology_weight import (
    OntologyWeightKnob,
    OntologyWeightState,
)
from engine.autoresearcher.knob_kinds.ordinal_mapping import (
    OrdinalMappingKnob,
    OrdinalMappingState,
)
from engine.autoresearcher.knob_kinds.primitive_beta import (
    PrimitiveBetaKnob,
    PrimitiveBetaState,
)
from engine.autoresearcher.knob_kinds.scorer_component import (
    ScorerComponentKnob,
    ScorerWeightState,
)


@dataclass
class KnobRegistry:
    """Holds all discovered knobs + the per-kind state instances they
    share. The evaluator passes the states through to the engine
    during replay."""
    knobs: list[Knob] = field(default_factory=list)
    ordinal_state: OrdinalMappingState = field(default_factory=lambda: OrdinalMappingState(values={}))
    ontology_state: OntologyWeightState = field(default_factory=OntologyWeightState)
    scorer_state: ScorerWeightState = field(default_factory=ScorerWeightState)
    keyword_state: KeywordSetState = field(default_factory=KeywordSetState)
    primitive_beta_state: PrimitiveBetaState = field(default_factory=PrimitiveBetaState)

    def by_kind(self, kind: str) -> list[Knob]:
        return [k for k in self.knobs if k.kind == kind]

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for k in self.knobs:
            out[k.kind] = out.get(k.kind, 0) + 1
        out["__total__"] = len(self.knobs)
        return out


def _discover_ordinal_mappings(registry: KnobRegistry) -> None:
    """One OrdinalMappingKnob per (category, label) loaded from the TTL."""
    registry.ordinal_state = OrdinalMappingState.from_query_all()
    for (cat, label), _val in registry.ordinal_state.values.items():
        try:
            registry.knobs.append(OrdinalMappingKnob(
                category=cat, label=label, state=registry.ordinal_state,
            ))
        except Exception:
            continue


def _discover_ontology_weights(registry: KnobRegistry) -> None:
    """Read all `materialityWeight`, `hasRiskWeight`, and `boostValue`
    triples from the ontology and produce one knob per triple."""
    try:
        from engine.ontology.graph import OntologyGraph
        g = OntologyGraph()
        g.load()
    except Exception:
        return

    weight_predicates = [
        ("materialFor", "snowkap:materialityWeight"),
        ("hasRiskWeight", "snowkap:hasRiskWeight"),
        ("boostsFramework", "snowkap:boostValue"),
    ]
    for pred_slug, _ttl_predicate in weight_predicates:
        sparql = """
        SELECT ?subj ?obj ?val WHERE {
            ?node snowkap:materialityWeight ?val .
            ?node rdf:subject ?subj .
            ?node rdf:object ?obj .
        }
        """ if pred_slug == "materialFor" else None
        # NB: only materialityWeight queryable via reification; the
        # other weights are inline on the subject. Skip with best-effort
        if sparql is None:
            continue
        try:
            rows = g.select_rows(sparql)
        except Exception:
            continue
        for row in rows:
            subj = str(row.get("subj", ""))
            obj = str(row.get("obj", ""))
            try:
                val = float(row["val"])
            except (KeyError, TypeError, ValueError):
                continue
            # Strip the URI prefix for cleaner ids
            subj_short = subj.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
            obj_short = obj.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
            registry.ontology_state.set(pred_slug, subj_short, obj_short, val)
            try:
                registry.knobs.append(OntologyWeightKnob(
                    predicate=pred_slug, subj=subj_short, obj=obj_short,
                    state=registry.ontology_state,
                ))
            except Exception:
                continue


def _discover_scorer_components(registry: KnobRegistry) -> None:
    """One ScorerComponentKnob per (role, component) pair in the scorer."""
    registry.scorer_state = ScorerWeightState.from_scorer_module()
    for (role, component), _v in registry.scorer_state.values.items():
        try:
            registry.knobs.append(ScorerComponentKnob(
                role=role, component=component, state=registry.scorer_state,
            ))
        except Exception:
            continue


def _discover_keyword_sets(registry: KnobRegistry) -> None:
    """One KeywordSetKnob per (event_type, keyword) pair currently
    in the ontology, in BOTH 'add' and 'remove' actions.

    Practical cardinality: ~22 event types × ~10 keywords each × 2
    actions ≈ 440 keyword knobs. We cap to the most-common keywords
    per type (top 10) to keep the search space tractable.
    """
    try:
        from engine.ontology.graph import OntologyGraph
        g = OntologyGraph()
        g.load()
    except Exception:
        return

    sparql = """
    SELECT ?event ?kw WHERE {
        ?event a snowkap:EventType .
        ?event snowkap:hasKeyword ?kw .
    }
    """
    try:
        rows = g.select_rows(sparql)
    except Exception:
        return

    # Group by event_type
    by_event: dict[str, list[str]] = {}
    for row in rows:
        event_uri = str(row.get("event", ""))
        kw = str(row.get("kw", ""))
        if not event_uri or not kw:
            continue
        event_short = event_uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        by_event.setdefault(event_short, []).append(kw)

    for event_type, keywords in by_event.items():
        registry.keyword_state.set(event_type, frozenset(keywords))
        # One knob per (event_type, keyword, action) for the first 10 keywords
        for kw in keywords[:10]:
            for action in ("add", "remove"):
                try:
                    registry.knobs.append(KeywordSetKnob(
                        event_type=event_type, keyword=kw,
                        action=action, state=registry.keyword_state,  # type: ignore[arg-type]
                    ))
                except Exception:
                    continue


def _discover_primitive_betas(registry: KnobRegistry) -> None:
    """One PrimitiveBetaKnob per `elasticityOrWeight` triple."""
    try:
        from engine.ontology.graph import OntologyGraph
        g = OntologyGraph()
        g.load()
    except Exception:
        return

    sparql = """
    SELECT ?edge ?val WHERE {
        ?edge snowkap:elasticityOrWeight ?val .
    }
    """
    try:
        rows = g.select_rows(sparql)
    except Exception:
        return

    for row in rows:
        edge_uri = str(row.get("edge", ""))
        try:
            val = float(row["val"])
        except (KeyError, TypeError, ValueError):
            continue
        edge_short = edge_uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        registry.primitive_beta_state.set(edge_short, val)
        try:
            registry.knobs.append(PrimitiveBetaKnob(
                edge_id=edge_short, state=registry.primitive_beta_state,
            ))
        except Exception:
            continue


def discover_all_knobs() -> KnobRegistry:
    """Walks every tunable surface and returns a populated registry.

    Best-effort: any individual discovery sub-step that fails (e.g.
    ontology not loaded yet on a fresh checkout) returns gracefully
    so the registry still contains whatever was reachable.
    """
    registry = KnobRegistry()

    _discover_ordinal_mappings(registry)
    _discover_ontology_weights(registry)
    _discover_scorer_components(registry)
    _discover_keyword_sets(registry)
    _discover_primitive_betas(registry)

    # Final safety filter — no blacklisted knobs survive even if a
    # discovery routine produced one
    registry.knobs = [
        k for k in registry.knobs
        if not is_blacklisted(kind=k.kind, knob_id=k.knob_id)
    ]
    return registry
