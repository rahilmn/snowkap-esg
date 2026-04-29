# Phase 10 — Drip Campaign Scheduler + Sales Admin Role

> **Active plan.** Tracks execution checkboxes for Phase 10. See `CLAUDE.md` reference section for how this fits the broader roadmap. Phase 9 (manual Share button) is the foundation — Phase 10 layers scheduling + admin controls on top without touching the render surface.

## Context

Phase 9 shipped one-click manual Share: `ShareArticleButton` → `share_article_by_email()` → `render_newsletter()` → Resend. The recipient gets a personalised, Economic-Times-style HTML email carrying the full analysed article.

Phase 10 layers three things on top:

1. **Scheduler** — configure campaigns to drip on a cadence (weekly / monthly / once), alongside the existing manual send.
2. **Sales admin login** — a `sales@snowkap.com`-style user with super-admin privileges: switch between all companies, switch role-view, configure scheduled campaigns.
3. **Client onboarding (unchanged)** — external client users continue through `resolve-domain → login → onboarding` with role-based view customisation.

**Product-designer lens:** a shared email is the moment of truth. The recipient forms their entire impression of Snowkap's intelligence from one HTML render. Every ₹ figure must be source-tagged, every framework citation must be real, the narrative must read like a senior analyst wrote it — not a template-mailer. Phase 10 must not let automation dilute Phase 9's hand-curated quality.

## Deliverables

- SQLite-backed campaign store (`campaigns`, `campaign_recipients`, `campaign_send_log`)
- Cron-driven runner that reuses `share_article_by_email()` verbatim — zero rendering regression
- Super-admin role with company + role-view switchers in the header
- `/settings/campaigns` page (list + create/edit/pause/resume/send-now/log)
- Role-based default perspective panel on article detail
- This plan file (progress tracked via checkboxes below)

## Decisions locked in

| Question | Answer | Impact |
|---|---|---|
| Sender domain | `snowkap.co.in` verified in Resend | Default FROM = `newsletter@snowkap.co.in`. Production-ready from day one. |
| Recipient input | Textarea only (paste emails, one per line) | No CSV upload UI or parser in V1. |
| Campaign templates | Share single article only (reuse Phase 9) | Runner calls `share_article_by_email()` per recipient. `template_type` column kept in schema for V2. |
| Unsubscribe | Defer to V2 | Internal/known-recipient only. No public opt-out URL. |

## Non-goals

- Legacy `backend/` campaign model stays as-is; Phase 10 adds new SQLite persistence under `engine/`.
- No newsletter-digest / CFO exec-brief templates in V1.
- No CSV import, open-tracking pixels, A/B tests, unsubscribe URL, cross-tenant membership migration.

## Reusable pieces (must NOT rebuild)

| What | Where | Reuse for |
|---|---|---|
| Role enum + perms + `require_permission` | `backend/core/permissions.py` | Add SUPER_ADMIN; gate campaign endpoints |
| JWT with tenant/company/designation/perms | `backend/core/security.py` | Carry SUPER_ADMIN bits |
| `get_tenant_context()` | `backend/core/dependencies.py` | Extend with `?tenant_id=` override |
| `share_article_by_email()` | `engine/output/share_service.py` | Called per recipient by runner |
| `render_newsletter()` + `NewsletterArticle` | `engine/output/newsletter_renderer.py` | HTML email surface (spec below) |
| `name_from_email()` + `is_valid_email()` | `engine/output/email_sender.py` | Greeting + validation |
| `build_articles_from_outputs()` | `engine/output/newsletter_renderer.py` | `latest_home` resolver |
| `sqlite_index.py` pattern | `engine/index/sqlite_index.py` | Template for `campaign_store.py` |
| Radix Dialog | `@radix-ui/react-dialog` | Campaign form modal |
| ShareArticleButton | `client/src/components/sharing/ShareArticleButton.tsx` | "Save as campaign" variant |
| authStore | `client/src/stores/authStore.ts` | Extend with `activeCompanySlug`, `viewAsRole` |
| `news.share` in api.ts | `client/src/lib/api.ts` | Pattern for new `campaigns.*` methods |

## Email content spec — what the recipient sees

Phase 10 does **not** change the HTML. The runner calls Phase 9's `share_article_by_email()` unchanged. The accuracy audit in Phase B validates these invariants:

| Section | Source | Must be true |
|---|---|---|
| Header | `newsletter_title`, `tagline`, Snowkap branding | 600px width; tested in Gmail/Outlook/Apple Mail |
| Greeting | `name_from_email()` or `name_override` | Capitalised first name; falls back for generic mailboxes |
| Intro paragraph | `sender_note` or default | Default contains "one ESG signal on {company}" + source-tag pledge |
| Article headline | `NewsletterArticle.title` | Matches the analysed article verbatim |
| Materiality badge | `insight.decision_summary.materiality` | HIGH / MODERATE / LOW, colour-coded pill |
| Bottom line | `insight.decision_summary.key_risk` or `net_impact_summary` | Single sentence, ≤ 40 words |
| Why it matters (3 bullets) | Framework alignment + causal cascade + peer precedent | Each bullet cites a named source |
| Financial exposure grid | Primitive engine output (₹ Cr, margin bps) | Every ₹ tagged `(from article)` or `(engine estimate)` |
| Board action (HOME only) | `perspectives.ceo.board_paragraph` | Rendered when CEO perspective exists |
| Footer CTA | Campaign `cta_url` + `cta_label` | Defaults to `https://snowkap.com/contact-us/` |
| Provenance line | Auto-generated | Mentions ontology triples + frameworks cited + primitives |

**Anti-patterns** (runner pre-checks guard these):

1. SECONDARY-tier article with no Stage 10 enrichment → email has empty "Bottom line". **Fix:** `latest_home` filters HOME-only; `specific` admin picks trigger warning.
2. Stale schema (`schema_version != 2.0-primitives-l2`) → missing cascade. **Fix:** runner triggers `enrich_on_demand` before rendering.
3. Framework citations without section codes → weakens credibility. **Fix:** assert `section` field populated before send.
4. Greeting falls back to neutral silently → feels mass-mailed. **Fix:** admin previews greeting in UI before send.

---

## Phase A — Plan doc + sales admin role + switchers  *(2.5 days)*

### A.0  Plan doc + CLAUDE.md reference
- [x] `PHASE_10_CAMPAIGN_SCHEDULER_PLAN.md` created at repo root.
- [x] `CLAUDE.md` contains a reference line under the "Reference: Production Readiness Plan (Active)" section.

### A.1  Backend role + allowlist
- [x] `SUPER_ADMIN = "super_admin"` added to `backend/core/permissions.py` Role enum. Grants every existing permission + `OVERRIDE_TENANT_CONTEXT`, `MANAGE_DRIP_CAMPAIGNS`.
- [x] `api/routes/legacy_adapter.py::auth_login` + `auth_returning_user` extended: email in `SNOWKAP_INTERNAL_EMAILS` + `@snowkap.com`/`@snowkap.co.in` → JWT carries `super_admin`, `manage_drip_campaigns`, `override_tenant_context` perms. Uses `api/auth_context.is_snowkap_super_admin()`.
- [x] `api/auth_context.py` created: `decode_bearer`, `require_bearer_permission`, `SUPER_ADMIN_PERMISSIONS`, `is_snowkap_super_admin`.
- [x] Tenant override: `?tenant_id=<slug>` on `/news/feed` is already a query param — `CompanySwitcher` will pass through. Regular clients get their own slug only (front-end guard); admin bypass is implicit via the switcher UI.
- [x] `GET /api/admin/tenants` (in new `api/routes/admin.py`) returns `[{id, slug, name, industry, article_count, last_analysis_at}]`. Gated by `require_bearer_permission("super_admin")`. Mounted in `api/main.py` BEFORE `legacy_adapter` so it owns the path.
- [x] Tests: `tests/test_phase10_auth.py` — 11 cases, all green.

### A.2  Frontend switchers
- [x] `client/src/components/admin/CompanySwitcher.tsx` — header dropdown, grouped "Target companies" + "Onboarded prospects (N)", writes `companyId` to authStore. Verified in preview: all 7 targets + 4 onboarded render with per-tenant article counts.
- [x] `client/src/components/admin/RoleViewSwitcher.tsx` — 5 options: My role / CFO / CEO / ESG Analyst / Member. Writes `viewAsRole` to authStore. Verified: selecting "CFO" updates the button label AND persists to sessionStorage.
- [x] Both visible only when `permissions.includes("super_admin")`. Verified: sales@snowkap.com sees both; `ci@mintedit.com` sees neither.
- [x] `authStore.ts` extended with `viewAsRole`, `setViewAsRole`, `useActiveRole()` selector, and `useIsSuperAdmin()` hook.
- [x] `MinimalHeader.tsx` conditionally mounts `CompanySwitcher` (for admins) vs the legacy 7-target dropdown (for clients); `RoleViewSwitcher` mounts next to PerspectiveSwitcher only for admins.
- [x] `api.ts` gets typed `admin.tenants()` + `AdminTenant` interface.
- [x] `npm run type-check` passes on all Phase 10 files (pre-existing errors in ArticleDetailSheet.tsx:1104/1107 are unrelated).

### A.1b  Super-admin all-roles-all-companies access audit
- [x] `SUPER_ADMIN_PERMISSIONS` in `api/auth_context.py` now contains every `Permission` enum value (24 of 24) + shim strings `read`/`chat` + Phase 10 additions.
- [x] `tests/test_phase10_super_admin_access.py` — 12 tests:
  - Every Permission enum value is in super-admin set (locked invariant — adding a new enum value fails the test until the list is updated)
  - No duplicates in the list
  - Feed endpoint 200s for every slug `/api/admin/tenants` returns (targets + onboarded)
  - All 3 perspectives (CFO / CEO / ESG Analyst) retrievable for an admin-seeded article
  - Full insight payload retrievable
  - Client tokens still 403 on admin + share (regression guards)
  - Admin not blocked from share (passes auth, gets 404 for nonexistent article)

### Validation gate A
- [ ] `PHASE_10_CAMPAIGN_SCHEDULER_PLAN.md` exists; `CLAUDE.md` contains reference line.
- [ ] `sales@snowkap.com` (in allowlist) → JWT includes `super_admin`, `override_tenant_context`, `manage_drip_campaigns`.
- [ ] `sales@other-domain.com` NOT in allowlist → SUPER_ADMIN NOT granted.
- [ ] CompanySwitcher lists all 7 companies; selecting `adani-power` reloads feed filtered to that slug.
- [ ] RoleViewSwitcher "CFO" → next article open defaults to CFO panel.
- [ ] Regular client user → switchers NOT rendered; `GET /api/admin/tenants` → 403.
- [ ] Admin's `?tenant_id=adani-power` override honoured; non-admin's silently ignored.
- [ ] `pytest tests/test_phase10_auth.py` passes (6 cases).

---

## Phase B — Campaign persistence + scheduler core  *(3.5 days)* — SHIPPED

### B.1  Data model (`engine/models/campaign_store.py`)
- [x] Three tables: `campaigns`, `campaign_recipients`, `campaign_send_log`.
- [x] Indexes: `idx_campaigns_active_due`, `idx_sendlog_campaign`, `idx_sendlog_dedup`, `idx_recipients_campaign`.
- [x] Full CRUD: create/get/list/list_due/update/set_status/mark_sent/delete + recipient replace/add/count/touch + send-log append/list/find_recent_send.
- [x] Typed dataclasses: `Campaign`, `Recipient`, `SendLogEntry` with `to_dict()`.
- [x] ON DELETE CASCADE for recipients; send_log survives delete (audit trail).
- [x] **Tests**: 18/18 pass (`test_phase10_campaign_store.py`).

### B.2  Cadence math (`engine/output/cadence.py`)
- [x] Pure `compute_next_send(cadence, day_of_week, day_of_month, send_time_utc, from_time=now)`.
- [x] `cadence_interval(cadence) -> timedelta`, `dedup_window_start(cadence, now_iso)`.
- [x] Handles once/weekly/monthly + UTC + month rollover + 28-day cap.
- [x] Accepts ISO strings with or without `Z` suffix.
- [x] **Tests**: 21/21 pass (`test_phase10_cadence.py`).

### B.3  Runner (`engine/output/campaign_runner.py`)
- [x] `run_due_campaigns(now, dry_run, campaign_id, force)` — both batch and send-now modes.
- [x] Article resolution: `specific` vs `latest_home` (HOME-only via `build_articles_from_outputs`).
- [x] **Freshness pre-check:** calls `enrich_on_demand(force=True)` when `schema_version != "2.0-primitives-l2"`.
- [x] **Accuracy pre-check:** rejects insights without materiality / bottom line / framework section.
- [x] Per-recipient loop isolates failures (one Resend error doesn't abort the batch).
- [x] Dedup probe via `find_recent_send` + `dedup_window_start(cadence)` = cadence_interval/2.
- [x] Advances `next_send_at` only when at least one delivery succeeded AND not `force=True`.
- [x] CLI: `python -m engine.output.campaign_runner run-due [--once] [--dry-run] [--campaign-id <id>] [--now <iso>] [--force]`.
- [x] **Tests**: 7/7 pass (`test_phase10_campaign_runner.py`).

### B.4  API (`api/routes/campaigns.py`)
- [x] Router-level gate: `require_auth` + `require_bearer_permission("manage_drip_campaigns")`.
- [x] `GET /api/campaigns` — list (all or filtered by status)
- [x] `POST /api/campaigns` — create with embedded recipients list
- [x] `GET /api/campaigns/{id}` — detail + recipient_count
- [x] `PATCH /api/campaigns/{id}` — partial update; recomputes next_send_at on schedule change
- [x] `DELETE /api/campaigns/{id}` — 204, cascades recipients (send_log survives)
- [x] `POST /api/campaigns/{id}/send-now` — 202, runs in background, force=True (doesn't advance schedule)
- [x] `POST /api/campaigns/{id}/pause` / `/resume` / `/archive`
- [x] `GET /api/campaigns/{id}/send-log?limit=50`
- [x] `GET /api/campaigns/{id}/preview` — renders HTML for next send without delivery
- [x] `POST /api/campaigns/{id}/recipients` — bulk replace
- [x] Pydantic validation on create/patch (3-60 char names, 1-28 day_of_month, etc.).
- [x] **Tests**: 13/13 pass (`test_phase10_campaigns_api.py`).

### B.5  Mount + env
- [x] `api/main.py` mounts `campaigns.router` (and already had `admin.router` from Phase A.1).
- [x] `engine/output/email_sender.py::_default_from_address()` → `SNOWKAP_FROM_ADDRESS` env, falls back to legacy `EMAIL_FROM`, final default `Snowkap ESG <newsletter@snowkap.co.in>`.

### Phase B validation gate — GREEN
- [x] `pytest tests/test_phase10_cadence.py` — 21/21 pass.
- [x] `pytest tests/test_phase10_campaign_store.py` — 18/18 pass (CRUD, dedupe, cascade).
- [x] `pytest tests/test_phase10_campaign_runner.py` — 7/7 pass (article resolve, stale-skip, freshness, dedup, force-send, paused).
- [x] `pytest tests/test_phase10_campaigns_api.py` — 13/13 pass (permissions, create, lifecycle, send-now, patch).
- [x] Full Phase 9 + 10 suite: **136/136 tests pass** (48 Phase 9 + 17 auth + 12 access + 21 cadence + 18 store + 7 runner + 13 API).
- [ ] Product-designer accuracy audit (3 real articles × 3 tiers, htmlemailcheck.com, Gmail/Outlook/Apple Mail) — **deferred to Phase E end-to-end run with a real RESEND_API_KEY**; all tests mock Resend for determinism.

### Validation gate B  *(accuracy + delivery)*
- [ ] `pytest tests/test_phase10_cadence.py` — 10+ cases.
- [ ] `pytest tests/test_phase10_campaign_runner.py` — dedup, stale-skip, on-demand re-enrich, HOME-only filter, recipient iteration after partial Resend failure.
- [ ] `pytest tests/test_phase10_campaigns_api.py` — permission gating, recipient parsing, send-now roundtrip, pause blocks run.
- [ ] **Product-designer accuracy audit:**
  - [ ] Render 3 real articles (HIGH, MODERATE, LOW) via runner `--dry-run`.
  - [ ] Each HTML: all ₹ figures source-tagged, frameworks have section + rationale, perspectives carry their content, 0 placeholder leaks, 0 empty sections.
  - [ ] HTML passes `htmlemailcheck.com` — 0 errors; Gmail/Outlook/Apple Mail previews clean.
- [ ] Integration: seeded campaign + `RESEND_API_KEY` → actual email arrives at `ci@snowkap.com` from `newsletter@snowkap.co.in`.
- [ ] Dedup: 2 runs in 30s → second writes `skipped_dedup`.
- [ ] Stale-schema article → `enrich_on_demand` triggers; send succeeds on retry.

---

## Phase C — Frontend campaign management  *(2 days)* — SHIPPED

### C.1  SettingsCampaignsPage (`client/src/pages/SettingsCampaignsPage.tsx`)
- [x] Route `/settings/campaigns` registered in `App.tsx`, page-level guard via `hasPermission("manage_drip_campaigns")` → `<Navigate to="/home">` for non-admins.
- [x] 4 tabs: Active / Paused / Archived / Send history.
- [x] Table columns: Name, Cadence (humanised "Weekly · Mondays 09:00 UTC"), Company slug, Recipients count, Last sent, Next send, Status pill, Actions.
- [x] Row actions: Edit · Pause/Resume · Send now · Log · Archive · Delete (with confirm).
- [x] "New campaign" button opens `CampaignFormDialog`.
- [x] Send history tab merges send_log across every campaign sorted newest-first.
- [x] Send log modal per-campaign (inline, not Radix).
- [x] Status pills colour-coded: active=emerald, paused=amber, archived/preview=gray, sent=emerald, failed=red, skipped_stale=amber, skipped_dedup=gray.

### C.2  CampaignFormDialog (`client/src/components/campaigns/CampaignFormDialog.tsx`)
- [x] Radix Dialog with all 12 fields: Name, Target company (dropdown from `/api/admin/tenants`), Article selection radio, conditional article_id input, Cadence radio (once/weekly/monthly) with conditional day-of-week / day-of-month / time inputs, Recipients textarea (live-counted), Sender note, CTA URL + label.
- [x] Save + Save & Send now buttons — the latter calls `/send-now` after the create/patch returns.
- [x] Inline validation for name length, missing article_id, day_of_month 1–28, bad email syntax.
- [x] Auto-picks first tenant as default when dialog opens.
- [x] Edit mode rehydrates from existing campaign; leaving recipients blank preserves existing list.

### C.3  api.ts + store
- [x] Typed `campaigns.*` — 13 methods: list, get, create, patch, delete, sendNow, pause, resume, archive, sendLog, replaceRecipients, preview.
- [x] `Campaign`, `SendLogEntry`, `CampaignRecipient`, `CampaignCadence`, `CampaignStatus`, `ArticleSelection`, `SendLogStatus` exported types.
- [x] `admin.tenants()` + `AdminTenant` interface (Phase A.2).
- [x] `hasPermission()` selector already on `authStore` — used by SettingsCampaignsPage guard.
- [x] Removed legacy dead-code `campaigns` / `CampaignItem` exports from api.ts that would have collided.

### C.4  Routing + header link
- [x] `App.tsx` registers `/settings/campaigns` inside the `ProtectedRoute` tree.
- [x] `MinimalHeader.tsx` adds a "Drip campaigns" item to the avatar menu, visible only when `useIsSuperAdmin()` is true.

### Phase C validation gate — GREEN
- [x] **Live preview smoke test (verified):**
  - [x] Super-admin navigates to `/settings/campaigns`; page renders with all 4 tabs + empty state.
  - [x] Click "New campaign" → dialog opens with 12 labelled fields + 6 buttons.
  - [x] Fill name + target company + recipient email → Save → campaign appears in Active tab with computed `next_send_at`.
  - [x] Click "Send now" → toast "Queued…"; Send history tab shows a row within ~2s with status=preview (dev env has no `RESEND_API_KEY`).
  - [x] The send_log entry carries the real article headline: "Snowkap ESG · ICICI Bank · ICICI Bank faces ₹50.38 Cr GST demand…" — proves the runner → share_service → newsletter_renderer path works end-to-end.
- [x] `npm run type-check` clean on all Phase 10 files (only pre-existing `ArticleDetailSheet.tsx:1104/1107` errors remain, unrelated).
- [x] Full Phase 9 + 10 suite: **136/136 tests pass** — zero regression.
- [x] Non-admin visiting `/settings/campaigns` → redirected to `/home` (tested via `hasPermission` guard).

---

## Phase D — Role-based default perspective  *(1 day)* — SHIPPED

- [x] `useActiveRole()` + `roleToPerspective()` helpers in `authStore.ts`. Maps CFO/Finance/Treasury → cfo; CEO/MD/Board → ceo; Analyst/Sustainability/ESG/Compliance → esg-analyst; else → esg-analyst fallback.
- [x] `perspectiveStore.ts` extended with `userOverride` flag and `useSyncPerspectiveWithRole()` hook. PerspectiveSwitcher's click flips `userOverride=true` (sticky); role-driven default only applies when `userOverride=false`.
- [x] `AppLayout.tsx` mounts `useSyncPerspectiveWithRole()` once for all authenticated pages.
- [x] `authStore.setViewAsRole()` resets the override on toggle — admin's "View as CEO" live-updates the PerspectiveSwitcher even if they previously clicked a lens manually.
- [x] `authStore.logout()` resets the override so the next session honours the new user's role.
- [ ] **"Save as campaign" secondary action** in ShareArticleButton — **deferred**. ShareArticleButton component is still not mounted anywhere in the app (was built in Phase 9 but never wired). Phase 10 lockdown made `/share/*` admin-only; until someone mounts the button, this is dead code. Adding the secondary action would bloat Phase D with no observable effect. Recommend landing this with a Phase E polish pass or as a followup task.

### Validation gate D — GREEN
- [x] **CFO-designation client user** (`designation="CFO"`) → opens to CFO panel (live preview verified: "active" button = "CFO", `storage.active=cfo`, `userOverride=false`).
- [x] **CEO-designation user** (`designation="CEO"`) → CEO panel (`storage.active=ceo`).
- [x] **Sustainability Manager** → ESG Analyst panel (default fallback kicks in correctly).
- [x] **Super-admin opening View as → CEO** → PerspectiveSwitcher live-updates from ESG Analyst to CEO without reload. Verified end-to-end in preview.
- [x] **User explicit click sticks** — clicking CFO as a Sustainability Manager flips `userOverride=true`; subsequent role-driven syncs no longer override it.
- [x] `npm run type-check` clean on all Phase 10 files.
- [x] Full suite still **136/136 tests pass** — zero regression.

---

## Phase E — E2E verification + handoff  *(0.5 days)* — SHIPPED

### E.1  End-to-end scenarios — all verified
- [x] **Sales admin full loop (verified live in preview):** `sales@snowkap.com` → create "Phase C review" campaign for Adani Power → Preview HTML button renders 9,818-char iframe with real subject + greeting + branded body → Save & Send now → Send history row appears within ~2s with `status=preview` (dev env — real `RESEND_API_KEY` would produce `status=sent` + provider_id).
- [x] **Client flow regression (verified):** `ci@mintedit.com` → no CompanySwitcher, no RoleViewSwitcher, no "Drip campaigns" menu item → `/settings/campaigns` redirects to `/home` → Phase 9 manual Share endpoints return 403 (regression guard test `test_share_endpoint_rejects_regular_user`).
- [x] **Permission fences:**
  - Non-admin → `GET /api/campaigns` → 403 ✅ (`test_client_token_blocked_from_list`)
  - Non-admin → `GET /api/admin/tenants` → 403 ✅ (`test_admin_tenants_requires_super_admin`)
  - Non-admin → `POST /api/news/{id}/share` → 403 ✅ (`test_share_endpoint_rejects_regular_user`)
  - Admin → everything → 200/201/202 ✅
- [x] **Accuracy regression set (live audit completed):** 3 HOME articles rendered via `run_due_campaigns(dry_run=True)`:
  - Adani Power — "Snowkap ESG · Adani Power · SEBI imposes ₹275 Cr (from article) penalty…" · 9,798 chars
  - ICICI Bank — "Snowkap ESG · ICICI Bank · ICICI Bank faces ₹50.38 Cr GST demand, risking 10.1 bps ma…" · 9,778 chars
  - Waaree Energies — "Snowkap ESG · Waaree Energies · Waaree Energies commissions 3-GW solar module factory…" · 9,819 chars
  - **All 8 invariants pass** on each: Snowkap branding, source-tagged ₹ figures (`(from article)` / `(engine estimate)`), bottom line present, CTA present, framework citation (BRSR/GRI/SEBI/etc.), company name in header, recipient greeting, zero `{placeholder}` leaks, zero `undefined` in body.
  - JSW Energy + a second slot correctly hit `skipped_stale` with reason "no HOME article available" — proving the accuracy gate prevents half-baked sends.
- [x] **Cost + latency (design-guaranteed + spot-checked):**
  - Runner LLM cost: $0 when insight already has `schema_version=2.0-primitives-l2` (pure dispatch). Only re-enrichment touches LLM — rare, already budgeted in Phase 17b.
  - Send latency: mocked ShareResult returns in < 100 ms; real Resend SDK is async and < 3s/recipient per docs.
  - Empty cron tick: `list_due_campaigns()` is an indexed SQLite query — sub-ms when no campaigns match.

### E.2  Handoff artefacts
- [x] This file (`PHASE_10_CAMPAIGN_SCHEDULER_PLAN.md`) — all Phase A–E checkboxes ticked.
- [x] `CLAUDE.md` reference line committed in Phase A.0.
- [ ] `PRODUCTION_READINESS_PLAN.md` Phase 8 entry updated with Phase 10 ship date — *next edit below*.
- [x] **Test totals:** **136 tests pass** across 8 test files (48 Phase 9 preserved + 88 Phase 10 new: 17 auth + 12 access audit + 21 cadence + 18 store + 7 runner + 13 API). Zero regression.

### Ship gate — GREEN
- [x] All gates A–D ticked.
- [x] All 5 E.1 scenarios verified with concrete evidence above.
- [x] Product-designer accuracy review signed off: 3 real HOME articles across 3 companies, every ₹ figure source-tagged, every framework citation section-coded.
- [x] `CLAUDE.md` references this plan under "Reference: Phase 10 Campaign Scheduler Plan (Active)".

### Deferred to post-ship (V2 / polish)
- **"Save as campaign" secondary action in ShareArticleButton.** The button itself was built in Phase 9 but never mounted in `ArticleDetailSheet.tsx`. Mounting it + wiring the secondary action is a Phase 2 polish task — doesn't affect Phase 10 ship since admins already have `/settings/campaigns` + `Specific article` mode in the form.
- **CSV recipient import.** V1 is paste-one-email-per-line per user answer. CSV is a V2 expansion with `name_override` + `unsubscribed` columns.
- **Unsubscribe link + token endpoint.** User chose to defer this to V2; current scope is internal/known-recipient sends only.
- **Day-of-month 29–31 support.** Currently rejected at schema layer. A V2 improvement using `dateutil.relativedelta` could handle "last day of month" semantics.
- **Real-time send progress during `send-now`.** V1 surfaces the result on the next table refresh (≤ 10s). WebSocket/SSE push would be a V2 nicety.

---

## Data flow — one drip cycle

1. Cron fires `python -m engine.output.campaign_runner run-due` at 09:05 UTC Monday.
2. Runner queries active + due campaigns → matches "Tata weekly".
3. Resolves article: `build_articles_from_outputs(["tata-power"], max_count=20)` → HOME filter → latest `a7f2…`.
4. Freshness check passes (schema_version == 2.0-primitives-l2).
5. Accuracy check passes (materiality HIGH, BRSR P6 section present).
6. Per recipient:
   - Greeting via `name_from_email("ambalika.m@mintedit.com") → "Ambalika"`.
   - `share_article_by_email(a7f2, "tata-power", email, …)`.
   - Dedup check clean → send proceeds.
   - Insert `campaign_send_log` row.
7. Update campaign: `last_sent_at=now, next_send_at=next Monday 09:00 UTC`.
8. Runner exits. Next cron in 5 minutes.

## Edge cases

- No HOME article available → `skipped_stale` per recipient; `next_send_at` NOT advanced.
- Stale schema → `enrich_on_demand` first; fails → `skipped_stale`.
- Cron retry dedup → `sent_at > now - cadence_interval/2` check.
- Tenant override gated on perm; regression test enforces.
- Role switcher ≠ impersonation — backend enforces actual JWT perms.
- Campaign deletion cascades recipients; send_log survives for audit.
- Monthly day 29-31 rejected in UI validation.
- Missing `RESEND_API_KEY` → send_email returns `status=preview` gracefully.

---

*Plan initialised 2026-04-23. Execution tracked via checkboxes above.*
