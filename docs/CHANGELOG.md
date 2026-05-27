# Snowkap Engine — Changelog

Phase-by-phase log of architectural changes. Read this for history;
read [`CLAUDE.md`](../CLAUDE.md) for current state.

---

## Phase 46 — Clean rebuild (2026-05-28)

The validation loop that bit through Phases 38-45 was caused by an
accreted architecture: 3 onboarding paths, 5 schema versions, 12+
silent try/except blocks, and a tier gate that left SECONDARY-tier
articles half-analysed. Phase 46 collapses all of that into one
coherent flow.

### Decisions locked

- **Onboarding**: domain-only. LLM resolver infers painpoints + KPIs + role.
- **Rec quality gate**: 4 fields required (peer + framework + ₹ budget + audit_trail).
- **Delivery**: both email + in-app deck.
- **Role tabs**: killed. JWT role drives one framing per article.
- **Chat, forum, wiki, drip**: kept.

### Sub-phases shipped

| # | Title | Notes |
|---|---|---|
| 46.A | LLM resolver returns painpoints + KPIs + default reader role | 5-7 painpoints, 3-5 KPIs, role from 5-canonical-set. Industry × region fallback defaults guarantee non-empty signals. |
| 46.B | Hard quality gate on every recommendation | `enforce_quality_gate` drops recs missing any of: named peer, real framework section, ₹ budget + payback months, ≥2 audit_trail entries. |
| 46.C | (planned) Role tabs killed from UI — one view per article | Pending |
| 46.D | Painpoint × KPI scoring in criticality_scorer | Token-overlap fallback when no curated embeddings; ensures every tenant has non-zero painpoint signal day-one. |
| 46.E | `POST /api/onboard/v3` synchronous, no tier gate | Every article gets full Stage 1-12 + lede. ~$4.50/onboard, ~120-180s wall-clock. |
| 46.F | (planned) Daily 8 AM email brief | Pending |
| 46.G | CLAUDE.md rewrite | This file split — current state in CLAUDE.md, history here. |
| 46.H | `scripts/validate_phase46.py` — 11 tests | New: personalization-signals-present, every-rec-passes-quality-gate. |

### What got removed/deprecated

- `engine.main._run_article` tier gate — still used by ingest cron but bypassed by v3
- Phase 45.E-I defensive fallbacks (`unified_analysis._build_why_it_matters` inline recovery, `insight_generator` criticality_summary fallback, `recommendation_engine` empty-result deterministic monitor, `writer.py` last-line safety net) — kept but no longer required because v3's full-pipeline guarantee makes the contract hold at write time, not at runtime
- Phase 28 SSE worker (`scripts/onboarding_worker.py`) + onboarding state machine (`engine.models.onboarding_status` + `onboarding_events`) — not removed yet, but no longer the canonical onboarding path

### What's pending (Phase 46.C + F + cleanup pass)

- Frontend: kill CFO/CEO/Analyst toggle from `ArticleDetailSheet`
- Backend: daily 8 AM email cron (Resend integration exists; just need the cron job)
- Cleanup: delete `_background_onboard`, `onboard_stream`, `engine.analysis.on_demand`, role generator dead code (~2,000 LoC)

---

## Phase 45 — Validation loop hot-fixes (2026-05-27 → 2026-05-28)

Eight defensive patches to keep the validation script green while
Phase 46 was being designed. Most are subsumed by Phase 46's clean
architecture but stay in the codebase for now.

- **45.A**: LLM company resolver (replaced yfinance heuristic that picked wrong company on `reliance.com`)
- **45.B**: Synchronous onboard v2 endpoint (precursor to v3)
- **45.C**: Validation script aligned to v2 + test 07/08/09 path fixes
- **45.D**: Router registered, push
- **45.E**: Eager top-3 Stage 10+11+12 pass in v2 — promotes SECONDARY-tier articles after the first pass
- **45.F**: Wall-clock guards in v2 (limit=3, eager top-2, 150s budget cutoff, per-future timeouts)
- **45.G**: Global FastAPI exception handler + `/news/{id}/analysis` wrapping — surfaces actual exception class instead of bare 500
- **45.H**: Defensive fallbacks (`unified_analysis` criticality_summary inline recovery; `recommendation_engine` deterministic monitor when LLM returns empty; validation script SSE parser fix)
- **45.I**: Last-line safety net in `writer.py` — guarantees criticality_summary + ≥1 rec on disk regardless of upstream silent failures

---

## Phase 44 — End-to-end validation harness (2026-05-26 → 27)

Built `scripts/validate_phase44.py` (9 tests) to gate the rebuild.
Sub-phases:

- **44.A** — Initial harness
- **44.B** — Bulletproof `mark_ready` (closes orphan `analysing` state)
- **44.C** — Parallelise per-article analysis (ThreadPoolExecutor max_workers=3)
- **44.D** — Parallelise Phase 36 eager pass + raise validation bar to 240s
- **44.E** — Alias → canonical SSE event bridge in `onboard_stream`

---

## Phase 38-43 — Tone, lede, recs hardening (2026-05-25 → 2026-05-27)

Editorial polish + intelligence accuracy fixes. Highlights:

- **38**: TONE_GUARDRAILS module, post-render scrubber, strip emojis from email layout
- **39**: Editorial lede writer (`lede_writer.py`, Opus 4.6, schema bump to `3.3-editorial-lede`)
- **40.A**: Lede article-body grounding (rejects ungrounded claims)
- **40.B**: Reject off-topic recommendations (topic-drift check)
- **41**: Pre-warm deck + faster on-demand polling
- **42**: Register slug alias EARLY in onboard flow (prevents 404 on first deck load)
- **43.A**: Migrate Stage 12 recommendations to OpenRouter Opus 4.6 (was gpt-4.1-mini, producing templated recs)
- **43.B**: Migrate `subject_line.py` to OpenRouter

---

## Phase 32-37 — Unified analysis + body capture (2026-05-15 → 2026-05-24)

- **32**: Single unified analysis (kill per-role explainer, collapse to 4-bullet schema `3.0-unified-analysis`)
- **33**: Article-detail UX polish + Morning-Brew newsletter rewrite
- **34**: Mobile-first reading (`/now` swipe deck, `/forum`, `/wiki`, persistent chat)
- **35**: Recommendation accuracy guardrails (framework whitelist, canonical ₹ pin, audit_trail validation)
- **35.5**: Full-text article capture via `googlenewsdecoder` + `trafilatura` (closes the 80% headline-only problem)
- **36**: Body capture as a continuous PROCESS (retry cron, auto-reenrich on body backfill, per-tenant coverage metrics)

---

## Phase 27-31 — Phase C stitch (2026-05-13 → 2026-05-16)

The big stateful-agent stitch:
- **27**: LLM gateway + memory + chat + advisor + autoresearcher + MCP server + wiki + governance (8 subsystems, 14 MCP tools)
- **28**: Domain-driven onboarding + Supabase persistence (replaced by v3 in Phase 46.E)
- **29**: Per-role view tightening + per-panel info icons
- **30**: Onboarding cleanup
- **31**: Chat ↔ article context plumbing

---

## Phase 23-26 — Global-company hosting + role generators (2026-04-25 → 2026-05-10)

- **23**: Globalise news ingestion locale + onboarder (14 countries, 6 framework regions)
- **24-25**: CFO/CEO/Analyst dedicated generators (Phase 11a-b: ESG Analyst + CEO via real persona-specific LLM, CFO via legacy transform)
- **26**: Enhancement plan execution (criticality scorer, number rendering protocol, role distinctness)

---

## Phase 11-22 — Production readiness + analysis hardening (2026-04-24 → 2026-04-30)

- **11**: Phase C production gate (signed JWT, SQLite WAL, hourly backup, admin onboarding modal, Sentry, Prometheus)
- **12**: Analysis-hardening + fuzz harness (7 blockers + nightly fuzz)
- **13**: ET/Mint demo-readiness (event archetypes, recommendation audit_trail, dynamic FY strings, low-confidence warnings)
- **14**: Demo-grade analysis (canonical ₹ as hard constraint, positive-event precedent library)
- **15**: Full ontology migration (zero hardcoded domain dicts in Python)
- **16**: Field readiness (admin onboarding UI, production runbook, smoke test)
- **17 / 17b / 17c / 17d**: Causal Primitives integration (22 primitives, 123 P→P edges, 48 P→outcome edges, 69 P3/P4 chains)
- **18**: Social/labor intelligence coverage (`event_social_violation`, GRI:408/409/412)
- **19**: Self-evolving ontology (discovery buffer + promoter + audit log)

---

## Phase 0-10 — Initial build (2025-12 → 2026-04)

The original 8-phase build: setup, ontology foundation, ingestion, NLP,
analysis pipeline, insight generation, end-to-end integration, framework
deepening, SQLite index, FastAPI, frontend simplification.

Production gate (Phase 11) closed the original roadmap. Everything from
Phase 12 onwards is incremental hardening + new feature work.

---

## Reading the codebase

If you're new:

1. Read [`CLAUDE.md`](../CLAUDE.md) first — current architecture in one page.
2. Read [`api/routes/onboard_v3.py`](../api/routes/onboard_v3.py) — the one onboarding flow.
3. Skim this changelog from the top — recent phases inform recent code.
4. Run [`scripts/validate_phase46.py`](../scripts/validate_phase46.py) to see the contract green.

Most "why is this here?" questions trace back to a specific phase entry above.
