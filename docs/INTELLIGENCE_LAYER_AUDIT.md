# Snowkap ESG — Intelligence Layer Audit

> **The "97% ontology / 3% LLM" engine: what it is, what works, what doesn't, and what to do next.**

| | |
|---|---|
| **Date** | 2026-06-07 |
| **Scope** | The intelligence layer — the ontology (RDF/`.ttl`), the SPARQL query layer (`engine/ontology/`), the 12-stage analysis pipeline (`engine/analysis/`), and the ontology-vs-LLM split |
| **Method** | Static code review **+ live execution** on a dev machine (Python 3.13, rdflib 7.1.1): loaded the graph, ran the built-in competency-question suite, exercised the query + cascade functions against real data, traced join points, attempted the test suite |
| **Audience** | Engineering, product, and decision-makers planning enhancements |
| **Status of findings** | Evidence-backed; every claim below was reproduced by running code, with file:line references |

---

## How to read this document

- **Just need the verdict?** Read **§1 (Executive Summary)** — ~60 seconds.
- **Deciding what to build next?** Read **§1**, **§6 (Issues)** and **§8 (Roadmap)**.
- **Engineer picking up a fix?** Every issue in **§6** has evidence (file:line), impact, and a concrete fix. **§9 (Reproduce)** shows how to re-run the tests.
- **New to the system?** Start with **§3 (What it is, in plain English)** and the **Glossary (§10)**.

---

## 1. Executive summary (the 10-second verdict)

**The ontology is real and genuinely strong. The "97% ontology-driven" *claim* is overstated, the wiring around the ontology is fragile, and the automated test suite currently doesn't run.**

🟢 **What's solid:** The knowledge base loads cleanly (**10,353 triples**), passes **17/17** of its own competency-question tests, and its query + causal-cascade engine return real, sector-aware values. The core promise — *"numbers are computed from a knowledge graph, not hallucinated by an LLM"* — largely holds.

🟠 **What's shaky:** The decision that matters most (*which articles are "critical"*) is hardcoded in Python, not the ontology. The materiality lookup silently falls back to a neutral constant when an LLM-produced label doesn't byte-match the ontology — with no logging. A whole ontology layer (SASB) is loaded but never used.

🔴 **What's broken:** `pytest tests/` fails to even start (a stale import of a deleted module), so there is currently **no working automated regression safety net** for the intelligence layer.

**Is it "working 100%"?** No — and nothing this size is. It's a **strong ontology wrapped in over-claimed, under-tested integration.** With ~1–2 weeks of focused work (see §8) it can be both more accurate *and* honestly described.

| Dimension | Health | One-line reason |
|---|---|---|
| Ontology data & knowledge | 🟢 ~90% | Loads clean, CQ 17/17, correct sector-aware weights |
| Query + cascade engine | 🟢 ~90% | Real values; 30/31 event types causally linked |
| Integration / wiring | 🟠 ~60% | Silent default-fallbacks, a dead layer, ranking outside the ontology |
| Test / verification posture | 🔴 broken | Unit suite can't be collected |
| Accuracy of the "97/3" claim | 🟠 overstated | Docs disagree (97% vs 80% vs 15%); not measured |

---

## 2. What we tested, and how (so you can trust this)

This audit is **not** based on reading docs — the docs turned out to be partly wrong. Everything was executed:

1. **Loaded** the `_global` ontology graph in-process and counted triples.
2. **Ran `stats()`** to count every class (frameworks, events, topics, risks, SDGs…).
3. **Ran the competency-question suite** (`engine/ontology/cq_runner.py`) — the project's own ontology test harness.
4. **Exercised ~10 `intelligence.py` query functions** (materiality, risk weight, frameworks, event rules, hop-decay) and the **primitive/cascade engine** with real inputs.
5. **Traced the materiality join** from `pipeline.py` down to the SPARQL match, and verified the failure mode against real labels.
6. **Attempted `pytest tests/`.**

All numbers in this document are measured, not quoted.

---

## 3. What the intelligence layer is (plain English)

Snowkap reads ESG news for a company and produces a briefing. The "intelligence" is split into two parts:

- **The knowledge graph (ontology).** Think of it as a large, structured encyclopedia of ESG facts encoded as ~10,000 machine-readable statements ("triples"): *which ESG topics matter to which industries and by how much, which regulatory frameworks apply, how one risk cascades into a financial impact, what penalties precedents exist*, and so on. It lives in `.ttl` files and is queried with SPARQL (a query language for graphs). **This is where the numbers come from.**

- **The LLMs.** Large language models do the parts a graph can't: reading the article (extraction), tagging themes, and writing the human-readable prose (the editorial "lede", the deep-insight narrative, the recommendations). **This is where the words come from.**

The pitch — *"97% ontology, 3% LLM"* — means: *the figures and structure are computed by the graph, and the LLM only writes prose around numbers the engine already decided.* That's the right idea. The reality is more nuanced (see §7).

### The 12-stage pipeline at a glance

| Stage | Driver | What it does |
|---|---|---|
| 1. NLP extraction | 🤖 LLM (gpt-4.1-mini) | Sentiment, entities, ESG signals |
| 2. Theme tagging | 🤖 LLM (gpt-4.1-mini) | Picks the primary ESG theme |
| 3. Event classification | 📚 Ontology | 31 event types, score bounds |
| 4. Relevance & materiality | 📚 Ontology | 5-dimension score × **materiality weight** |
| 5. Causal cascade | 📚 Ontology | Chains an event → financial exposure (the ₹ number) |
| 6. Geo / climate match | 📚 Ontology | Region + climate-zone exposure |
| 7. Framework alignment | 📚 Ontology | Maps to BRSR/GRI/TCFD/… sections |
| 8. Risk assessment | 📚 Ontology | 10 ESG + 7 TEMPLES risk categories |
| 9. Stakeholder / SDG | 📚 Ontology | Maps to stakeholders + UN SDGs |
| **Ranking (criticality)** | ⚠️ **Hardcoded Python** | Decides which 3 articles are "critical" |
| 10. Deep insight | 🤖 LLM (Opus 4.6) | 9-section narrative |
| 11. Perspectives | 📚 + 🤖 | Role views (some ontology, some LLM) |
| 12. Recommendations + lede | 🤖 LLM (Opus 4.6) | Actions + editorial opener |

> **Key nuance:** the **ranking** step — the single most consequential decision (what gets promoted to a full, expensive, prominently-shown "critical" card) — is **not** ontology-driven. See Issue #3.

---

## 4. The numbers (measured vs documented)

The documentation is stale. None of these are catastrophic, but they show the "97/3" headline is not maintained or measured.

| Metric | Doc claims | **Measured** | Note |
|---|---|---|---|
| Total triples | 8,200 | **10,353** | Graph is ~26% bigger than documented |
| Event types | 22 | **31** | |
| Canonical industries | "14" (resolver) | **15** (ontology) | Off-by-one between resolver prompt and graph |
| `intelligence.py` cached funcs | "all `@lru_cache`d" | **11 of 56** | |
| Ontology-vs-LLM split | 97% / 80% / 15% (three different docs) | not measured | See §7 |
| Frameworks / topics / risk cats / TEMPLES / SDGs | 21 / 21 / 10 / 7 / 17 | **21 / 21 / 10 / 7 / 17** ✓ | These match |

---

## 5. What's working ✅

These are real strengths — keep them and build on them.

1. **The graph loads cleanly** — all ~24 core `.ttl` layers parse, 10,353 triples, no errors.
2. **The ontology passes its own tests** — `cq_runner` reports **17/17 competency questions pass, 0 empty, 0 errors.** The knowledge needed to answer the product's core questions is present and queryable.
3. **No empty knowledge classes** — frameworks (21), event types (31), risk categories (10), TEMPLES (7), SDGs (17), topics (21) are all populated.
4. **Query functions return real data** — e.g. `frameworks_for_topic("climate change")` → 15 frameworks; `event_rules` → 31 rules with score floors/ceilings/keywords; `hop_decay` → `{0:1.0, 1:0.7, 2:0.4, 3:0.2, 4:0.1}`.
5. **The causal cascade is well-connected** — **30 of 31** event types link to ≥1 causal primitive, so the "compute the ₹ exposure" path has real graph coverage.
6. **The materiality *data* is correct and sector-aware** — e.g. `Climate Change → Power/Energy = 1.0`, `→ Financials/Banking = 0.8`, `Water → Banking = 0.3`. The knowledge is right (the *lookup* is the problem — see Issue #1).

**Bottom line:** the "computed, not fabricated numbers" differentiator is real. The graph is an asset.

---

## 6. What's not working — issues (severity-ranked)

| # | Severity | Issue | Evidence |
|---|----------|-------|----------|
| 1 | 🔴 High | Materiality silently degrades to a constant | `intelligence.py:153-206`, `pipeline.py:336` |
| 2 | 🔴 High | Automated test suite doesn't run | `tests/conftest.py:35-37` |
| 3 | 🟠 Medium | Core ranking is hardcoded Python, not ontology (breaks Rule #1) | `criticality_scorer.py:99-134` |
| 4 | 🟠 Medium | A 584-line ontology layer (SASB) is loaded but never used | `intelligence.py:169`, `pipeline.py:336` |
| 5 | 🟡 Low | Silent `except: pass` hides failures (breaks Rule #2) | ~15 in `engine/`, incl. `intelligence.py:175` |
| 6 | 🟡 Low | Documentation drift / unmeasured claims | §4 |

### Issue #1 — Materiality silently degrades to a neutral constant 🔴

**What:** Stage 4 multiplies an article's relevance by a *materiality weight* looked up from the ontology for the `(theme, industry)` pair. The lookup (`query_materiality_weight`) matches the industry and theme on an **exact, case-insensitive label**. On any miss it returns **0.5** (neutral) — silently, with no log.

**Proof:** The triple `Climate Change → Financials/Banking = 0.8` exists. But calling the lookup with the industry string `"Banking"` returns **0.5**, because the ontology's label is the exact string `"Financials/Banking"`. `"Commercial Banks"`, `"banking & capital markets"` → also 0.5.

**Why it happens:** `pipeline.py:336` calls `score_relevance(nlp, themes, company.industry)`, passing **raw LLM-produced strings** — `company.industry` (from the company resolver) and `tags.primary_theme` (from the Stage-2 tagger) — straight into the exact-match query. **There is no normalization layer** mapping free-text to the 15 industry / 21 theme canonical labels. Both sides of the join are unguarded.

**Impact:** For any company whose stored industry isn't byte-exact (e.g. "Banking" instead of "Financials/Banking"), **every** article's materiality collapses to 0.5 — neutralizing the single most-touted "ontology-driven" scoring input. Because it's silent, this is invisible in production: the output looks plausible, just less accurate. This is the most important correctness risk in the layer.

**Fix:** (a) Add an alias/normalization shim that maps resolver/tagger output to canonical ontology labels before the query; (b) **log every default-hit** (0.5 / 1.0) so degradation is observable; (c) consider fuzzy/synonym matching in the SPARQL or a lookup table in `.ttl`.

> The exact labels the LLM must hit today (no synonyms tolerated):
> **Industries (15):** Asset Management, Automotive, Chemicals, Consumer Goods, Financials/Banking, Healthcare, Infrastructure, Metals & Mining, Oil & Gas, Pharmaceuticals, Power/Energy, Renewable Energy, Retail, Steel, Technology.
> **Themes (21):** Biodiversity, Board & Leadership, Climate Adaptation, Climate Change, Community Impact, Data Privacy & Security, Diversity Equity & Inclusion, Emissions, Energy, Ethics & Compliance, Health & Safety, Human Capital, Pollution, Product Safety, Risk Management, Stakeholder Governance, Supply Chain Labor, Tax Transparency, Transparency & Disclosure, Waste & Circularity, Water.

### Issue #2 — The automated test suite does not run 🔴

**What:** `pytest tests/` fails at **collection** (before any test executes) with `ModuleNotFoundError: No module named 'backend'`.

**Proof:** `tests/conftest.py:35-37` imports `backend.core.permissions`, `backend.core.security`, `backend.main` — but the `backend/` package was removed during the Phase-46 rebuild (CLAUDE.md §2). Because `conftest.py` is loaded for the whole suite, **zero tests can run.**

**Impact:** There is currently **no working automated regression net** for the engine. CLAUDE.md §9 instructs contributors to run `python -m pytest tests/ -q` — that command is dead. Confidence rests entirely on `validate_phase46.py`, which needs a fully deployed stack (Postgres + LLM keys) and so isn't run routinely. **Any of the other issues here could regress undetected.**

**Fix:** Repair or replace the `backend.*` imports in `conftest.py` (point them at the current `api.auth_context` / `api.main` equivalents, or delete the legacy fixtures). Then triage whatever individual tests fail. This is the highest-leverage fix because it unlocks verification of everything else.

### Issue #3 — The core ranking is hardcoded Python, not the ontology 🟠

**What:** The criticality score — which decides **which 3 articles become "critical"** (full Stage 10–12 LLM treatment + prominent display) vs "light" — is computed from **hardcoded Python weights and thresholds**, commented *"locked per the plan §3.1, §3.2."*

**Proof:** `criticality_scorer.py:99-134` — `WEIGHTS_DEFAULT` (`financial_magnitude 0.30, materiality 0.20, painpoint_match 0.20, actionability 0.15, …`), per-role overrides (`WEIGHTS_BY_ROLE`), and `BAND_THRESHOLDS` (`CRITICAL ≥ 0.75, HIGH ≥ 0.55, …`) are all Python literals.

**Why it matters:** CLAUDE.md **Rule #1** ("never hardcode domain knowledge in Python — weights/thresholds/rules go in `.ttl`") is a stated **P0**. The most consequential intelligence decision in the product violates it. It also undercuts the "ontology-driven" claim: the *ranking brain* lives outside the ontology, so it can't be tuned per-tenant via the graph and isn't covered by the ontology tests.

**Fix (decision required):** Either (a) move these weights/thresholds into `.ttl` and query them (honors Rule #1, enables per-tenant tuning), or (b) consciously accept them as Python and **update the docs** to stop claiming the ranking is ontology-driven. Don't leave it silently contradictory.

### Issue #4 — The SASB materiality layer is loaded but never used 🟠

**What:** `sasb_materiality.ttl` (584 lines, "banks ≠ industrials" sector weights) is parsed into the graph but never consulted by the scoring path.

**Proof:** `query_materiality_weight` only consults SASB when a `sasb_sector` argument is passed (`intelligence.py:169`). The only caller, `pipeline.py:336`, **does not pass it.** No other code path queries `sasb_loader.query_sasb_materiality` outside that guarded branch.

**Impact:** A documented capability (CLAUDE.md §6 lists SASB as a full ontology layer) and ~584 lines of curated knowledge are dead weight at runtime. The more accurate sector-specific weights it contains are exactly what would *fix* part of Issue #1 — so this is a missed asset, not just clutter.

**Fix (decision required):** Either wire `sasb_sector` through `pipeline.py` → `score_relevance` → `query_materiality_weight` (recommended — improves accuracy), or remove the layer and the docs that reference it.

### Issue #5 — Silent `except: pass` hides failures 🟡

**What:** ~15 `except …: pass` blocks in `engine/` swallow errors with no log — including inside `intelligence.py:175` (the SASB lookup) and `engine/config.py` (the held-company overlay).

**Why it matters:** CLAUDE.md **Rule #2** forbids silent catches. Combined with Issue #1, this is why a materiality miss is invisible: the failure is swallowed *and* the fallback is silent.

**Fix:** Replace bare `pass` with `logger.warning(..., exc_info=True)`. Mechanical, low-risk, high-observability payoff.

### Issue #6 — Documentation drift / unmeasured claims 🟡

**What:** See §4 — triples (8,200 vs 10,353), event types (22 vs 31), industries (14 vs 15), "all cached" (11/56), and the headline split stated three different ways (97% / 80% / 15%).

**Why it matters:** Stakeholders plan around these numbers. A "97% ontology" claim that the codebase contradicts erodes trust and can mislead a buyer/investor conversation.

**Fix:** Generate the counts programmatically (from `stats()` + a call-site analysis) and keep one measured number. See §7.

---

## 7. The "97% ontology / 3% LLM" claim — honest verdict

**The claim is a marketing figure, not a measured one — and the docs don't even agree with each other:**

- `docs/INTELLIGENCE_AND_CALCULATIONS.md` → **97% / 3%**
- `docs/PRD.md` → **"15% of intelligence is LLM"**
- `CLAUDE.md` → **"~80% in .ttl"**

**Is it true in spirit?** Partly:

- ✅ **Defensible:** the *numbers* (relevance scores, framework codes, ₹ cascade, risk weights) are computed/constrained by the graph, not invented by an LLM. That is real and it's the product's genuine edge.
- ❌ **Overstated**, for three reasons:
  1. The **ranking** that decides what's important is hardcoded Python (Issue #3) — not ontology.
  2. The **output the user actually reads** — the editorial lede, the 9-section insight, the recommendations, the role narratives — is *entirely* LLM prose. Calling that "3%" understates both its footprint and its **risk**: it's the fabrication-prone surface you had to build *two* LLM gates (`approval_gate`, rec quality gate) to police. The "3%" is the *riskiest* 3%.
  3. Parts of the ontology are **loaded but unused** (Issue #4) or **silently bypassed** (Issue #1), so the *effective* ontology contribution is lower than the *loaded* ontology size suggests.

**Recommended framing for external use:** *"Every figure is computed from a 10,000-triple ESG knowledge graph and provenance-tagged; LLMs render the narrative around those figures and are gated against fabrication."* — accurate, and still a strong story. Replace the single percentage with this, or compute a real, defensible number.

---

## 8. Enhancement roadmap (prioritized for planning)

Effort: **S** = <1 day · **M** = 1–3 days · **L** = ~1 week. Ordered by leverage.

### Phase 0 — Restore the safety net (do first)
| Action | Effort | Impact | Issue |
|---|---|---|---|
| Fix `conftest.py` `backend.*` imports so `pytest` collects; triage failures | **M** | 🔴 High — unlocks verification of everything else | #2 |

### Phase 1 — Quick wins (high impact, low effort)
| Action | Effort | Impact | Issue |
|---|---|---|---|
| Log every materiality/risk default-hit (0.5 / 1.0) | **S** | 🔴 High — makes silent degradation visible | #1, #5 |
| Replace silent `except: pass` with logged catches in `engine/` | **S** | 🟡 Med — observability | #5 |
| Regenerate doc numbers from `stats()`; pick one measured split figure | **S** | 🟡 Med — trust | #4, #6, #7 |

### Phase 2 — Accuracy fixes (the real intelligence quality wins)
| Action | Effort | Impact | Issue |
|---|---|---|---|
| Add industry/theme **normalization shim** → canonical labels before the materiality query | **M** | 🔴 High — fixes the biggest correctness risk | #1 |
| **Wire SASB** `sasb_sector` through the scorer (or remove the layer) | **M** | 🟠 Med — sector-accurate materiality | #4 |
| Add an ontology-level test asserting every live company's `industry` resolves to a real materiality weight (no silent 0.5) | **S** | 🟠 Med — prevents regression of #1 | #1, #2 |

### Phase 3 — Strategic decisions (need a human call)
| Decision | Effort | Impact | Issue |
|---|---|---|---|
| Move criticality weights/bands into `.ttl` (honor Rule #1, enable per-tenant tuning) **or** formally accept them as Python and correct the docs | **L** | 🟠 Med — consistency, tunability, honest claim | #3 |
| Compute and publish a real "ontology vs LLM" contribution metric to replace "97/3" | **M** | 🟡 Med — credible external story | #7 |

### If you only do three things
1. **Fix the test suite** (#2) — you're flying blind without it.
2. **Add the materiality normalization + default-hit logging** (#1) — biggest accuracy win, and you'll finally *see* where the ontology isn't firing.
3. **Decide SASB and the criticality-weights question** (#4, #3) — wire it or cut it; own the claim either way.

---

## 9. How to reproduce these findings

Run from the repo root (`snowkap-esg/`). No Postgres or LLM keys needed for the ontology checks — `SNOWKAP_ALLOW_SQLITE=1` keeps the DB hard-gate out of the way.

```bash
# 1. Load the graph, count triples, run the competency-question suite
PYTHONPATH=. SNOWKAP_ALLOW_SQLITE=1 python - <<'PY'
from engine.ontology.graph import OntologyGraph
from engine.ontology import cq_runner
g = OntologyGraph(tenant_id="_global").load()
print("triples =", len(g.graph))
print("stats   =", g.stats())
r = cq_runner.run_all(graph=g)
print(f"CQ: total={r.total} pass={r.passing} empty={r.warnings} error={r.errors}")
PY

# 2. Reproduce the materiality silent-default bug
PYTHONPATH=. SNOWKAP_ALLOW_SQLITE=1 python - <<'PY'
from engine.ontology.graph import OntologyGraph
from engine.ontology import intelligence as I
g = OntologyGraph(tenant_id="_global").load()
print("Banking            ->", I.query_materiality_weight("Climate Change","Banking",graph=g))            # 0.5 (miss)
print("Financials/Banking ->", I.query_materiality_weight("Climate Change","Financials/Banking",graph=g)) # 0.8 (hit)
PY

# 3. Confirm the test suite is broken
PYTHONPATH=. SNOWKAP_ALLOW_SQLITE=1 python -m pytest tests/ --co -q   # ModuleNotFoundError: No module named 'backend'
```

---

## 10. Glossary (for non-specialist readers)

| Term | Plain meaning |
|---|---|
| **Ontology** | A structured, machine-readable encyclopedia of ESG knowledge (facts + relationships). |
| **Triple** | One fact in the graph, e.g. *(Climate Change) — (is material for) — (Banking, weight 0.8)*. The graph has ~10,353. |
| **RDF / `.ttl`** | The standard format the knowledge is stored in (Turtle files). |
| **SPARQL** | The query language used to ask the graph questions. |
| **Materiality weight** | How much a given ESG topic matters to a given industry (0.0–1.0). Drives relevance scoring. |
| **Causal cascade / primitive** | The chain the engine follows to turn an event into a financial (₹) exposure estimate. |
| **Competency question (CQ)** | A test query that checks the ontology can answer a question the product needs. |
| **Criticality** | The score that decides whether an article becomes a prominent "critical" card. |
| **Stage 10/12/lede** | The LLM-written parts: deep insight, recommendations, and the editorial opener. |
