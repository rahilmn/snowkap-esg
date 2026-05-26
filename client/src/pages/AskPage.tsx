/**
 * POW-5 — Ask tab (mobile-first chat shell, Power of Now aesthetic).
 *
 * Mounts at `/ask`. Replaces the desktop-grade PersistentChatPage
 * (which stays reachable at `/chat` for power users) with a clean
 * bubble UI matching the Power of Now reference design.
 *
 * Reuses:
 *   - `useChatStream` (Phase C SSE backend, no change)
 *   - All 14 MCP tools (intelligence-forecast, memory-recall, etc.)
 *   - Conversation persistence via `chat_conversations` + `chat_messages`
 *
 * Context-aware seeds (per docs/POWER_OF_NOW_ARCHITECTURE.md §14):
 *   - `?article={id}`                       → article-only context
 *   - `?article={id}&include_comments=true` → article + comment thread
 *   - `?article={id}&include_comments=true&focus_comment={cid}`
 *                                           → reply-assist mode
 *   - no params                             → cold empty-state chips
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";

import { useChatStream } from "@/hooks/useChatStream";
import { useAuthStore } from "@/stores/authStore";
import { IPhoneFrame } from "@/components/ui/IPhoneFrame";
import { TOKENS } from "@/lib/designTokensV2";

interface ChatBubble {
  role: "user" | "assistant";
  content: string;
}

export function AskPage() {
  const firstName = useAuthStore((s) => (s.name || "").split(" ")[0] || "there");
  const companyId = useAuthStore((s) => s.companyId);
  const prettyCompany = (companyId || "your company").replace(/-/g, " ");

  const [searchParams, setSearchParams] = useSearchParams();
  // Read URL params ONCE on mount into stable state. The chipSuggestions
  // memo reads from these state values, not the live URL params, so the
  // one-shot URL clean-up below doesn't strip the context away before
  // the chips render.
  const [ctxArticle] = useState<string | null>(() => searchParams.get("article"));
  const [ctxIncludeComments] = useState<boolean>(() => searchParams.get("include_comments") === "true");
  const [ctxFocusComment] = useState<string | null>(() => searchParams.get("focus_comment"));
  // Forum v1.1 — `?forum_thread={id}` opens /ask with a seed prompt that
  // primes the LLM to dispatch the `forum-thread` MCP tool.
  const [ctxForumThread] = useState<string | null>(() => searchParams.get("forum_thread"));
  // Wiki v1.1 — `?wiki=true` opens /ask with the user's bookmark
  // library pre-loaded into the system prompt server-side.
  const [ctxWiki] = useState<boolean>(() => searchParams.get("wiki") === "true");

  const [draft, setDraft] = useState("");
  const seededRef = useRef<boolean>(false);
  const { messages, status, send } = useChatStream();

  // Auto-seed when entered from an article OR forum-thread context.
  // One-shot — clears the URL params after the first send so refreshing
  // doesn't re-fire.
  useEffect(() => {
    if (seededRef.current) return;
    if (!ctxArticle && !ctxForumThread && !ctxWiki) return;
    seededRef.current = true;
    let seed: string;
    if (ctxWiki) {
      seed = `Summarise what I've saved this week for ${prettyCompany}. Highlight patterns, recurring themes, and gaps in my reading list.`;
    } else if (ctxForumThread) {
      seed = `Summarise the Forum discussion on thread ${ctxForumThread} for ${prettyCompany}. Reference specific replies by author + company stance, then suggest a thoughtful reply I could post.`;
    } else if (ctxFocusComment) {
      // POW-5c — NEVER put the raw IDs in the user-visible message.
      // The backend reads `focus_comment_id` + `article_id` separately
      // and pre-loads the targeted comment into the system prompt, so
      // the LLM knows exactly which comment to address by author name.
      seed = `Draft a thoughtful reply to the comment I'm focused on. Engage directly with what they said, ground it in the article's facts, and keep it 60-110 words — ready to paste, no preamble.`;
    } else if (ctxIncludeComments) {
      seed = `Summarise the discussion on this article for ${prettyCompany} — name the commenters, their stances, and where peers agree or disagree.`;
    } else {
      seed = `Tell me what this article means specifically for ${prettyCompany}.`;
    }
    setDraft(seed);
    // Clear the URL so we don't re-seed on refresh.
    setTimeout(() => {
      const next = new URLSearchParams(searchParams);
      next.delete("article");
      next.delete("include_comments");
      next.delete("focus_comment");
      next.delete("forum_thread");
      next.delete("wiki");
      setSearchParams(next, { replace: true });
    }, 0);
  }, [ctxArticle, ctxIncludeComments, ctxFocusComment, ctxForumThread, ctxWiki, prettyCompany, searchParams, setSearchParams]);

  const chipSuggestions = useMemo(() => {
    if (ctxWiki) {
      return [
        "What's the climate trend across my saved articles?",
        "Which bookmarks haven't I added a note to yet?",
        "Summarise this week's saves in 5 bullets",
      ];
    }
    if (ctxForumThread) {
      return [
        "Summarise the top 3 positions in this thread",
        `Suggest a reply that ties to ${prettyCompany}'s painpoints`,
        "Where do peers in other industries weigh in differently?",
      ];
    }
    if (ctxArticle && ctxFocusComment) {
      return [
        "Suggest a thoughtful response to this comment",
        "What are the strongest counter-arguments?",
        "Cite specific data from the article in my reply",
      ];
    }
    if (ctxArticle && ctxIncludeComments) {
      return [
        "What's the most useful thread on this article?",
        "Where do peers in my industry disagree?",
        "Summarise the top 3 positions in the discussion",
      ];
    }
    if (ctxArticle) {
      return [
        `Summarise this article for ${prettyCompany} in 30 seconds`,
        "What's the biggest ₹ exposure?",
        "What should I do about this in the next 30 days?",
      ];
    }
    return [
      `What's the highest-criticality article for ${prettyCompany} this week?`,
      "What's the latest on BRSR compliance?",
      `Compare ${prettyCompany} to its 3 closest peers`,
      "What changed in my Wiki this week?",
    ];
  }, [ctxArticle, ctxIncludeComments, ctxFocusComment, ctxForumThread, ctxWiki, prettyCompany]);

  const handleSend = (text?: string) => {
    const msg = (text ?? draft).trim();
    if (!msg) return;
    send({
      conversation_id: null,
      message: msg,
      article_id: ctxArticle || undefined,
      company_slug: companyId || undefined,
      // Forum v1.1 — backend reads this to pre-load the forum thread
      // into the system prompt (see chat.py::_load_forum_thread_context).
      forum_thread_id: ctxForumThread || undefined,
      // Wiki v1.1 — backend reads this to pre-load the caller's
      // bookmark library (see chat.py::_load_wiki_context).
      wiki_context: ctxWiki || undefined,
      // POW-5c — backend reads these to pre-load the article's comment
      // thread (see chat.py::_load_article_comments_context). Without
      // this the LLM has no commenter names + bodies and falls back to
      // bracketed placeholder templates.
      include_comments: ctxIncludeComments || undefined,
      focus_comment_id: ctxFocusComment || undefined,
    });
    setDraft("");
  };

  return (
    <IPhoneFrame>
      <div style={{
        position: "absolute", inset: 0,
        display: "flex", flexDirection: "column",
        background: "#fff",
      }}>
        {/* Header */}
        <div style={{ padding: "14px 20px 6px", borderBottom: `1px solid ${TOKENS.line}` }}>
          <span style={{
            fontSize: 11, color: TOKENS.ink4, letterSpacing: "0.04em",
            fontWeight: 600, textTransform: "uppercase",
          }}>
            Ask Snowkap
          </span>
          <h1 className="serif" style={{
            margin: "2px 0 0", fontSize: 22, fontWeight: 500, color: TOKENS.ink,
            letterSpacing: "-0.015em",
          }}>
            How can I help, {firstName}?
          </h1>
        </div>

        {/* Conversation area. Bottom padding clears the absolutely-
            positioned input bar (~60px) + bottom nav (70px) so the
            last paragraph never gets hidden behind the chrome. */}
        <div className="app-scroll" style={{
          flex: 1, overflowY: "auto",
          padding: "12px 16px 160px",
          display: "flex", flexDirection: "column", gap: 12,
        }}>
          {messages.length === 0 && status === "idle" ? (
            <EmptyState chipSuggestions={chipSuggestions} onPick={(s) => { setDraft(s); handleSend(s); }} />
          ) : (
            messages.map((m, i) => (
              <Bubble key={i} role={m.role as "user" | "assistant"} content={m.content || ""} />
            ))
          )}
          {status === "streaming" && messages.length > 0 && (
            <div style={{ alignSelf: "flex-start", display: "flex", gap: 4, padding: "8px 12px" }}>
              <Dot delay={0}/><Dot delay={150}/><Dot delay={300}/>
            </div>
          )}
        </div>

        {/* Input bar */}
        <div style={{
          position: "absolute", left: 0, right: 0, bottom: 70,
          padding: "10px 16px",
          background: "#fff", borderTop: `1px solid ${TOKENS.line}`,
          display: "flex", gap: 8, alignItems: "flex-end",
        }}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="Ask anything…"
            rows={1}
            style={{
              flex: 1, resize: "none",
              padding: "10px 14px",
              borderRadius: 14, border: `1px solid ${TOKENS.line}`,
              fontSize: 14, fontFamily: "inherit",
              maxHeight: 96, lineHeight: 1.4,
            }}
          />
          <button
            onClick={() => handleSend()}
            disabled={!draft.trim() || status === "streaming"}
            style={{
              background: draft.trim() && status !== "streaming" ? TOKENS.brand : TOKENS.line,
              color: draft.trim() && status !== "streaming" ? "#fff" : TOKENS.ink4,
              border: "none", borderRadius: 14,
              width: 44, height: 40,
              cursor: draft.trim() && status !== "streaming" ? "pointer" : "default",
              fontSize: 18,
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            ↑
          </button>
        </div>

        {/* Bottom nav — mirrors NowPage / WikiPage / ForumPage */}
        <div style={{
          position: "absolute", left: 0, right: 0, bottom: 0,
          height: 70,
          borderTop: `1px solid ${TOKENS.line}`,
          background: "#fff",
          display: "flex", alignItems: "center", justifyContent: "space-around",
        }}>
          {(["Now", "Forum", "Wiki", "Ask"] as const).map((label) => {
            const href = label === "Now" ? "/now"
              : label === "Wiki" ? "/wiki"
              : label === "Forum" ? "/forum"
              : "/ask";
            return (
              <a
                key={label}
                href={href}
                className="tap"
                style={{
                  fontSize: 11, fontWeight: 600,
                  color: label === "Ask" ? TOKENS.brand : TOKENS.ink4,
                  background: "transparent", border: "none", cursor: "pointer",
                  padding: "8px 14px", borderRadius: 10,
                  textDecoration: "none",
                }}
              >
                {label}
              </a>
            );
          })}
        </div>
      </div>
    </IPhoneFrame>
  );
}

function EmptyState({ chipSuggestions, onPick }: { chipSuggestions: string[]; onPick: (s: string) => void }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "stretch",
      gap: 10, paddingTop: 20,
    }}>
      <div style={{
        textAlign: "center", marginBottom: 16,
      }}>
        <span style={{ fontSize: 28 }}>✨</span>
        <p style={{
          margin: "8px 0 0", fontSize: 13, color: TOKENS.ink3, lineHeight: 1.5,
        }}>
          I have access to your live news feed, intelligence engine,<br/>
          peer benchmarks, and conversation memory.
        </p>
      </div>
      {chipSuggestions.map((s, i) => (
        <button
          key={i}
          onClick={() => onPick(s)}
          className="tap"
          style={{
            background: "#fafaf8", border: `1px solid ${TOKENS.line}`,
            borderRadius: 14, padding: "12px 14px",
            textAlign: "left", cursor: "pointer",
            fontSize: 13.5, color: TOKENS.ink2,
            fontFamily: "inherit",
            lineHeight: 1.4,
          }}
        >
          {s}
        </button>
      ))}
    </div>
  );
}

function Bubble({ role, content }: ChatBubble) {
  const isUser = role === "user";
  return (
    <div style={{
      alignSelf: isUser ? "flex-end" : "flex-start",
      maxWidth: "85%",
      background: isUser ? TOKENS.ink : "#fafaf8",
      color: isUser ? "#fff" : TOKENS.ink,
      borderRadius: 18,
      padding: "10px 14px",
      fontSize: 13.5, lineHeight: 1.5,
      boxShadow: isUser ? "none" : `0 1px 3px rgba(0,0,0,0.04)`,
      border: isUser ? "none" : `1px solid ${TOKENS.line}`,
      whiteSpace: "pre-wrap",
    }}>
      {isUser ? content : (
        <div style={{
          // ReactMarkdown wraps in <p> by default — flatten for chat bubbles
        }}>
          <ReactMarkdown>{content || "…"}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}

function Dot({ delay }: { delay: number }) {
  return (
    <span style={{
      width: 6, height: 6, borderRadius: 999,
      background: TOKENS.ink3,
      display: "inline-block",
      animation: `pon-pulse 1.4s infinite`,
      animationDelay: `${delay}ms`,
    }}/>
  );
}
