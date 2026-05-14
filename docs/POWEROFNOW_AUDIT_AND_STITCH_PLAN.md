# Power of Now — Reconciliation Audit & Stitch Plan

**Generated:** 2026-05-14
**Audit scope:** every divergence between two working copies of Snowkap ESG
**Goal:** absorb the "Power of Now" enhancement work back into the active tree without losing anything from either side

| Side | Path | Most recent .py mtime |
|---|---|---|
| **Current** (the tree you're working in) | `D:\ClaudePowerofnow\snowkap-esg\snowkap-esg\` | 2026-05-11 |
| **Power of Now** (your enhancement work from another desktop) | `D:\ClaudePowerofnow\Power of Now\Power of Now\extracted\ClaudePowerofnow - Copy\snowkap-esg\snowkap-esg\` | 2026-05-13 |

---

## Executive Summary

**Power of Now is "Phase C" work** — a stateful chat + agent system port from a separate Base Version codebase, delivered alongside an autonomous calibration loop ("autoresearcher"), a 3-tier wiki layer, OWL-RL/SHACL ontology validation, and a sentiment-trajectory forecaster. Roughly **10,000 lines of net-new engine code across 8 new subsystems**, plus 9 new API routes, 3 new frontend pages, 4 new ontology assets, and 49 new test files (134 vs 85). The own release notes (`CHANGES_PHASE_C.md` in Power of Now) claim 1,862 tests passing.

**Current is "Phase 24.7" work** — narrower changes: the 17-file regression-fix commit `a326ce7` (1398/1398 tests green), the 5 new `docs/` deliverables I wrote (PRD, intelligence calculations, references-and-framework-tagging, role-blocks, Word doc), and `Base-Version-Adoption-Audit.md` (root). These are not in Power of Now.

**Critical anomaly:** both `CLAUDE.md` files are byte-identical (1,435 lines, same Phase coverage through Phase 26). The Power of Now subsystems are NOT documented in its own `CLAUDE.md` — they're code-only additions tracked in `CHANGES_PHASE_C.md` and `CHANGES_L2_HANDOFF.md`. The phase numbering simply continues from the shared baseline.

**Verdict:** the two trees are **complements, not competitors**. Power of Now is the bigger forward leap (architectural, ~10K LoC, +131 tests). Current has the more recent test-suite hardening and a refreshed doc set. The correct reconciliation is:

1. Absorb Power of Now → current in phased adoption (Phase A–F below, ~3 weeks engineering).
2. Preserve current's `docs/` deliverables + commit `a326ce7` regression fix through the absorption.
3. After absorption, run the Supabase migration runbook (Part 4) — both sides have identical DB scaffolding already, so the actual cutover is a 3-step env change + one migrate command.

The rest of this document is the detailed inventory (Part 1), risk-weighted verdict (Part 2), phased execution plan (Part 3), Supabase runbook (Part 4), and sign-off checklist (Part 5).

---

## Part 1 — Inventory: every divergence, classified

### 1.1 Top-level structure

| Folder | Current | Power of Now | Diff |
|---|---|---|---|
| `api/routes/` | 15 files | 24 files | **+9 routes in PoN** |
| `engine/` (subsystems) | 12 dirs | 20 dirs | **+8 subsystems in PoN** |
| `engine/analysis/` | ~30 files | ~31 files | **+1 in PoN** (`forecaster.py`) |
| `client/src/pages/` | 15 pages | 18 pages | **+3 pages in PoN** |
| `client/src/components/` | 8 folders | 11 folders | **+3 folders in PoN** (`chat`, `charts`, `graphs`) |
| `client/src/hooks/` | (no diff folder) | +1 | **+`useChatStream.ts`** |
| `data/ontology/` | 21 items | 25 items | **+4 ontology assets in PoN** |
| `tests/` | 85 files | 134 files | **+49 test files in PoN** |
| `tests/` subdirs | 0 net-new | 9 net-new dirs | advisor/api/autoresearcher/chat/governance/llm/mcp/memory/wiki |
| `scripts/` | (baseline) | +3 | `build_wiki.py`, `run_autoresearcher.py`, `run_mcp_server.py` |
| `docs/` | **5 unique** | (lacks them) | Current has 5 docs PoN doesn't |
| Root `.md` | Has `Base-Version-Adoption-Audit.md` (PoN lacks) | Has `CHANGES_L2_HANDOFF.md` + `CHANGES_PHASE_C.md` (current lacks) | Bidirectional |
| `wiki/` (top-level) | absent | present | Net-new in PoN |
| `CLAUDE.md` | 1,435 lines, Phase 26 | 1,435 lines, Phase 26 | **Byte-identical** |
| `requirements.txt` | baseline | +`owlrl>=7.1` + `pyshacl>=0.27` | Validation infra |
| `.env.example` | baseline | identical (no net-new env vars listed) | But code references `OPENROUTER_API_KEY` — gap |

### 1.2 Net-new engine subsystems (the big absorption)

All eight live under `engine/` in Power of Now. Read order: **llm → memory → governance → chat → advisor → autoresearcher → mcp → wiki** (low-coupling → high-coupling).

#### B1. `engine/llm/` — Unified LLM gateway (492 LoC, 5 files)

**What it does:** single entry point for every LLM call in the engine. Wraps the OpenAI SDK pointed at OpenRouter (when `OPENROUTER_API_KEY` is set) or direct OpenAI as fallback. Routes by task class (`reasoning_heavy`, `extraction`, `composition`, etc.) to specific models. Cost passthrough reads `usage.cost` directly from OpenRouter responses.

| Module | Purpose |
|---|---|
| `client.py` | `OpenRouterClient` — sync + async + streaming |
| `routing.py` | `TASK_CLASS_TO_MODEL` table + `resolve_model()` |
| `cost.py` | `parse_cost(usage)` — authoritative billed cost |
| `keys.py` | `OPENROUTER_API_KEY` resolution + legacy OpenAI fallback |
| `__init__.py` | Public API |

**Routing table** (from `routing.py`):
| task_class | model |
|---|---|
| `reasoning_heavy` | `anthropic/claude-opus-4.7` |
| `reasoning_default` | `openai/gpt-4.1` |
| `extraction` | `openai/gpt-4.1-mini` |
| `composition` | `openai/gpt-4.1` |
| `classification` | `openai/gpt-4o-mini` |
| `chat` | `openai/gpt-4.1` |
| `search_aided` | `perplexity/sonar-pro` |
| `embeddings` | `openai/text-embedding-3-small` |

**Migration status (from `CHANGES_PHASE_C.md`):** 17 of 18 existing OpenAI call sites migrated to `engine.llm.get_llm_client(task_class=...)`. The single exception is `engine/analysis/batch_processor.py` (3 sites — OpenAI Batch API endpoints OpenRouter doesn't proxy).

**Dependencies:** none upstream. **Foundation for everything else.**

**Env vars added:** `OPENROUTER_API_KEY` (not yet in `.env.example` — gap to close).

**Test count:** 1 test file, 21 individual tests (per release notes).

**Strategic value:** 5/5. Unlocks Claude Opus + Perplexity routing without rewriting the engine. Cost authority via OpenRouter response.

---

#### B2. `engine/memory/` — Tenant + user memory store (487 LoC, 5 files)

**What it does:** persistent facts / preferences / decisions / open threads extracted from conversations by a secondary LLM pass after a conversation ends. Stored in SQLite (`tenant_memory` table); retrieved at chat-time via BM25 over content, filtered to the requesting `(tenant, user)` scope.

| Module | Purpose |
|---|---|
| `schema.py` | `ensure_schema()` — adds `tenant_memory` table |
| `store.py` | `MemoryRecord`, `insert_memory`, `list_memories`, `delete_memory` |
| `extractor.py` | `extract_memories_from_conversation()` — LLM call |
| `retrieval.py` | `retrieve_for_injection()` — BM25 retrieval |
| `__init__.py` | Public API |

**Tables added to `data/snowkap.db`:** `tenant_memory` (per-(tenant, user) BM25-searchable facts).

**Dependencies:** uses `engine.llm` for extraction. **Pure-Python BM25** (no pgvector — explicitly deferred in release notes).

**Test count:** 1 test file, 12 individual tests.

**Strategic value:** 4/5. Required by chat + advisor + governance.

---

#### B3. `engine/governance/` — Probe, belief revision, phase gate, company agent (1,906 LoC, 7 files)

**What it does:** read-only-by-default checks and "prelude gates" that run before any TTL/config mutation. The L0–L7 Base Version adoption layers. The `CompanyAgent` is a 5-state lifecycle machine that tracks per-company belief evolution.

| Module | Purpose |
|---|---|
| `probe.py` | L0 — search 6 sources for prior art before mutation (`decision_log`, `discovery_audit`, `discovery_staging`, `discovered_ttl`, `tenant_painpoints`, `live_sparql`) |
| `belief_revision.py` | L7 rule R5 — forecast-driven risk-band proposal |
| `belief_schema.py` | Typed-belief data classes |
| `llm_belief_refiner.py` | LLM-driven belief refinement pass |
| `company_agent.py` | 5-state CompanyAgent + `AgentAction` |
| `phase_gate.py` | L5 SOP phase-gate state machine |
| `__init__.py` | Public API |

**Dependencies:** `engine.memory` (state persistence) + `engine.llm` (refiner).

**Test count:** 18 test files (largest test surface of all PoN subsystems).

**Strategic value:** 5/5. Quality + audit-discipline backbone. Adopts the entire Base Version L0–L7 adoption from `Base-Version-Adoption-Audit.md`.

---

#### B4. `engine/chat/` — Conversation persistence (807 LoC, 4 files)

**What it does:** SQLite-backed chat conversation + message store. Per-(tenant, user) isolation enforced by filter at every read site (no SQLite RLS). FTS5 virtual table for full-text search; falls back to `LIKE` on builds without FTS5.

| Module | Purpose |
|---|---|
| `schema.py` | `ensure_schema()` — `chat_conversations` + `chat_messages` + `chat_messages_fts` virtual table + 3 triggers |
| `conversations.py` | Conversation CRUD (`ensure_conversation`, `list_conversations`, `rename_conversation`, `delete_conversation`, `fork_conversation`, `archive_conversation`, `search_conversations`) |
| `messages.py` | Message CRUD (`insert_user_message`, `insert_assistant_message`, `load_conversation_history`, `load_messages_for_llm`) |
| `__init__.py` | Public API |

**Tables added to `data/snowkap.db`:** `chat_conversations`, `chat_messages`, `chat_messages_fts` (virtual).

**Dependencies:** `engine.llm` (message generation) + `engine.memory` (memory injection at chat time).

**Test count:** 1 test file, 17 individual tests.

**Strategic value:** 5/5. Foundation for the persistent chat UI page.

---

#### B5. `engine/advisor/` — Multi-coach push-style hints (554 LoC, 10 files)

**What it does:** push-style intelligence surface. Each coach (`data_coach`, `risk_coach`, `forecast_coach`) inspects an `AdvisorEvent` and may emit one or more hints. Hints flow through a suppression engine (dedup + dismissal-tracking + cooldown + global volume cap) before reaching the user. Rendered as SSE `advisor_hint` events on the chat stream, or as cards on the chat page sidebar.

| Module | Purpose |
|---|---|
| `engine.py` | `AdvisorEngine` + `AdvisorHint` dataclass |
| `events.py` | Event taxonomy: `RiskArticleEvent`, `DataIngestEvent`, `ForecastShiftEvent`, `AutoresearcherKeepEvent`, `BeliefRevisionEvent` |
| `suppression.py` | `SuppressionState` — dedup/dismissal/cooldown |
| `personas/data_coach.py` | Data-side hints |
| `personas/risk_coach.py` | Risk-side hints |
| `personas/forecast_coach.py` | Forecast-side hints |

**Dependencies:** `engine.chat` (SSE stream) + `engine.memory` (suppression state) + `engine.governance` (event source).

**Test count:** 2 test files, 14 individual tests.

**Strategic value:** 4/5. Adds reactive intelligence on top of chat. Skippable if chat ships without proactive hints first.

---

#### B6. `engine/autoresearcher/` — Karpathy-style calibration loop (2,978 LoC, 33 files — **biggest subsystem**)

**What it does:** continuously proposes small perturbations to the prediction machinery (ontology weights, scorer components, primitive cascade β, keyword sets, ordinal mappings), replays them against a held-out corpus, and keeps changes that improve a scalar calibration metric.

**Architecture (`__init__.py` docstring):**
- `knobs.py` — `Knob` ABC (apply/revert/bounds/serialise) with blacklist guardrail
- `knob_kinds/` — 10 concrete knob kinds (~555 atomic knobs total)
- `ontology_introspector.py` — auto-discovers all atomic knobs from TTL + scorer
- `metrics.py` — composite calibration metric (F1 + NDCG + audit-clean + advisor-agreement)
- `corpus.py` — held-out article corpus + gold labels from audit logs
- `ledger.py` — append-only JSONL + L2-tagged audit emit
- `experimenter.py` — deterministic seed-stable random walk
- `evaluator.py` — replay loop (snapshot-state, idempotent)
- `loop.py` — outer keep/discard with budget
- `llm_proposer.py` — env-gated LLM proposer (`SNOWKAP_AUTORESEARCHER_LLM_PROPOSER=1`)
- `tier0/` — system-tier (corpus + metric + promoter)
- `tier1/` — tenant-tier (per-company knob promotion via R6 → CompanyAgent)
- `tier2/` — user-tier (PersonaWeight knobs, per-user isolated)

**Dependencies:** `engine.llm` (proposer) + `engine.governance` (CompanyAgent for tier-1 promotion) + `engine.advisor` (queue routing for tier-0).

**Test count:** 11 test files, ~112 individual tests across all 3 tiers (per release notes).

**Strategic value:** 4/5 if you want continuous self-calibration; 2/5 if you ship the engine + manual tuning. Defer-able — the 555 knobs are tunable manually too.

---

#### B7. `engine/mcp/` — MCP server for Snowkap-ESG (1,086 LoC, 13 files)

**What it does:** manifest-driven tool catalog + introspection. Phase 1 ships **introspection + in-process dispatch only** (no stdio/SSE SDK bootstrap — same stance as Base Version's Phase M Wave 4 Task 4.1). Real MCP-protocol stdio/SSE comes later. Today's API surface (`GET /api/mcp/manifest`, `GET /api/mcp/tools`, `POST /api/mcp/invoke`) gives the admin UI everything it needs.

| Module | Purpose |
|---|---|
| `manifest.json` | Tool + resource catalog |
| `server.py` | `MCPServerHandle`, `build_server`, `load_manifest` |
| `resources.py` | 25 resource URIs |
| `chat_integration.py` | `dispatch_tool` for in-chat invocation |
| `tool_metadata.py` | Schemas + annotations |
| `tools/` | 7 tool adapters (named tools that wrap engine functions) |

**Dependencies:** `engine.chat` (in-chat dispatch) + 7 engine subsystems (each tool adapter wires to one).

**Test count:** 4 test files, 33 individual tests.

**Strategic value:** 3/5. Required IF the team wants Claude Code or other MCP clients to call Snowkap tools. Otherwise pure infra.

---

#### B8. `engine/wiki/` — 3-tier markdown wiki layer (1,705 LoC, 8 files)

**What it does:** hierarchical markdown wiki built over the existing ontology + audit + insights + persona data. Pure derivation — every page is rebuildable from `data/inputs/`, `data/outputs/`, `data/audit/`, `data/agents/`, and the ontology TTL files. No new source of truth.

| Tier | Purpose |
|---|---|
| Tier 0 — System core | cross-tenant institutional memory |
| Tier 1 — Tenant | per-company filtering + analysis |
| Tier 2 — User | per-analyst painpoints + history |

| Module | Purpose |
|---|---|
| `system_builder.py` | Tier 0 page generation |
| `tenant_builder.py` | Tier 1 page generation |
| `user_builder.py` | Tier 2 page generation |
| `autoresearcher_pages.py` | Materialises `wiki/system/autoresearcher/{experiments,top-hits,discarded}.md` |
| `index.py` | Wiki index |
| `links.py` | Cross-links between pages |
| `paths.py` | Path conventions |

**Dependencies:** `engine.autoresearcher` (for autoresearcher pages) + ontology + audit + insights. Output directory: top-level `wiki/`.

**Test count:** 7 test files.

**Strategic value:** 3/5. Useful for institutional memory + reproducibility. Not user-facing in the typical sense.

---

#### B9. `engine/analysis/forecaster.py` — Sentiment trajectory forecaster (~400 LoC, 1 file)

**What it does:** OpenAI-native alternative to embedding MiroFish (avoids AGPL + Zep Cloud). Given a company's recent insight history, projects 3 / 6 / 12-month sentiment direction with confidence bands. Module-level cache keyed by `(company_slug, content_hash)` prevents redundant LLM calls.

**Consumers (per docstring):**
- `criticality_scorer` (new `sentiment_trajectory` component)
- `insight_generator` (stamps `sentiment_trajectory` on the insight)
- L7 `belief_revision` rule R5 (forecast-driven risk-band proposal)
- frontend `TrajectoryChart` + `StrategicHorizonPanel`

**Failure modes (all fall back to deterministic baseline):**
- OpenAI API error / timeout
- Malformed JSON response
- Schema-violating LLM output (unknown direction enum)

**Test count:** 1 test file (`tests/test_forecaster.py`).

**Strategic value:** 4/5. Replaces an AGPL dependency + integrates into 4 downstream paths.

---

### 1.3 API routes net-new in Power of Now

9 routes added under `api/routes/`:

| Route file | Purpose | Engine subsystem it calls |
|---|---|---|
| `chat.py` | SSE stream endpoint for chat messages | `engine.chat`, `engine.advisor`, `engine.memory` |
| `conversations.py` | CRUD for chat conversations | `engine.chat` |
| `memory.py` | List / insert / delete memory records | `engine.memory` |
| `mcp_admin.py` | `GET /api/mcp/manifest`, `tools`, `resources`, `POST /invoke` | `engine.mcp` |
| `advisor.py` | List active hints, dismiss | `engine.advisor` |
| `autoresearcher.py` | `GET /experiments`, `/leaderboard`, `POST /run` | `engine.autoresearcher` |
| `beliefs.py` | Read / list typed beliefs per tenant | `engine.governance` |
| `intelligence.py` | Aggregate intelligence endpoint | varies — coordinates multiple |
| `wiki.py` | Read / search / regenerate wiki pages | `engine.wiki` |

All 9 must be wired into `api/main.py` after the engine subsystems land.

### 1.4 Frontend net-new in Power of Now

| Asset | Path | Strategic value |
|---|---|---|
| `AdminAutoresearcherPage.tsx` | `client/src/pages/` | Admin UI for autoresearcher experiments + leaderboard; gated by `manage_drip_campaigns` |
| `AdvisorPage.tsx` | `client/src/pages/` | Advisor hint inbox |
| `PersistentChatPage.tsx` | `client/src/pages/` | The main user-facing chat surface |
| `components/chat/` | folder | `ConversationSidebar.tsx`, `ToulminBadge.tsx`, `ToolInvocationCard.tsx`, `AdvisorHintCard.tsx` |
| `components/charts/` | folder | Forecasting + autoresearcher visualisations |
| `components/graphs/` | folder | Belief / ontology graph rendering |
| `hooks/useChatStream.ts` | hook | SSE consumer for chat streaming |

`client/src/lib/api.ts` has a "Phase-C section" added (conversations, memory, mcp, streamChat methods).

### 1.5 Ontology assets net-new in Power of Now

| Asset | Purpose | Effort to adopt |
|---|---|---|
| `data/ontology/primitives_keywords.ttl` | Lifted from hardcoded `KEYWORD_TO_PRIMITIVE` dict (per Base-Version-Adoption-Audit Proposal #9). Each keyword now a triple. | Drop-in copy |
| `data/ontology/quantitative_mappings.ttl` | 15 ordinal mappings (band → severity, stance → magnitude, priority weights) previously hardcoded. Queryable via 4 new SPARQL functions: `query_band_mapping`, `query_severity_mapping`, `query_stance_magnitude`, `query_priority_weight`. | Drop-in copy + 4 SPARQL functions in `intelligence.py` |
| `data/ontology/shacl/snowkap_core.shacl.ttl` | SHACL shapes for core ontology validation. Powers `engine/ontology/shacl_validator.py`. | Drop-in copy + adopt validator module |
| `data/ontology/competency_questions/cq_*.rq` | 7 SPARQL competency questions: companies, event_types, frameworks, materiality_and_keywords, perspectives, primitives, risk. Used by CI gate. | Drop-in copy + adopt CQ runner |

### 1.6 Test files net-new in Power of Now

**49 test files** across 9 new subdirectories:

| Subdir | Test files | Targets |
|---|---|---|
| `tests/advisor/` | 2 | `engine.advisor` |
| `tests/api/` | 3 | new HTTP routes (chat SSE, conversations, memory) |
| `tests/autoresearcher/` | 11 | `engine.autoresearcher` (all 3 tiers + knobs + ledger) |
| `tests/chat/` | 1 | `engine.chat` (schema + conversations + messages + FTS5) |
| `tests/governance/` | 18 | `engine.governance` (probe, belief, phase_gate, CompanyAgent) — **largest test surface** |
| `tests/llm/` | 1 | `engine.llm` (routing + cost + OpenRouter passthrough) |
| `tests/mcp/` | 4 | `engine.mcp` (manifest + tools + dispatch) |
| `tests/memory/` | 1 | `engine.memory` (BM25 retrieval + extraction) |
| `tests/wiki/` | 7 | `engine.wiki` (3-tier generation) |

Plus `tests/test_forecaster.py` (top-level).

### 1.7 Scripts net-new in Power of Now

| Script | Purpose |
|---|---|
| `scripts/build_wiki.py` | Regenerate all wiki tiers |
| `scripts/run_autoresearcher.py` | Run autoresearcher loop (CLI: `--tier system|tenant|user`) |
| `scripts/run_mcp_server.py` | Smoke MCP server (`--smoke`, `--list-tools`, `--invoke`) |

### 1.8 Dependencies net-new in Power of Now

In `requirements.txt`:
```
owlrl>=7.1
pyshacl>=0.27
```

Used by `engine.ontology.reasoner` (OWL-RL deductive closure) and `engine.ontology.shacl_validator` (SHACL shape validation).

**Env vars referenced in code but NOT in `.env.example`** (gap to close before adopting):
- `OPENROUTER_API_KEY` — required for the unified LLM gateway
- `SNOWKAP_AUTORESEARCHER_LLM_PROPOSER` — `0` (default deterministic) / `1` (LLM-driven proposer)

### 1.9 Documentation net-new in Power of Now

At root:
- `CHANGES_L2_HANDOFF.md` — Base Version L0–L7 adoption release notes (132 KB+)
- `CHANGES_PHASE_C.md` — Phase C stateful agent system release notes (50 KB+, the canonical change log for Power of Now)
- top-level `wiki/` directory — generated outputs

### 1.10 Current-only items (MUST be preserved through the merge)

These exist in current but NOT in Power of Now. They must survive any absorption.

| Asset | Why it must survive |
|---|---|
| `docs/INTELLIGENCE_AND_CALCULATIONS.md` | 570-line explainer (created 2026-05-13) covering pipeline + math + safety nets |
| `docs/REFERENCES_AND_FRAMEWORK_TAGGING.md` | 370-line companion: references / validation / framework tagging |
| `docs/Snowkap_Intelligence_Documentation.docx` | Consolidated Word version of the above two docs |
| `docs/ANALYSIS_BLOCKS_BY_ROLE.md` | Per-role block explanations for CFO / CEO / Analyst |
| `docs/PRD.md` | Product requirements doc |
| `Base-Version-Adoption-Audit.md` (root) | The May 12 audit of Base Version vs current (a different snapshot from Power of Now) |
| Commit `a326ce7` | "fix(tests): repair stale-test cluster — 1398/1398 green from 1346/65f" — 17-file regression fix. Power of Now branched before this commit. |

---

## Part 2 — Reconciliation Verdict

### 2.1 The 30-second answer

Power of Now is the strategic future state. It has eight architectural enhancements (chat, advisor, autoresearcher, governance, llm gateway, memory, mcp, wiki) plus a forecaster, plus SHACL+CQ validation infra. The release notes claim 1862/1862 green tests on its own machine.

Current has the more recent test-suite hygiene work (`a326ce7`) and a refreshed `docs/` set that didn't make it to Power of Now.

The stitch direction is **Power of Now → current**, in phased adoption (Part 3). After absorption, replay current-only commits (`a326ce7` test fixes if they're not already covered + the 5 `docs/` files).

### 2.2 Risk assessment per net-new subsystem

| Subsystem | Coupling | Tables added | External deps | Risk to current | Adoption order |
|---|---|---|---|---|---|
| `engine.llm` | None upstream | None | `OPENROUTER_API_KEY` env var | **Low** — fallback to direct OpenAI preserves behaviour | **B1** (first) |
| `engine.memory` | Uses `llm` | `tenant_memory` | None | **Low** — pure SQLite + Python BM25 | **B2** |
| `engine.governance` | Uses `memory` | varies (belief tables) | None | **Medium** — large surface, 18 test files to validate | **B3** |
| `engine.chat` | Uses `llm` + `memory` | `chat_conversations`, `chat_messages`, FTS5 virtual | None | **Medium** — adds 3 tables + FTS5 (may not exist on all SQLite builds) | **B4** |
| `engine.advisor` | Uses `chat` + `memory` + `governance` | None | None | **Low** — pure logic on top of state | **B5** |
| `engine.autoresearcher` | Uses `llm` + `governance` + `advisor` | None directly (writes JSONL ledger) | Optional LLM proposer env flag | **Medium** — 33 files, 11 test files, biggest LoC | **B6** |
| `engine.mcp` | Uses `chat` + 7 engine subsystems | None | None | **Low** — introspection + dispatch, no transport | **B7** |
| `engine.wiki` | Uses `autoresearcher` + ontology + audit + insights | None (pure markdown output to `wiki/`) | None | **Low** — pure derivation | **B8** |
| `engine.analysis.forecaster` | Uses `llm` | None directly (writes to insight payload) | None | **Low** — fallback to deterministic baseline | drop-in with **B1** |

### 2.3 Anti-patterns to avoid

1. **Don't blind-copy** — Power of Now's `engine/analysis/*` may have already been modified to call `engine.llm.get_llm_client(...)` instead of direct `OpenAI(...)`. Adopting only some subsystems leaves the LLM unified routing half-applied. **Either adopt the full Phase C migration of the 17 call sites OR keep them on direct OpenAI.** The release notes confirm Power of Now did the migration; if you adopt `engine.llm`, you must also accept the call-site changes in existing analysis modules.

2. **Don't lose current's commit `a326ce7`** — verify Power of Now lacks those test fixes before adopting. If Power of Now has the same test-side staleness (likely, since it diverged earlier), apply `a326ce7` again after subsystem absorption.

3. **Don't merge the wikis blindly** — current has `Base-Version-Adoption-Audit.md` and `docs/INTELLIGENCE_AND_CALCULATIONS.md` etc. that PoN doesn't. Both `CHANGES_*` files in PoN should land as `docs/CHANGES_*.md` or in a new `docs/release-notes/` directory rather than at root.

4. **Don't run schema migrations before validating tables don't collide** — Power of Now adds `tenant_memory`, `chat_conversations`, `chat_messages`, `chat_messages_fts`. Current has none of these. No collision risk for the absorption itself, but the FTS5 virtual table needs SQLite build support — gracefully degrade to LIKE per the release notes.

5. **Don't enable autoresearcher LLM proposer by default** — keep `SNOWKAP_AUTORESEARCHER_LLM_PROPOSER=0` in `.env.example`. The deterministic random walk is the production path.

---

## Part 3 — Stitching Plan (phased execution)

Total effort estimate: **~3 weeks** for full feature parity, broken into 6 phases. Each phase produces a green build before moving on.

### Phase 0 — Pre-flight (1 hour)

```bash
# 0.1 Confirm current state is green
cd /d/ClaudePowerofnow/snowkap-esg/snowkap-esg
git status                                   # should be clean
python -m pytest tests/ -q 2>&1 | tail -3   # target: 1398/1398
python scripts/smoke_test.py                  # target: 10/10

# 0.2 Branch + capture baseline
git checkout -b feat/power-of-now-reconciliation
git tag baseline-before-pon-merge

# 0.3 Bring Power of Now in as a side-by-side mount for diffing
# (don't copy yet — we want diff-able view)
export PON='/d/ClaudePowerofnow/Power of Now/Power of Now/extracted/ClaudePowerofnow - Copy/snowkap-esg/snowkap-esg'
ls "$PON" | head    # smoke check

# 0.4 Record the test count + smoke status for the after-comparison
git rev-parse HEAD > /tmp/baseline-commit.txt
```

**Checkpoint:** clean branch, baseline state captured, PoN tree accessible at `$PON`.

---

### Phase A — Drop-in additions (1 day, zero coupling)

These additions don't depend on each other and don't break any existing code path. Land them all in one commit.

```bash
# A.1 Add validation deps
# Edit requirements.txt to add:
#   owlrl>=7.1
#   pyshacl>=0.27
pip install owlrl pyshacl  # local validation

# A.2 Copy net-new ontology assets
cp "$PON/data/ontology/primitives_keywords.ttl"       data/ontology/
cp "$PON/data/ontology/quantitative_mappings.ttl"     data/ontology/
cp -r "$PON/data/ontology/shacl"                      data/ontology/
cp -r "$PON/data/ontology/competency_questions"       data/ontology/

# A.3 Copy forecaster + its test
cp "$PON/engine/analysis/forecaster.py"               engine/analysis/
cp "$PON/tests/test_forecaster.py"                    tests/

# A.4 Verify ontology still loads
python -c "from engine.ontology.intelligence import get_graph; print(len(get_graph()))"
# Expected: > 8200 (current baseline ~8222) — increases because of net-new TTLs

# A.5 Run tests
python -m pytest tests/test_forecaster.py -s
python -m pytest tests/ -q --tb=no 2>&1 | tail -3  # confirm 1398 + 1 pass

# A.6 Commit
git add requirements.txt data/ontology/ engine/analysis/forecaster.py tests/test_forecaster.py
git commit -m "Phase A: drop-in additions (forecaster, ontology validation infra, primitives_keywords)"
```

**Checkpoint:** 1399/1399 tests green; ontology triple count rises; smoke 10/10.

---

### Phase B — Net-new engine subsystems (2 weeks total, ~2 days per subsystem)

Order matters — each subsystem depends on the previous. Each gets its own commit.

#### B1 — `engine/llm/` + 17 call-site migrations (3 days)

```bash
# B1.1 Copy subsystem
cp -r "$PON/engine/llm" engine/
cp -r "$PON/tests/llm"  tests/

# B1.2 Add OPENROUTER_API_KEY to .env.example (use the PoN value if you have one)
# Edit .env.example: add stanza below the OPENAI_API_KEY block

# B1.3 Verify import + fallback path (no OPENROUTER_API_KEY set)
unset OPENROUTER_API_KEY
python -c "from engine.llm import get_llm_client; c = get_llm_client(task_class='extraction'); print(type(c).__name__)"
# Expected: works in fallback mode — byte-equivalent to direct OpenAI

# B1.4 Run new test file
python -m pytest tests/llm/ -s

# B1.5 Migrate the 17 call sites (the hard part)
# Use git diff on $PON vs current for each of:
#   engine/nlp/*.py
#   engine/analysis/insight_generator.py
#   engine/analysis/recommendation_engine.py
#   engine/analysis/ceo_narrative_generator.py
#   engine/analysis/esg_analyst_generator.py
#   engine/analysis/perspective_engine.py
#   engine/output/subject_line.py
#   engine/persona/persona_scorer.py
#   ... etc.
# For each: replace `OpenAI(api_key=...)` with `get_llm_client(task_class="...")`

# B1.6 Run full suite
python -m pytest tests/ -q --tb=line 2>&1 | tail -10
# Target: 1399 + 21 (new llm) + 12 (chat-related stubs may show) = at minimum 1420 green

# B1.7 Commit
git add engine/llm tests/llm .env.example engine/{nlp,analysis,output,persona}
git commit -m "Phase B1: unified LLM gateway + 17 call-site migrations"
```

**Checkpoint:** all OpenAI usage routed through `engine.llm`; OpenRouter env-flag-gated.

#### B2 — `engine/memory/` (1.5 days)

```bash
cp -r "$PON/engine/memory" engine/
cp -r "$PON/tests/memory"  tests/

# Schema bootstrap
python -c "from engine.memory.schema import ensure_schema; ensure_schema()"

python -m pytest tests/memory/ -s
git add engine/memory tests/memory
git commit -m "Phase B2: tenant + user memory store (BM25, SQLite)"
```

**Checkpoint:** `tenant_memory` table exists in `data/snowkap.db`; tests pass.

#### B3 — `engine/governance/` (3 days)

```bash
cp -r "$PON/engine/governance" engine/
cp -r "$PON/tests/governance"  tests/

# Schema bootstrap (belief tables)
python -c "from engine.governance.company_agent import ensure_schema; ensure_schema()"

python -m pytest tests/governance/ -s
git add engine/governance tests/governance
git commit -m "Phase B3: governance (probe, belief revision, phase gate, CompanyAgent L0-L7)"
```

**Checkpoint:** all 18 governance test files green.

#### B4 — `engine/chat/` (2 days)

```bash
cp -r "$PON/engine/chat" engine/
cp -r "$PON/tests/chat"  tests/

# Schema bootstrap (chat tables + FTS5)
python -c "from engine.chat.schema import ensure_schema; ensure_schema()"
# Verify FTS5 available: python -c "import sqlite3; print(sqlite3.connect(':memory:').execute('select fts5_source(?)', ('chat_messages_fts',)).fetchall())"
# If FTS5 absent, code falls back to LIKE per release notes

python -m pytest tests/chat/ -s
git add engine/chat tests/chat
git commit -m "Phase B4: chat persistence (conversations, messages, FTS5)"
```

**Checkpoint:** chat tables exist; FTS5 working (or LIKE fallback exercising).

#### B5 — `engine/advisor/` (1.5 days)

```bash
cp -r "$PON/engine/advisor" engine/
cp -r "$PON/tests/advisor"  tests/

python -m pytest tests/advisor/ -s
git add engine/advisor tests/advisor
git commit -m "Phase B5: multi-coach advisor + suppression engine"
```

#### B6 — `engine/autoresearcher/` (3 days — biggest)

```bash
cp -r "$PON/engine/autoresearcher" engine/
cp -r "$PON/tests/autoresearcher"  tests/
cp "$PON/scripts/run_autoresearcher.py" scripts/

# CLI smoke
python scripts/run_autoresearcher.py --tier system --dry-run

# Full test suite
python -m pytest tests/autoresearcher/ -s

git add engine/autoresearcher tests/autoresearcher scripts/run_autoresearcher.py
git commit -m "Phase B6: Karpathy autoresearcher (Tier 0 + 1 + 2, 555 knobs)"
```

#### B7 — `engine/mcp/` (1.5 days)

```bash
cp -r "$PON/engine/mcp" engine/
cp -r "$PON/tests/mcp"  tests/
cp "$PON/scripts/run_mcp_server.py" scripts/

# Smoke
python scripts/run_mcp_server.py --smoke

python -m pytest tests/mcp/ -s
git add engine/mcp tests/mcp scripts/run_mcp_server.py
git commit -m "Phase B7: MCP server (introspection + in-process dispatch, 14 tools)"
```

#### B8 — `engine/wiki/` (1.5 days)

```bash
cp -r "$PON/engine/wiki" engine/
cp -r "$PON/tests/wiki"  tests/
cp "$PON/scripts/build_wiki.py" scripts/

# Build all 3 tiers
python scripts/build_wiki.py

python -m pytest tests/wiki/ -s
git add engine/wiki tests/wiki scripts/build_wiki.py wiki/
git commit -m "Phase B8: 3-tier wiki layer (system + tenant + user)"
```

**Checkpoint after Phase B:** full test suite should be ~1398 + 49 ≈ 1447 green (matches Power of Now's claimed 1862 minus the gap I can't verify without running it).

---

### Phase C — API route wiring (2 days)

```bash
# C.1 Copy all 9 new routes
for f in advisor.py autoresearcher.py beliefs.py chat.py conversations.py intelligence.py mcp_admin.py memory.py wiki.py; do
  cp "$PON/api/routes/$f" api/routes/
done

# C.2 Copy the 3 API tests
cp -r "$PON/tests/api" tests/

# C.3 Update api/main.py — register the routers
# Diff $PON/api/main.py against current to see exact include_router calls

# C.4 Smoke-test each new endpoint
python -m uvicorn api.main:app --port 8001 &
sleep 3
for path in /api/chat/health /api/conversations /api/memory /api/mcp/manifest /api/advisor /api/autoresearcher/experiments /api/beliefs /api/intelligence /api/wiki; do
  echo -n "$path → "
  curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8001$path" -H "X-API-Key: $SNOWKAP_API_KEY"
done
kill %1

# C.5 Test
python -m pytest tests/api/ -s
git add api/routes api/main.py tests/api
git commit -m "Phase C: wire 9 new API routes + 3 API test files"
```

**Checkpoint:** all 9 new routes return 200 (or 401 if auth needed) on smoke.

---

### Phase D — Frontend (2 days)

```bash
# D.1 Copy 3 new pages
cp "$PON/client/src/pages/AdminAutoresearcherPage.tsx" client/src/pages/
cp "$PON/client/src/pages/AdvisorPage.tsx"             client/src/pages/
cp "$PON/client/src/pages/PersistentChatPage.tsx"      client/src/pages/

# D.2 Copy 3 new component folders
cp -r "$PON/client/src/components/chat"   client/src/components/
cp -r "$PON/client/src/components/charts" client/src/components/
cp -r "$PON/client/src/components/graphs" client/src/components/

# D.3 Copy the SSE hook
cp "$PON/client/src/hooks/useChatStream.ts" client/src/hooks/

# D.4 Diff + merge lib/api.ts (Phase-C section)
# Open $PON/client/src/lib/api.ts and current/client/src/lib/api.ts side-by-side
# Append the Phase-C exports (conversations, memory, mcp, streamChat)

# D.5 Update App.tsx routing
# Diff $PON/client/src/App.tsx vs current — add routes for /chat, /advisor, /settings/autoresearcher

# D.6 Build + lint
cd client
npm install                  # in case anything new in package.json
npm run lint
npm run build
cd ..

git add client/
git commit -m "Phase D: frontend (chat / advisor / autoresearcher admin pages + 3 component folders)"
```

**Checkpoint:** `npm run build` succeeds; new pages render in browser (manual click-test).

---

### Phase E — Replay current-only commits (0.5 day)

```bash
# E.1 Cherry-pick the regression-fix commit if Power of Now lacks it
# Power of Now branched on May 13 — check if its tests/conftest.py has the
# SNOWKAP_DB_BACKEND=sqlite forcing line:
grep -c "SNOWKAP_DB_BACKEND.*sqlite" "$PON/tests/conftest.py" || echo "PoN lacks the conftest fix"

# If lacking, current's commit a326ce7 is already on this branch (it was at HEAD before Phase 0),
# so it's still there. Verify:
git log --oneline | grep "fix(tests): repair stale-test cluster" && echo "kept" || echo "MISSING — reapply"

# E.2 Confirm docs/ deliverables survived (they should — they weren't touched)
ls docs/INTELLIGENCE_AND_CALCULATIONS.md docs/REFERENCES_AND_FRAMEWORK_TAGGING.md \
   docs/Snowkap_Intelligence_Documentation.docx docs/ANALYSIS_BLOCKS_BY_ROLE.md docs/PRD.md

# E.3 Copy Power of Now's CHANGES_* docs into docs/ (not root, to avoid root clutter)
mkdir -p docs/release-notes
cp "$PON/CHANGES_PHASE_C.md"      docs/release-notes/
cp "$PON/CHANGES_L2_HANDOFF.md"   docs/release-notes/

git add docs/release-notes
git commit -m "Phase E: relocate Power of Now CHANGES_* to docs/release-notes/"
```

**Checkpoint:** all current-only items present; PoN's release notes filed under `docs/release-notes/`.

---

### Phase F — Final validation (0.5 day)

```bash
# F.1 Full pytest sweep
python -m pytest tests/ -q --tb=line 2>&1 | tail -10
# Target: 1398 baseline + 49 new test files (estimated ~131 new individual tests) = ~1529 green

# F.2 Smoke test
python scripts/smoke_test.py
# Target: 10/10 pass

# F.3 Fuzz harness
python scripts/fuzz_pipeline.py
# Target: ≥ 8/10 pass

# F.4 MCP smoke
python scripts/run_mcp_server.py --smoke

# F.5 Wiki regenerate
python scripts/build_wiki.py
ls wiki/system wiki/tenants wiki/users  # confirm all 3 tiers materialised

# F.6 Frontend manual click-through
cd client && npm run dev &
# Browser → http://localhost:5173
#   - Click "Chat" → new conversation works
#   - Send a message → SSE stream visible
#   - Open "Advisor" → empty state renders
#   - Open "Settings → Autoresearcher" → empty experiments list
#   - Pages render without console errors

# F.7 Final commit
git log --oneline | head -15
git commit --allow-empty -m "feat: reconcile Power of Now enhancements (Phase A-F complete)

Subsystems absorbed (8): llm, memory, governance, chat, advisor,
autoresearcher, mcp, wiki. Plus engine/analysis/forecaster.py.

Routes added (9): chat, conversations, memory, mcp_admin, advisor,
autoresearcher, beliefs, intelligence, wiki.

Pages added (3): PersistentChatPage, AdvisorPage, AdminAutoresearcherPage.

Ontology validation: SHACL + competency_questions + owlrl + pyshacl.

Net-new tests: 49 test files, ~131 individual tests (estimated).

Current-only preserved: a326ce7 regression fix, docs/* set, CHANGES_*
relocated to docs/release-notes/."
```

**Checkpoint:** branch ready for merge to master.

---

## Part 4 — Database on Supabase Runbook

Both versions have **byte-identical** database scaffolding (`engine/db/connection.py`, `engine/db/dialect.py`, `engine/db/migrate.py`, `engine/db/migrations/001_initial.sql`, `engine/db/migrations/002_pinned_until.sql`, 10-table schema). This runbook applies regardless of whether you cut over before or after the Power of Now absorption.

> **One caveat:** if you cut over AFTER Phase B above, the schema includes the net-new tables from `engine.memory.schema`, `engine.chat.schema`, and `engine.governance.company_agent` (memory, chat_conversations, chat_messages, FTS5 mirror, beliefs). Those subsystems call `ensure_schema()` on first import; you need them to run AT LEAST ONCE against the Supabase DB before traffic.

### 4.1 Pre-migration backup (5 min)

```bash
# 4.1.1 Stop the API to freeze the SQLite file
# (kill any running uvicorn / python process)
ps aux | grep "uvicorn api.main"
kill <pid>

# 4.1.2 Backup the SQLite file
cp data/snowkap.db data/snowkap.db.pre-supabase-$(date +%Y%m%d_%H%M%S).bak

# 4.1.3 Note row counts for post-migration sanity-check
sqlite3 data/snowkap.db <<EOF
SELECT 'article_index', COUNT(*) FROM article_index;
SELECT 'tenant_registry', COUNT(*) FROM tenant_registry;
SELECT 'campaigns', COUNT(*) FROM campaigns;
SELECT 'llm_calls', COUNT(*) FROM llm_calls;
SELECT 'onboarding_status', COUNT(*) FROM onboarding_status;
SELECT 'outbound_touches', COUNT(*) FROM outbound_touches;
EOF
```

Save the output — you'll cross-check it after migration.

### 4.2 Supabase project setup (one-time, 10 min)

1. Go to https://supabase.com and create a project. Pick a region close to your hosting (e.g. `ap-south-1` for Mumbai, `eu-west-1` for EU).
2. While the DB provisions, capture three things from the dashboard:
   - **Project ref** — looks like `gvlnlgvynxktrgxnyarb` (URL slug)
   - **DB password** — set during project creation
   - **Region** — e.g. `ap-southeast-1`
3. Once provisioned: **Settings → Database → Connection string → URI** → copy. It looks like:
   ```
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```
   The `:6543` is the **transaction pooler** (pgbouncer) — that's the right port for the app's connection pattern.
4. **Settings → Database → Connection pooling** → confirm pgbouncer is enabled in transaction mode (default).
5. **Settings → API → Project URL** — note this if you plan to use Supabase Auth or Storage later (not needed for the SQL migration).

### 4.3 Environment configuration

In your production environment (Replit Secrets, Railway env vars, or wherever — **not in committed `.env`**):

```bash
SNOWKAP_DB_BACKEND=postgres
SUPABASE_DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
SNOWKAP_PG_STATEMENT_TIMEOUT_MS=300000   # 5 min, bumps the Supabase default 60s
```

For local testing against Supabase (don't do this on a developer machine without intent — it points everyone's `engine.db.connect()` at the live DB):

```bash
export SNOWKAP_DB_BACKEND=postgres
export SUPABASE_DATABASE_URL='postgresql://...'   # use single quotes — password may contain $
export SNOWKAP_PG_STATEMENT_TIMEOUT_MS=300000
```

### 4.4 Apply schema

```bash
# 4.4.1 Run the migration runner — applies 001_initial.sql + 002_pinned_until.sql
SNOWKAP_DB_BACKEND=postgres \
SUPABASE_DATABASE_URL='postgresql://...' \
python -m engine.db.migrate

# Expected output:
# Active backend: postgres
# Applying 001_initial.sql...
#   ✓ 001_initial.sql
# Applying 002_pinned_until.sql...
#   ✓ 002_pinned_until.sql
# Applied 2 migration(s).

# 4.4.2 If you've absorbed Power of Now, also bootstrap the Phase C tables
python -c "from engine.memory.schema     import ensure_schema; ensure_schema()"
python -c "from engine.chat.schema       import ensure_schema; ensure_schema()"
python -c "from engine.governance.company_agent import ensure_schema; ensure_schema()"
```

If a migration errors mid-way: read the error, fix it (usually a Postgres dialect translation issue), re-run. The migration runner is idempotent — `CREATE TABLE IF NOT EXISTS` everywhere.

### 4.5 Smoke test against Supabase

```bash
SNOWKAP_DB_BACKEND=postgres \
SUPABASE_DATABASE_URL='postgresql://...' \
python scripts/smoke_test.py
```

Expected: **9/10 pass**. The expected fail is "SQLite article_index has > 0 rows" — that's correct because Supabase is empty. Either backfill data (4.6) or accept the fail until first ingest fills the table.

If you get other fails:
- "no such table" → migration didn't run cleanly; re-run 4.4
- "permission denied" → Supabase user lacks privileges; check the connection string is the `postgres` user from the URI
- connection timeout → wrong port (must be `:6543` for pooler) or wrong region in URL

### 4.6 (Optional) Backfill existing SQLite data

If you want to keep your existing articles + tenants:

```bash
# A backfill script doesn't exist yet — quick adapter:
cat > scripts/sqlite_to_supabase_backfill.py <<'PY'
"""One-shot: copy every row from local SQLite to the active Postgres backend."""
import os, sqlite3
from engine.db import connect as pg_connect
from engine.config import get_data_path

SRC = sqlite3.connect(str(get_data_path("snowkap.db")))
SRC.row_factory = sqlite3.Row

TABLES = [
    "article_index", "slug_aliases", "tenant_registry",
    "article_analysis_status", "campaigns", "campaign_recipients",
    "llm_calls", "onboarding_status", "auth_otp", "outbound_touches",
    # Add Phase C tables if you absorbed them:
    # "tenant_memory", "chat_conversations", "chat_messages",
]

for tbl in TABLES:
    try:
        rows = SRC.execute(f"SELECT * FROM {tbl}").fetchall()
    except sqlite3.OperationalError:
        print(f"{tbl}: not in local DB, skipping")
        continue
    if not rows:
        print(f"{tbl}: empty, skipping")
        continue
    cols = rows[0].keys()
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO {tbl} ({', '.join(cols)}) VALUES ({placeholders})"
    inserted = 0
    with pg_connect() as conn:
        for r in rows:
            try:
                conn.execute(sql, tuple(r))
                inserted += 1
            except Exception as e:
                print(f"  {tbl}: row failed: {e}")
    print(f"{tbl}: {inserted}/{len(rows)} rows copied")
PY

# Run with the same env vars set
SNOWKAP_DB_BACKEND=postgres \
SUPABASE_DATABASE_URL='postgresql://...' \
python scripts/sqlite_to_supabase_backfill.py
```

After backfill, verify counts:

```bash
psql 'postgresql://...' -c "
  SELECT 'article_index', COUNT(*) FROM article_index
  UNION ALL SELECT 'tenant_registry', COUNT(*) FROM tenant_registry
  UNION ALL SELECT 'campaigns', COUNT(*) FROM campaigns;
"
```

Cross-check against the pre-migration counts from 4.1.3.

### 4.7 Cut over the live API

```bash
# Set the env vars in your production environment
# (Replit: Secrets pane; Railway: Variables; etc.)
# DO NOT commit them to .env — keep secrets out of git.

# Restart the API
systemctl restart snowkap-api          # or `pm2 restart snowkap` or platform-specific

# Tail the logs for first 60 seconds
tail -f /var/log/snowkap-api.log
# Watch for:
#   ✓ "Active backend: postgres" — DB layer initialised
#   ✗ "database is locked" — wrong backend (should never appear on Postgres)
#   ✗ "no such table" — schema not migrated; re-run 4.4
#   ✗ "connection refused" — wrong URL / port / region
```

### 4.8 Rollback procedure

If anything breaks within the first 10 minutes:

```bash
# 4.8.1 Revert env vars (unset SNOWKAP_DB_BACKEND or set to sqlite)
# In your hosting platform: edit env vars, remove SNOWKAP_DB_BACKEND (defaults to sqlite)
# Or set: SNOWKAP_DB_BACKEND=sqlite

# 4.8.2 Restart
systemctl restart snowkap-api

# 4.8.3 Verify
curl -s -o /dev/null -w "%{http_code}\n" http://your-host/health  # expect 200
```

Supabase data stays intact — re-attempt later. SQLite resumes serving from the pre-migration `data/snowkap.db`.

### 4.9 Post-cutover monitoring (24 hours)

Watch these:

1. **`/metrics` Prometheus endpoint** — `snowkap_articles_total` should keep increasing on ingest cron.
2. **Logs for slow-request warnings** — any request > 500ms logs a warning. With Supabase pgbouncer, expect 80-200ms p50; > 1s p95 indicates network or query plan issues.
3. **`/api/news/feed?company_id=adani-power`** — spot-check 3 companies; rows should match Supabase Table Editor view.
4. **Trigger one fresh article ingest** — verify writes hit Supabase:
   ```bash
   curl -X POST http://your-host/api/ingest/adani-power -H "X-API-Key: $SNOWKAP_API_KEY"
   # then in Supabase SQL Editor:
   SELECT id, title, written_at FROM article_index ORDER BY written_at DESC LIMIT 3;
   ```

### 4.10 What's NOT wired (honest disclosure)

These are limitations of the current architecture — not blockers, but you should know:

1. **No async Postgres driver.** `asyncpg` is in `requirements.txt` but `engine.db.connect()` uses sync `psycopg2`. The API is async (FastAPI) but every DB call goes through a thread pool. This works fine up to ~100 concurrent requests; beyond that, the thread pool can saturate.

2. **No connection-pool circuit breaker at the app level.** Supabase pgbouncer handles pooling, but if `max_connections` is hit at the Supabase side, the app hangs rather than 503-ing. Mitigation: monitor `pgbouncer.max_client_conn` in Supabase dashboard; alert on > 80% utilisation.

3. **Zero integration tests run against Postgres.** `tests/conftest.py` forces `SNOWKAP_DB_BACKEND=sqlite` for the test suite. To validate Postgres-specific dialect translations end-to-end, you'd need a separate Supabase staging project + a CI workflow that switches the backend. Currently this is dev-only sanity testing.

4. **No automatic schema diff check.** If you run `engine.db.migrate` against an already-migrated Supabase, it re-applies all `CREATE TABLE IF NOT EXISTS` statements — they're idempotent, no harm. But if you add a column in code without a migration file, Supabase won't pick it up. Discipline: every schema change goes through `engine/db/migrations/*.sql`.

5. **Supabase Row Level Security (RLS) is not configured.** The app enforces tenant isolation by filter (`WHERE company_id = ?` everywhere). If someone bypasses the app and queries Supabase directly with the service-role key, they see everything. To harden: enable RLS in Supabase Dashboard → SQL Editor and add policies per table. This is in scope for a Phase 27 "tenant isolation hardening" if needed.

---

## Part 5 — Sign-off checklist

After the full Phase A–F + Supabase cutover, every box below should be ticked:

### Code

- [ ] Phase A: forecaster + 2 ontology TTLs + SHACL + competency_questions copied
- [ ] Phase B1: `engine/llm/` + 17 call-site migrations
- [ ] Phase B2: `engine/memory/` + table schema
- [ ] Phase B3: `engine/governance/` + belief tables
- [ ] Phase B4: `engine/chat/` + 3 chat tables + FTS5
- [ ] Phase B5: `engine/advisor/`
- [ ] Phase B6: `engine/autoresearcher/` (Tier 0/1/2)
- [ ] Phase B7: `engine/mcp/`
- [ ] Phase B8: `engine/wiki/`
- [ ] Phase C: all 9 new API routes registered + return 200 on smoke
- [ ] Phase D: 3 new pages render + 3 new component folders copied
- [ ] Phase E: `a326ce7` regression fix preserved + 5 `docs/*` files preserved + `CHANGES_*` relocated to `docs/release-notes/`

### Tests

- [ ] Full pytest green (target: ~1447–1530 depending on Phase C migration count)
- [ ] Smoke test 10/10
- [ ] Fuzz harness ≥ 8/10
- [ ] MCP server `--smoke` clean
- [ ] Wiki build runs (`scripts/build_wiki.py` populates `wiki/`)
- [ ] Frontend `npm run build` succeeds with zero ESLint errors

### Database

- [ ] Supabase project created in correct region
- [ ] Migrations 001 + 002 applied (Postgres backend)
- [ ] Phase C `ensure_schema()` run once for memory + chat + governance
- [ ] `python scripts/smoke_test.py` against Supabase returns 9/10 (or 10/10 after backfill)
- [ ] Backfill script ported existing rows (if you opted in)
- [ ] Production env vars set: `SNOWKAP_DB_BACKEND=postgres`, `SUPABASE_DATABASE_URL`, `SNOWKAP_PG_STATEMENT_TIMEOUT_MS=300000`
- [ ] First fresh ingest writes to Supabase (verified via SQL Editor)
- [ ] 24-hour post-cutover monitoring complete with no rollback

### Operational

- [ ] OPENROUTER_API_KEY documented in `.env.example` (and `.env.production.example`)
- [ ] SNOWKAP_AUTORESEARCHER_LLM_PROPOSER default = `0` (deterministic)
- [ ] CTA cadence + outbound_touches tracker unaffected by absorption
- [ ] Cron jobs unchanged (hourly ingest, 30-min promote, hourly backup)
- [ ] Sentry DSN configured (optional but recommended for production)

---

## Open questions / known gaps in this audit

1. **Power of Now's `engine/analysis/*` migration to `engine.llm`** — verified at the release-notes level (17 of 18 call sites migrated) but NOT spot-checked file-by-file. Phase B1 needs a diff pass to capture each migrated call site.

2. **Possible schema collisions** — the Power of Now `engine/governance/company_agent.py` introduces typed-belief tables; their names aren't in current's schema, but if any name happens to match an existing table the schema bootstrap will fail. Run a `.schema` check against `data/snowkap.db` post-Phase-B3.

3. **`Base-Version-Adoption-Audit.md` already proposed adopting Base Version's L0–L7 layers** — Power of Now is essentially the implementation of that audit's recommendations. After the absorption, that root document is functionally superseded and should be moved to `docs/historical/` or archived.

4. **The `wiki/` directory at the top-level** — adopting it adds ~MB of generated markdown to the repo. Consider `.gitignore`-ing `wiki/` and regenerating via cron / on-deploy. Otherwise rebuild cycles add noise to git history.

5. **`tests/conftest.py` in Power of Now may have different fixtures** (e.g. `_wipe_chat_memory` autouse fixture mentioned in release notes). Phase B4 needs to merge — not overwrite — `conftest.py`.

6. **OPENROUTER_API_KEY** — release notes say "byte-for-byte equivalent when not set" but the call-site changes ARE permanent. If you don't set the env var, you stay on direct OpenAI but the code path is different. Plan: ship Phase B1 with `OPENROUTER_API_KEY` unset in `.env.example`, document in deployment runbook that setting it unlocks Claude Opus / Perplexity routing.

---

*End of audit. Estimated execution time after sign-off: 3 weeks engineering for full Phase A–F + 1 day for Supabase migration. Total: ~16 working days.*
