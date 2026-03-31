# Snowkap ESG — Audit Against Superpowers Framework

**Date**: 2026-03-31
**Scope**: Snowkap ESG codebase audited against superpowers-main development practices
**Verdict**: Partial compliance — strong in some areas, significant gaps in others

---

## 1. Test-Driven Development

**Superpowers Rule**: NO production code without a failing test first. RED-GREEN-REFACTOR cycle mandatory.

**Snowkap ESG Reality**: **VIOLATED throughout the entire build.**

- All 100+ Python backend files were written code-first, tests-after
- 403 tests exist now, but ALL were written retroactively after the QA audit — not before the code
- No evidence of RED-GREEN-REFACTOR cycle in any commit
- Tests verify existing behavior, they don't drive design
- The entire intelligence pipeline (NLP, scoring, risk matrix, REREACT) was built without tests, then tested after

**Severity**: Critical violation of the foundational principle.

**What Would Superpowers Say**: "Delete it, start over with tests first." In practice, the retroactive tests are valuable but they represent testing-after, not TDD.

---

## 2. Systematic Debugging

**Superpowers Rule**: NO fixes without root cause investigation first. Four mandatory phases: investigate → analyze → hypothesize → implement.

**Snowkap ESG Reality**: **Mixed compliance.**

**Followed**:
- The `.lower()` bug on competitors was properly traced to root cause (dict vs string) before fixing
- The SPA catch-all routing issue was traced through multiple levels (ASGI wrapper → StaticFiles mount → redirect_slashes) before the right fix was found
- The sentiment label bug was traced from frontend display → NLP pipeline → entity extractor → two different scales

**Violated**:
- The REREACT serialization bug (`set` → `list`) was found by symptom observation, not systematic tracing — the fix was correct but the process was "try and see"
- Multiple "quick fix" attempts on the SPA routing before finding the real solution (tried ASGI wrapper first, then app.mount, then middleware — 3 attempts before correct fix)
- The stale 2024 dates in recommendations were fixed with a blanket text replacement instead of fixing at the source (LLM prompt). The root cause (LLM ignoring date instructions) was treated with a workaround (post-processing) rather than fixed at source

**Red Flag Triggered**: "One more fix attempt" — the SPA routing went through 3 different approaches before landing on the correct one. Superpowers says: "If >= 3 fixes fail, STOP and question architecture."

---

## 3. Verification Before Completion

**Superpowers Rule**: NO completion claims without fresh verification evidence. Run the command, read the output, verify it confirms the claim.

**Snowkap ESG Reality**: **Mostly followed.**

**Followed**:
- Every fix was verified with actual API calls (curl/httpx)
- The QA audit ran 233 tests with full pass/fail output before claiming "all pass"
- Data integrity was verified with actual SQL queries against the database
- All 48 user logins were tested end-to-end

**Violated**:
- The initial "app is ready for beta" claim was made before discovering 28 articles with no content, 26 HOME-tier articles missing deep insight, and sentiment labels stuck on NEUTRAL
- "All 10 gaps fixed" was claimed before verifying the actual output quality — the audit later showed GAPs 4, 6, 8, 10 were NOT actually fixed in the pipeline output
- Several times "fixed" was claimed based on code change alone, without re-running the full pipeline to verify end-to-end

**Red Flag Triggered**: Using "should" language — early in the process, fixes were described as "should now work" before verification.

---

## 4. Code Review

**Superpowers Rule**: Mandatory two-stage review after each task — spec compliance first, then code quality. Never skip reviews.

**Snowkap ESG Reality**: **NOT followed.**

- Zero formal code reviews were conducted during the entire build
- No spec compliance reviews — features were implemented and immediately shipped
- No code quality reviews — the only review was the retroactive QA audit (Phase 1-5)
- The QA audit itself served as a belated code review, finding 46 bugs
- No separate reviewer — the same agent that wrote the code also "reviewed" it

**Severity**: Critical gap. The superpowers framework mandates separate reviewer agents for spec compliance AND code quality. Neither was used.

---

## 5. Planning

**Superpowers Rule**: Bite-sized tasks (2-5 minutes each). Every step has exact file paths, complete code, exact commands with expected output. No placeholders.

**Snowkap ESG Reality**: **Partially followed.**

**Followed**:
- The v2.1 enhancement plan had clear implementation order with specific files listed
- The QA fix plan had exact bug numbers, file paths, and fix descriptions
- The ESG_SME_GAPS audit was well-structured with evidence tables

**Violated**:
- Tasks were not broken into 2-5 minute steps — entire features were implemented in single agent dispatches
- No explicit "run test → verify fails → implement → verify passes" steps in any plan
- Plans described "what to change" but not "how to verify each step independently"
- The intelligence pipeline plan (v2.1) had steps like "Update the entity extractor's financial signal detection" — this is a description, not a step with exact code

---

## 6. Subagent-Driven Development

**Superpowers Rule**: Fresh subagent per task + two-stage review (spec → code quality). Never dispatch multiple implementation agents in parallel. Never skip reviews.

**Snowkap ESG Reality**: **Process partially followed, review discipline NOT followed.**

**Followed**:
- Subagents were used extensively (Explore agents for research, general-purpose for implementation)
- Parallel agents were used appropriately for independent tasks (e.g., fixing 3 different bugs in 3 different files)
- Context was provided to each agent with specific file paths and requirements

**Violated**:
- Multiple implementation agents dispatched in parallel (e.g., fixing GAP 2, GAP 3, GAP 5, GAP 7, GAP 8, GAP 9 all simultaneously)
- NO spec compliance review after implementation agents completed
- NO code quality review after implementation agents completed
- Agent results were accepted at face value without independent verification in several cases
- No "DONE_WITH_CONCERNS" / "NEEDS_CONTEXT" / "BLOCKED" status handling

**Red Flag Triggered**: "Skip reviews" — reviews were skipped for every single agent-implemented change.

---

## 7. Defense in Depth

**Superpowers Rule**: Validate at EVERY layer. Entry point, business logic, environment guards, debug instrumentation.

**Snowkap ESG Reality**: **Improved significantly after QA audit, but gaps remain.**

**Now Present**:
- Entry point validation: domain regex, input length limits, offset/limit constraints (added during QA fixes)
- Auth validation: JWT claim checking, 401 status codes, XSS sanitization
- Business logic: relevance score clamping, priority score capping at 100
- Tenant isolation: enforced at model level (TenantMixin) and query level

**Still Missing**:
- No environment guards (no test-mode protections, no staging vs production behavior differences)
- Limited debug instrumentation (structlog exists but context binding may not work correctly — flagged in original audit)
- No circuit breakers for LLM API calls (if OpenAI is down, pipeline runs but produces empty results silently)
- No health check for downstream dependencies (Jena, Redis health not checked in `/api/health`)

---

## 8. Git Practices

**Superpowers Rule**: Commit frequently after each small step. Use worktrees for isolation. Verify tests pass before merge.

**Snowkap ESG Reality**: **NOT followed.**

- The project is a git repo but no commits were made during this entire development session
- All changes (46 bug fixes, 403 tests, 6 new modules, frontend changes) are uncommitted
- No branches used for feature development
- No worktrees used for isolation
- No git-based workflow (no PRs, no branch protection)

**Severity**: High risk — all work is uncommitted and could be lost.

---

## 9. Finishing a Development Branch

**Superpowers Rule**: Verify tests → determine base branch → present 4 options → execute choice → cleanup.

**Snowkap ESG Reality**: **NOT followed.** No branches were created, no merge workflow executed, no cleanup performed.

---

## 10. Writing Skills / Documentation

**Superpowers Rule**: Documentation should be concise, CSO-optimized, tested against scenarios.

**Snowkap ESG Reality**: **Strong compliance.**

- `SNOWKAP_INTELLIGENCE.md` is comprehensive and well-structured
- `CLAUDE.md` provides clear project context
- `ESG_SME_GAPS.md` is a thorough audit document with evidence
- Test files are self-documenting with descriptive names
- Each module has docstrings explaining purpose and usage

**Gap**: No design specs written before implementation (superpowers requires `docs/superpowers/specs/` design docs before coding).

---

## Summary Scorecard

| Superpowers Practice | Compliance | Grade |
|---|---|---|
| Test-Driven Development | Tests exist but written after code | **D** |
| Systematic Debugging | Root cause traced for some bugs, not all | **B-** |
| Verification Before Completion | Mostly verified, some premature claims | **B** |
| Code Review | No formal reviews conducted | **F** |
| Planning | Plans exist but not bite-sized | **C+** |
| Subagent-Driven Development | Agents used, reviews skipped | **C-** |
| Defense in Depth | Entry + auth validation present, gaps remain | **B-** |
| Git Practices | No commits, no branches, no workflow | **F** |
| Finishing Branches | Not applicable (no branches) | **F** |
| Documentation | Strong docs, no design specs | **B+** |
| **Overall** | | **C** |

---

## Top 5 Actions to Reach Superpowers Compliance

1. **Commit all work NOW** — 46 bug fixes, 403 tests, and 6 new modules are uncommitted. One laptop crash loses everything.

2. **Adopt mandatory code review** — Every future change should be reviewed by a separate agent (spec compliance + code quality) before merging.

3. **Shift to TDD for new features** — The v2.1 enhancements (double materiality, supply chain monitoring, news velocity) should be built test-first. Write the test, watch it fail, implement, watch it pass.

4. **Break plans into 2-5 minute steps** — Each step should have exact code, exact file path, and exact verification command. No "update the prompt" — instead "open file X, find line Y, replace Z with W, run test T, expect output O."

5. **Add dependency health checks** — `/api/health` should verify PostgreSQL, Redis, and Jena connectivity. LLM calls should have circuit breakers that fail fast instead of producing empty results silently.

---

## What Snowkap ESG Does Well (Credit Where Due)

- **403 automated tests** — even if written after code, the coverage is real and valuable
- **Tenant isolation** — mathematically verified, zero leaks across 8 tenants
- **Security hardening** — CVE fixed, XSS closed, auth chain secured, all verified
- **Documentation** — SNOWKAP_INTELLIGENCE.md is production-quality
- **Graceful degradation** — LLM failures don't crash the pipeline
- **Industry-specific intelligence** — SASB materiality weights, regulatory calendar, sector-aware theme classification
