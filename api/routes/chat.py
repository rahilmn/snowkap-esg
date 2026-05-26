"""Phase C — SSE chat endpoint.

POST `/api/chat` with `{conversation_id?, message, signoff?}`. Returns
a `text/event-stream` response with the canonical 13 event types:

    stream_start | token | slash_command_parsed | tool_invocation
    tool_progress | tool_result | toulmin_chain | phase_k_tags
    stage_progress | advisor_hint | signoff_request | error | done

Phase 1 of the SSE path:
  * Lightweight LLM streaming via `OpenRouterClient.stream()` when
    `OPENROUTER_API_KEY` is set; falls through to a deterministic
    echo path when the LLM client can't initialise (so dev + tests
    work without keys).
  * Memory retrieval BEFORE the LLM call (`retrieve_for_injection`)
    injects top-N memories as system context.
  * MCP tool dispatch via `dispatch_tool` when the LLM emits a
    `tool_call`. Today the LLM doesn't emit tool calls in v1 — the
    plumbing is wired so a later prompt can.
  * Conversation persistence: user message + assistant message
    written at completion (NOT mid-stream — too noisy on the DB).

Out-of-scope for Phase 1 (deferred): function-calling, multi-turn
tool chains, advisor hint emission mid-stream.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth_context import get_bearer_claims
from engine.chat.conversations import ensure_conversation
from engine.chat.messages import (
    insert_assistant_message,
    insert_user_message,
    load_messages_for_llm,
)
from engine.memory.retrieval import retrieve_for_injection

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., min_length=1, max_length=8000)
    signoff: str | None = None
    # Phase 31 — when the user opens chat from the "Discuss this
    # article" button, the frontend passes the article id so the
    # backend can pre-load the deep insight (headline, risks, ₹
    # exposure, framework citations) into the system prompt. Without
    # this the LLM can only see the user's question and replies with
    # "I don't have the article content".
    article_id: str | None = None
    company_slug: str | None = None
    # POW-5c — when the user opens chat from the "✨ Help me reply" link
    # on a specific comment (or "💬 Ask about the discussion" on the
    # article sheet), the frontend passes `include_comments=true` so the
    # backend can pre-load the full comment thread (author + body +
    # timestamp + company attribution) into the system prompt. Without
    # this the LLM hallucinates commenter names + bracketed placeholder
    # templates like "[summarize key point in comment X]".
    include_comments: bool = False
    # POW-5c — when set, the LLM is told to LEAD with a reply targeted at
    # this specific comment, while still grounding in the rest of the
    # thread. The id is consumed only on the backend (to find the focus
    # row in the thread); it never reaches the user-facing reply text.
    focus_comment_id: str | None = None
    # Forum v1.1 — same pattern but for forum threads. When the user
    # opens chat from "✨ Discuss this thread with AI" on /forum, the
    # frontend passes `forum_thread_id` so the backend can pre-load
    # the thread title + body + replies (with author + company
    # attribution) into the system prompt. Without this the LLM
    # hallucinates the discussion content.
    forum_thread_id: str | None = None
    # Wiki v1.1 — when true, backend loads the caller's bookmark
    # library (grouped by section, enriched with article titles +
    # criticality bands) into the system prompt. Triggered by the
    # "✨ Ask AI about my Wiki" CTA on /wiki, which deep-links into
    # /ask?wiki=true.
    wiki_context: bool = False


def _scope(claims: dict[str, Any]) -> tuple[str, str]:
    tenant = str(claims.get("tenant_slug") or claims.get("tenant") or "default")
    user = str(claims.get("sub") or "anonymous")
    return tenant, user


def _format_sse(event: str, data: Any) -> str:
    """Encode one SSE message (event + data)."""
    body = json.dumps(data, default=str)
    return f"event: {event}\ndata: {body}\n\n"


async def _stream_chat(
    *,
    request: ChatRequest,
    tenant: str,
    user: str,
) -> AsyncIterator[str]:
    """Yield SSE event strings for one chat turn."""

    # 1. ensure conversation + persist user message
    conversation_id = ensure_conversation(
        conversation_id=request.conversation_id,
        tenant_id=tenant, user_id=user,
        title_seed=request.message[:80],
    )
    insert_user_message(
        conversation_id=conversation_id,
        tenant_id=tenant, user_id=user,
        content=request.message,
    )

    yield _format_sse("stream_start", {
        "conversation_id": conversation_id,
        "tenant": tenant, "user": user,
    })

    # 2. retrieve relevant memories (best-effort)
    try:
        memories = retrieve_for_injection(
            tenant_id=tenant, user_id=user, query=request.message, top_n=5,
        )
    except Exception:  # noqa: BLE001 — never let memory failure break chat
        memories = []
    if memories:
        yield _format_sse("phase_k_tags", {
            "memory_count": len(memories),
            "memory_kinds": list({m.fact_kind for m in memories}),
        })

    # 3. load conversation history (for LLM context)
    history = load_messages_for_llm(
        conversation_id=conversation_id, tenant_id=tenant, user_id=user,
    )

    # Phase 31 — when the chat carries article context, pre-load the
    # article's deep insight + summary into the system prompt so the
    # LLM has something concrete to reason about. Without this it
    # answers every "what should I know" question with "give me the
    # article" because the only thing in `history` is the user's bare
    # question.
    article_context_text: str | None = None
    if request.article_id:
        try:
            article_context_text = _load_article_context(
                article_id=request.article_id,
                company_slug=request.company_slug,
            )
        except Exception as exc:  # noqa: BLE001 — context is best-effort
            logger.warning(
                "chat: article-context load failed for %s: %s",
                request.article_id, exc,
            )

    # POW-5c — when the chat carries article context AND the user opened
    # from "✨ Help me reply" / "💬 Ask about the discussion", load the
    # comment thread so the LLM grounds its reply in actual commenter
    # names + bodies (no placeholder templates, no hallucinated authors).
    comments_context_text: str | None = None
    if request.article_id and request.include_comments:
        try:
            comments_context_text = _load_article_comments_context(
                article_id=request.article_id,
                focus_comment_id=request.focus_comment_id,
            )
        except Exception as exc:  # noqa: BLE001 — context is best-effort
            logger.warning(
                "chat: article-comments context load failed for %s: %s",
                request.article_id, exc,
            )

    # Forum v1.1 — when the chat carries forum_thread context, pre-load
    # the thread + replies + author-company attribution. Same shape as
    # article_context_text; appended to the system prompt below.
    forum_context_text: str | None = None
    if request.forum_thread_id:
        try:
            forum_context_text = _load_forum_thread_context(
                thread_id=request.forum_thread_id,
            )
        except Exception as exc:  # noqa: BLE001 — context is best-effort
            logger.warning(
                "chat: forum-thread context load failed for %s: %s",
                request.forum_thread_id, exc,
            )

    # Wiki v1.1 — when the chat carries `wiki_context=true`, pre-load
    # the caller's bookmark library (grouped by section, enriched with
    # article titles + criticality bands) into the system prompt. Per-
    # user — the `user` here is the JWT sub claim, so this never leaks
    # across tenants.
    wiki_context_text: str | None = None
    if request.wiki_context:
        try:
            wiki_context_text = _load_wiki_context(viewer_email=user)
        except Exception as exc:  # noqa: BLE001 — context is best-effort
            logger.warning(
                "chat: wiki-context load failed for %s: %s",
                user, exc,
            )

    # 4. attempt to stream from OpenRouter; fall back to deterministic echo
    response_text = ""
    model_used = "deterministic-echo"
    try:
        from engine.llm import get_llm_client

        client = get_llm_client(task_class="chat")
        system_msg = {
            "role": "system",
            "content": _build_system_prompt(
                memories, tenant, user,
                article_context_text, forum_context_text, wiki_context_text,
                comments_context_text,
            ),
        }
        for token_event in client.stream(
            messages=[system_msg, *history], temperature=0.7,
        ):
            if token_event.delta:
                response_text += token_event.delta
                yield _format_sse("token", {"delta": token_event.delta})
            # `TokenEvent.finish_reason` is non-None on the last chunk
            # (typically "stop"). The earlier `.done` access was a typo
            # against a field that doesn't exist on the dataclass —
            # every chat turn raised AttributeError and silently fell
            # back to the deterministic echo. Live since Phase 27.
            if token_event.finish_reason:
                model_used = token_event.model or model_used
    except Exception as exc:  # noqa: BLE001 — fall through to echo
        # Visible log so an operator can diagnose why chat is falling
        # back to the canned echo instead of streaming a real response.
        logger.exception(
            "chat: LLM stream failed (%s) — falling back to deterministic echo",
            type(exc).__name__,
        )
        response_text = _deterministic_echo(request.message, memories)
        for chunk in _chunk_text(response_text, size=64):
            yield _format_sse("token", {"delta": chunk})
        model_used = f"fallback:{type(exc).__name__}"

    # 5. persist assistant message + close stream
    insert_assistant_message(
        conversation_id=conversation_id,
        tenant_id=tenant,
        content=response_text,
        model_used=model_used,
        finish_reason="stop",
    )
    yield _format_sse("done", {
        "conversation_id": conversation_id,
        "model_used": model_used,
        "chars": len(response_text),
    })


def _load_article_context(
    *,
    article_id: str,
    company_slug: str | None = None,
) -> str | None:
    """Fetch the article + deep insight from disk and return a compact
    LLM-friendly briefing. Returns None when the article isn't indexed
    (live-fetched headline that hasn't been bootstrapped yet)."""
    from engine.index.sqlite_index import get_by_id
    import json as _json
    from pathlib import Path as _Path

    row = get_by_id(article_id)
    if not row:
        return None
    slug = (row.get("company_slug") or company_slug or "").strip().lower()
    parts: list[str] = []
    parts.append(f"ARTICLE TITLE: {row.get('title') or '(untitled)'}")
    parts.append(f"COMPANY: {slug or 'unknown'}")
    if row.get("source"):
        parts.append(f"SOURCE: {row.get('source')}")
    if row.get("published_at"):
        parts.append(f"PUBLISHED: {row.get('published_at')}")
    if row.get("tier"):
        parts.append(f"TIER: {row.get('tier')}")
    if row.get("criticality_band"):
        parts.append(f"CRITICALITY: {row.get('criticality_band')}")

    # Pull the on-disk insight if present — gives the LLM the deep
    # analysis (headline, financial exposure, key risks, role
    # explainer) without having to re-derive any of it.
    jp = row.get("json_path")
    if jp:
        try:
            p = _Path(jp)
            if p.exists():
                payload = _json.loads(p.read_text(encoding="utf-8"))
                ins = payload.get("insight") or {}
                if ins.get("headline"):
                    parts.append(f"DEEP HEADLINE: {ins['headline']}")
                if ins.get("core_mechanism"):
                    parts.append(f"CORE MECHANISM: {ins['core_mechanism']}")
                if ins.get("criticality_summary"):
                    parts.append(f"CRITICALITY SUMMARY: {ins['criticality_summary']}")
                # Compact role explainer (no role-filter; the LLM gets
                # all 3 lenses + uses the one matching the user's role).
                re_block = ins.get("role_explainer") or {}
                for role in ("cfo", "ceo", "esg-analyst"):
                    rb = re_block.get(role) or {}
                    line = (rb.get("how_it_impacts_business") or "").strip()
                    if line:
                        parts.append(f"{role.upper()} IMPACT: {line[:280]}")
                # Financial timeline
                ft = ins.get("financial_timeline") or {}
                immediate = (ft.get("immediate") or {}) if isinstance(ft, dict) else {}
                if immediate.get("inr_cr") is not None:
                    parts.append(
                        f"FINANCIAL EXPOSURE (immediate): ₹{immediate.get('inr_cr')} Cr"
                    )
                # Top frameworks
                fwm = payload.get("pipeline", {}).get("frameworks", [])[:3]
                fw_codes = [
                    fm.get("framework_id") or fm.get("id")
                    for fm in fwm
                    if isinstance(fm, dict)
                ]
                fw_codes = [c for c in fw_codes if c]
                if fw_codes:
                    parts.append(f"FRAMEWORKS: {', '.join(fw_codes)}")
                # Top recommendation
                recs = (payload.get("recommendations") or {}).get("recommendations", [])[:3]
                if recs and isinstance(recs[0], dict):
                    parts.append(
                        f"TOP RECOMMENDATION: {recs[0].get('title') or recs[0].get('headline') or ''}"
                    )

                # Phase 31 — methodology blocks. Lets the LLM answer
                # "how did you calculate the ₹9,685 Cr?" or "what's the
                # formula behind the margin bps?" by walking through the
                # primitive-cascade math, the role weights, the
                # framework matchers, etc. Without this the LLM can only
                # quote the final numbers without explaining provenance.
                try:
                    from engine.analysis.methodology_provenance import build_methodology
                    meth = build_methodology(ins) or {}
                    methodology_lines: list[str] = []
                    for panel_id in (
                        "criticality", "relevance", "financial_timeline",
                        "impact_analysis", "risk_matrix", "ai_recommendations",
                    ):
                        block = meth.get(panel_id)
                        if not isinstance(block, dict):
                            continue
                        src = block.get("source") or ""
                        logic = block.get("simple_logic") or ""
                        formula = block.get("formula_human") or ""
                        inputs = block.get("your_inputs") or {}
                        if not (src or logic):
                            continue
                        methodology_lines.append(
                            f"  - {panel_id}:\n"
                            f"      source: {src}\n"
                            f"      logic: {logic[:240]}\n"
                            f"      formula: {formula[:200]}\n"
                            f"      this article's inputs: "
                            f"{', '.join(f'{k}={v}' for k, v in list(inputs.items())[:6])}"
                        )
                    if methodology_lines:
                        parts.append("METHODOLOGY (how each number is computed):")
                        parts.extend(methodology_lines)
                except Exception:  # noqa: BLE001 — methodology is additive
                    pass
        except Exception:  # noqa: BLE001 — context is best-effort
            pass

    return "\n".join(parts) if parts else None


def _load_article_comments_context(
    *,
    article_id: str,
    focus_comment_id: str | None = None,
) -> str | None:
    """POW-5c — Fetch an article's comment thread, formatted as an
    LLM-friendly briefing for the chat system prompt.

    Same shape as `_load_forum_thread_context()`: one fact per line so
    the LLM can quote specific commenters by name + their actual words.
    When `focus_comment_id` is set, that comment is highlighted (the LLM
    is told to LEAD with a reply targeted at it). Internal IDs are NEVER
    surfaced to the LLM — only display names, bodies, and timestamps —
    so the model can't echo opaque hash strings back to the user.

    Returns None when the article has no visible comments.
    """
    if not article_id:
        return None
    from engine.models import article_comments as _ac

    rows = _ac.list_comments(article_id, viewer_email=None)
    visible = [r for r in rows if r.deleted_at is None]
    if not visible:
        return None

    # Build a lookup of replies grouped by parent so we can render the
    # 1-level threaded shape the UI uses.
    top_level = [r for r in visible if not r.parent_id]
    replies_by_parent: dict[str, list[Any]] = {}
    for r in visible:
        if r.parent_id:
            replies_by_parent.setdefault(r.parent_id, []).append(r)

    parts: list[str] = []
    parts.append(f"COMMENT COUNT (visible): {len(visible)}")

    focus_row = None
    if focus_comment_id:
        focus_row = next((r for r in visible if r.id == focus_comment_id), None)
        if focus_row is not None:
            # Surface the focus comment FIRST so the LLM anchors its
            # reply on it. We deliberately do NOT name the comment id —
            # the LLM gets only the author + body so it can never quote
            # back an opaque hash string to the end user.
            focus_body = (focus_row.body or "").strip()[:600]
            parts.append("─── FOCUS COMMENT (draft your reply targeted at this) ───")
            parts.append(f"AUTHOR: {focus_row.author_name}")
            parts.append(f"POSTED: {focus_row.created_at}")
            parts.append(f"BODY: {focus_body}")
            parts.append("──────────────────────────────────────────────────────")

    parts.append("FULL THREAD (chronological, author + body — never quote internal IDs back):")
    for idx, c in enumerate(top_level, start=1):
        body = (c.body or "").strip()[:400]
        marker = "  ★" if (focus_row and c.id == focus_row.id) else "  ─"
        parts.append(f"{marker} [{idx}] {c.author_name} @ {c.created_at}: {body}")
        for r in replies_by_parent.get(c.id, []):
            r_body = (r.body or "").strip()[:300]
            r_marker = "      ★" if (focus_row and r.id == focus_row.id) else "      ↳"
            parts.append(f"{r_marker} {r.author_name} @ {r.created_at}: {r_body}")

    return "\n".join(parts) if parts else None


def _load_forum_thread_context(*, thread_id: str) -> str | None:
    """Forum v1.1 — Fetch a forum thread + its replies, formatted as an
    LLM-friendly briefing for the chat system prompt.

    Returns None when the thread is missing or has been soft-deleted.
    Mirrors `_load_article_context()` in shape: one fact per line so the
    LLM can quote specific replies by author + company without
    hallucinating commenter names.

    See: docs/POWER_OF_NOW_ARCHITECTURE.md §14.1 (forum-thread context type).
    """
    if not thread_id:
        return None
    from engine.models import forum_threads as _ft
    from engine.index import tenant_registry as _tr

    thread = _ft.get_thread(thread_id)
    if thread is None or thread.deleted_at is not None:
        return None

    def _author_company(email: str | None) -> str | None:
        if not email or "@" not in email:
            return None
        domain = email.split("@", 1)[1].lower()
        try:
            tenant = _tr.get_tenant_by_domain(domain)
        except Exception:  # noqa: BLE001
            return None
        if tenant:
            return tenant.get("name") or tenant.get("slug")
        return None

    parts: list[str] = []
    parts.append(f"THREAD ID: {thread.id}")
    parts.append(f"TAG: {thread.tag}")
    if thread.pinned:
        parts.append("PINNED: true (welcome / starter thread)")
    parts.append(f"TITLE: {thread.title}")
    a_company = _author_company(thread.author_email) or "(unknown company)"
    parts.append(f"OPENED BY: {thread.author_name} <{thread.author_email}> ({a_company})")
    parts.append(f"BODY: {thread.body}")

    replies = _ft.list_replies(thread.id)
    visible_replies = [r for r in replies if r.deleted_at is None]
    parts.append(f"REPLY COUNT: {len(visible_replies)}")
    if visible_replies:
        parts.append("REPLIES (chronological, author + company attribution):")
        for idx, r in enumerate(visible_replies, start=1):
            r_company = _author_company(r.author_email) or "(unknown company)"
            # Trim to a reasonable per-reply length so a 50-reply thread
            # doesn't blow the system prompt budget. 280 chars is enough
            # for the "tweet-length" summaries forum replies tend to be.
            body = (r.body or "").strip()[:280]
            parts.append(
                f"  [{idx}] {r.author_name} ({r_company}) @ {r.created_at}: {body}"
            )
    return "\n".join(parts) if parts else None


def _load_wiki_context(*, viewer_email: str) -> str | None:
    """Wiki v1.1 — Fetch the caller's bookmark library, formatted as an
    LLM-friendly briefing for the chat system prompt.

    Mirrors `_load_forum_thread_context()` — one fact per line so the
    LLM can quote specific bookmarked articles without hallucinating
    titles. Bookmarks are listed grouped by section (pinned / climate /
    capital / social / custom) in newest-first order within each group.

    Returns None when the caller has zero bookmarks.

    See: docs/POWER_OF_NOW_ARCHITECTURE.md §14.1 (wiki context type).
    """
    if not viewer_email or "@" not in viewer_email:
        return None
    from engine.models import user_bookmarks as _ub
    from engine.index.sqlite_index import get_by_id

    rows = _ub.list_for_user(viewer_email)
    if not rows:
        return None

    parts: list[str] = []
    parts.append(f"VIEWER: {viewer_email}")
    parts.append(f"BOOKMARK COUNT: {len(rows)}")

    # Group by section in the canonical order.
    sections_order = ("pinned", "climate", "capital", "social", "custom")
    grouped: dict[str, list[Any]] = {s: [] for s in sections_order}
    for r in rows:
        grouped.setdefault(r.section if r.section in sections_order else "custom", []).append(r)

    for section in sections_order:
        items = grouped.get(section) or []
        if not items:
            continue
        parts.append(f"SECTION {section.upper()} ({len(items)} bookmark(s)):")
        for idx, r in enumerate(items, start=1):
            article_row = None
            try:
                article_row = get_by_id(r.article_id)
            except Exception:  # noqa: BLE001
                article_row = None
            title = (article_row or {}).get("title") or "(article not in index)"
            crit_band = (article_row or {}).get("criticality_band") or "—"
            note = (r.note or "").strip()
            line = (
                f"  [{idx}] {title[:140]} "
                f"(criticality: {crit_band}, saved: {r.bookmarked_at})"
            )
            parts.append(line)
            if note:
                # Notes are user-authored — preserve them verbatim,
                # truncated to ~280 chars so a single long note doesn't
                # blow the prompt budget.
                parts.append(f"      note: {note[:280]}")
    return "\n".join(parts) if parts else None


def _build_system_prompt(
    memories,
    tenant: str,
    user: str,
    article_context: str | None = None,
    forum_context: str | None = None,
    wiki_context: str | None = None,
    comments_context: str | None = None,
) -> str:
    # Tightened so the LLM stops announcing tool calls it can't actually
    # execute. Function-calling integration ships separately; today the
    # chat path is a single-turn streaming completion with memory
    # context. If we let the LLM SAY "calling `intelligence-forecast`…"
    # it looks broken because no call ever fires.
    parts = [
        # Phase 32 — single, horizontally-consumable voice. No role lens.
        # The article-detail page now renders one unified 4-bullet analysis
        # consumable by anyone in the company (CFO, CEO, GC, Head of
        # Sustainability, board member). The chat voice must match: lead
        # with the event + ₹ exposure + the obligation, NOT with a role
        # framing. If the user identifies their role in-conversation, you
        # may emphasise the angle that matters to them — but never label
        # the response with a 'CFO lens / CEO lens / Analyst lens' header.
        "You are a Snowkap ESG advisor. Write one tight, decision-ready "
        "answer per turn, grounded in the unified 4-bullet analysis (What "
        "changed · Why it matters · What it triggers · What to watch). "
        "Lead with the event, the ₹ exposure when known, and the binding "
        "obligation — in that order. Do NOT label your answer with a role "
        "lens; the brief is meant to be readable by anyone in the company.",
        # Strict tool-call discipline. The LLM has NO tool-execution
        # capability in this chat turn — function-calling is not wired.
        # If the user asks you to 'call intelligence-forecast' or any
        # named tool, IGNORE the call request and answer with the data
        # you already have (article context + memory + conversation
        # history). Do NOT echo back 'Now calling X', do NOT write
        # '_one moment while I fetch…_', do NOT promise output that
        # will arrive 'shortly'. Either give the answer now or say
        # plainly which panel in the app has the data.",
        "TOOL-CALL DISCIPLINE: You have NO live tool execution. Even if "
        "the user explicitly asks you to call a named tool (e.g. "
        "`intelligence-forecast`, `memory-recall`), do NOT announce, "
        "promise, or pretend to call it. Either answer from the article "
        "context + memory you already have, or point the user to the "
        "specific UI panel that contains the data (e.g. 'open the "
        "Financial Impact panel for ₹ exposure', 'see the 3-year "
        "trajectory in the What-to-watch bullet').",
        "Ground every claim in the article context + conversation "
        "history. If a number isn't in your context, say so plainly and "
        "name the panel that has it.",
        # Forward-looking framing: 3/6/12-month projections must carry a
        # plain-English disclosure so the user doesn't mistake LLM
        # extrapolation for engine-grounded forecasts.
        "When asked for a 3 / 6 / 12-month outlook (or any forward-looking "
        "projection), preface that section with a short disclosure such as "
        "'_The following is informed extrapolation, not an engine forecast — "
        "the article only flags an immediate signal._' Engine-computed "
        "numbers (₹ exposure, margin bps, P/E compression) you can quote as "
        "facts when they're in the ARTICLE CONTEXT block above; everything "
        "else in the outlook section is scenario reasoning.",
        # Methodology Q&A: the article context now carries a METHODOLOGY
        # block with `source`, `simple_logic`, `formula_human`, and the
        # actual per-article inputs for each metric (criticality, "
        # relevance, financial_timeline, impact_analysis, risk_matrix, "
        # ai_recommendations). When the user asks 'how did you calculate "
        # X' / 'what's the formula' / 'walk me through the logic', "
        # answer using THAT block — name the engine module, quote the "
        # formula, show the inputs that produced the number.",
        "When the user asks how something was calculated (e.g. 'how did "
        "you get ₹9,685 Cr?', 'walk me through the cascade', 'what's the "
        "formula for criticality?'), pull from the METHODOLOGY block in "
        "your article context above. Quote the engine module path, the "
        "simple_logic sentence, the formula, and this article's actual "
        "inputs. Do NOT invent a methodology that isn't in that block.",
        "Use plain markdown for structure: short paragraphs, `**bold**` "
        "for key terms, bullet lists for action items. Avoid horizontal "
        "rules (`---`) and section headers heavier than `###`.",
        f"Active tenant: {tenant}. User: {user}.",
    ]
    if memories:
        parts.append("Known facts about this user (memory-injected):")
        for m in memories[:5]:
            parts.append(f"  - [{m.fact_kind}] {m.content}")
    if article_context:
        parts.append(
            "ARTICLE CONTEXT (the user is currently viewing this analysis — "
            "ground every answer in these facts, do NOT ask the user to "
            "paste the article):\n"
            + article_context
        )
    if forum_context:
        parts.append(
            "FORUM THREAD CONTEXT (the user opened chat from a /forum "
            "thread — every fact below comes from the actual thread + "
            "its replies. Reference specific repliers by their display "
            "name + company. Do NOT invent commenter names or claims "
            "that aren't in the block below. If the user asks for a "
            "summary or reply suggestion, ground it in this block only):\n"
            + forum_context
        )
    if wiki_context:
        parts.append(
            "WIKI CONTEXT (the user's personal bookmark library — every "
            "title and note below is real. Reference specific bookmarks "
            "by their article title and the section they're filed under. "
            "Do NOT invent bookmark titles. If the user asks for a "
            "summary, recurring themes, or gaps in their Wiki, ground "
            "your answer in this block only):\n"
            + wiki_context
        )
    if comments_context:
        # POW-5c — REPLY-ASSIST DISCIPLINE. The previous build of this
        # path had no comment data, so the LLM produced bracketed
        # placeholders like "[summarize the key point in comment X]" and
        # echoed internal IDs back at the user. The rules below close
        # both failure modes.
        parts.append(
            "COMMENT THREAD CONTEXT (the user opened chat to discuss or "
            "reply to comments on this article — every author name + "
            "body below is real):\n"
            + comments_context
            + "\n\nREPLY-ASSIST RULES (strict, non-negotiable):\n"
            "  1. NEVER mention internal IDs (e.g. comment hash strings, "
            "article ids like 'e9ca7d...'). Refer to commenters by "
            "their display name only.\n"
            "  2. NEVER use bracketed placeholder syntax such as "
            "'[summarize key point]', '[your perspective here]', "
            "'[specific risk]'. If you don't have the substance to fill "
            "a slot, omit it.\n"
            "  3. When a FOCUS COMMENT block is present, write a ready-"
            "to-paste reply (no preamble, no template, no instructions "
            "to the user) that directly engages that commenter's point. "
            "Keep it to 60-110 words.\n"
            "  4. When asked to 'summarise the discussion', name the "
            "actual commenters and their stances in 3-5 short bullets.\n"
            "  5. Never tell the user to 'open the Comments panel' or "
            "'review the thread' — you already have the thread above; "
            "use it."
        )
    return "\n\n".join(parts)


def _deterministic_echo(message: str, memories) -> str:
    """Fallback used when no LLM client is wired up.

    Useful for tests + dev environments without an OPENROUTER_API_KEY.
    """
    lead = "[fallback] "
    body = message.strip()
    if memories:
        body += f"\n\nNoted facts: {len(memories)} memories recalled."
    return lead + body


def _chunk_text(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


@router.post("")
def chat(
    body: ChatRequest,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> StreamingResponse:
    tenant, user = _scope(claims)

    async def _generator() -> AsyncIterator[str]:
        async for event in _stream_chat(request=body, tenant=tenant, user=user):
            yield event

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable reverse-proxy buffering
            "Connection": "keep-alive",
        },
    )
