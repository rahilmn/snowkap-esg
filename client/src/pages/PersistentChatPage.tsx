/**
 * Phase C — Persistent chat page (SSE + conversation sidebar + memory).
 *
 * Lives at `/chat`. Companion to the legacy `/agent` page, which uses
 * the old POST-based `agent.chat()` flow. This page:
 *
 *   - Streams responses via `useChatStream` (SSE)
 *   - Shows past conversations in a `ConversationSidebar`
 *   - Rehydrates messages from `GET /api/conversations/{cid}` on select
 *   - Renders `ToulminBadge` / `ToolInvocationCard` / `AdvisorHintCard`
 *     inline when the SSE stream carries the corresponding events
 *
 * Note: keeps a thin local message list rather than persisting through
 * Zustand — the conversation history IS the source of truth, and the
 * sidebar invalidates the React Query cache on every send so the list
 * stays fresh.
 */
import { useEffect, useState, useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";

import { ConversationSidebar } from "@/components/chat/ConversationSidebar";
import { ToulminBadge } from "@/components/chat/ToulminBadge";
import { ToolInvocationCard } from "@/components/chat/ToolInvocationCard";
import { AdvisorHintCard } from "@/components/chat/AdvisorHintCard";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { useChatStream } from "@/hooks/useChatStream";
import { conversations, news } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

export function PersistentChatPage() {
  const qc = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const { messages, status, send, conversationId } = useChatStream();

  // Phase 31 — welcome-state context for the "open chat without an
  // article" path. Surface the active company + role so the greeting
  // and suggested prompts read as if the advisor knows where the user
  // is sitting.
  const companyId = useAuthStore((s) => s.companyId);
  const designation = useAuthStore((s) => s.designation);
  const firstName = useAuthStore((s) => (s.name || "").split(" ")[0] || "there");
  const prettyCompany = (companyId || "your company").replace(/-/g, " ");
  const roleLabel = (() => {
    const d = (designation || "").toLowerCase();
    if (d.includes("cfo")) return "CFO";
    if (d.includes("ceo") || d.includes("chief executive")) return "CEO";
    if (d.includes("analyst")) return "ESG Analyst";
    return "ESG Analyst";
  })();
  // Three role-anchored suggestions. Click → primes the input + fires
  // immediately (one tap to start the conversation).
  const suggestedPrompts: string[] = (() => {
    if (roleLabel === "CFO") {
      return [
        `What's my top ₹ exposure across ${prettyCompany}'s recent ESG news?`,
        `Draft a 3-bullet board summary on this week's signals for ${prettyCompany}.`,
        `Which disclosure deadlines am I on for ${prettyCompany} right now?`,
      ];
    }
    if (roleLabel === "CEO") {
      return [
        `Brief me on ${prettyCompany}'s competitive ESG positioning this week.`,
        `Which signals could become board-level reputational risk in 90 days?`,
        `Compare ${prettyCompany} to its 3 closest peers on governance.`,
      ];
    }
    return [
      `What's the highest-criticality article for ${prettyCompany} this week?`,
      `Which frameworks (BRSR, GRI, TCFD, …) flag ${prettyCompany} right now?`,
      `Walk me through the methodology behind the criticality score.`,
    ];
  })();

  // Phase 28 / Feature 6 — article context plumbing.
  // When the user opens chat from ArticleDetailSheet, the URL carries
  // `?company={slug}&article={id}`. We auto-prime the input with a
  // contextual seed so the LLM can call `intelligence-forecast`,
  // `intelligence-competitors`, and `memory-recall` MCP tools against
  // the right company. The chat is only seeded ONCE per page mount
  // (tracked via `contextSeededRef`) so navigating between conversations
  // doesn't keep re-injecting the seed.
  const [searchParams, setSearchParams] = useSearchParams();
  const ctxCompany = searchParams.get("company");
  const ctxArticle = searchParams.get("article");
  const contextSeededRef = useRef<boolean>(false);

  // Rehydrate when an existing conversation is selected
  const historyQuery = useQuery({
    queryKey: ["conversation-history", activeId],
    queryFn: () => (activeId ? conversations.get(activeId) : null),
    enabled: !!activeId,
  });

  // Active conversation id = explicit selection OR the SSE conv id
  const effectiveId = activeId ?? conversationId;

  useEffect(() => {
    // After a successful turn, refresh the sidebar
    if (status === "done") {
      qc.invalidateQueries({ queryKey: ["conversations"] });
    }
  }, [status, qc]);

  // Phase 28 / Feature 6 — auto-seed the draft from article context.
  // The URL params arrive when the user clicks "Discuss this article"
  // on ArticleDetailSheet. We prime the input field (not auto-send) so
  // the user has a chance to edit before firing the LLM. After the
  // first prime we KEEP the URL params for ONE more render cycle so
  // the article-context banner stays visible — we only clear them
  // once the user has hit Send (status === "streaming").
  const [discussingArticle, setDiscussingArticle] = useState<{
    company: string;
    article: string;
    title?: string;
  } | null>(null);

  // Resolve the opaque article id to a human title so the banner +
  // seeded prompt can say "this article on YES Bank S&P CSA ranking"
  // instead of "article 481a3d243935f26f". One backend call, cached.
  const articleTitleQuery = useQuery({
    queryKey: ["article-title", ctxArticle],
    queryFn: async () => {
      if (!ctxArticle) return null;
      try {
        const r = await news.getAnalysisStatus(ctxArticle);
        const deep = r.analysis?.deep_insight as { headline?: string } | undefined;
        // Prefer the LLM-crafted deep_insight headline; fall back to
        // the raw article title surfaced by /news/feed.
        return (deep?.headline as string | undefined) || null;
      } catch {
        return null;
      }
    },
    enabled: !!ctxArticle,
    staleTime: 60 * 60 * 1000,
  });

  useEffect(() => {
    if (contextSeededRef.current) return;
    if (!ctxCompany && !ctxArticle) return;
    contextSeededRef.current = true;
    // Force a fresh conversation. Without this, hitting Send appended
    // to whichever conversation was previously selected — which is how
    // stale "I'm looking at article {hash}" turns from earlier sessions
    // kept resurfacing in the user's screenshot.
    setActiveId(null);
    setDiscussingArticle({
      company: ctxCompany || "",
      article: ctxArticle || "",
    });
    const pretty = ctxCompany ? ctxCompany.replace(/-/g, " ") : "this company";
    // Shorter, user-friendly seed without the article-id hash leaking
    // into the prompt. We update the seed again below once the article
    // title resolves.
    const seed = ctxArticle
      ? `What should I know about this article on ${pretty}? Surface the key risks for my role and the 3 / 6 / 12-month outlook.`
      : `Brief me on ${pretty} — current ESG positioning and active risks.`;
    setDraft(seed);
    const next = new URLSearchParams(searchParams);
    next.delete("company");
    next.delete("article");
    setSearchParams(next, { replace: true });
  }, [ctxCompany, ctxArticle, searchParams, setSearchParams]);

  // Once the title resolves, fold it into the banner state and rewrite
  // the seed prompt (only if the user hasn't already started typing).
  const titleResolved = articleTitleQuery.data;
  useEffect(() => {
    if (!titleResolved || !discussingArticle) return;
    if (discussingArticle.title === titleResolved) return;
    setDiscussingArticle((prev) => prev ? { ...prev, title: titleResolved } : prev);
  }, [titleResolved, discussingArticle]);

  const onSend = useCallback(() => {
    const text = draft.trim();
    if (!text || status === "streaming") return;
    setDraft("");
    send({
      conversation_id: effectiveId,
      message: text,
      // Phase 31 — forward the article context so the backend can
      // inject the deep-insight summary into the LLM's system prompt
      // on every turn (not just the first), keeping the conversation
      // grounded in the article even as the user asks follow-ups.
      article_id: discussingArticle?.article || undefined,
      company_slug: discussingArticle?.company || undefined,
    });
  }, [draft, status, send, effectiveId, discussingArticle]);

  const onNew = () => {
    setActiveId(null);
  };

  const onSelect = (cid: string) => {
    setActiveId(cid);
  };

  const onAuthorize = (phrase: string) => {
    if (status === "streaming") return;
    send({
      conversation_id: effectiveId,
      message: phrase,
      signoff: phrase,
    });
  };

  // Merge persisted history (when rehydrating) with live SSE messages
  const persistedMessages = (historyQuery.data?.messages ?? []).map((m) => ({
    role: m.role as "user" | "assistant",
    content: m.content ?? "",
    toulmin: (m.toulmin ?? null) as Record<string, unknown> | null,
  }));
  // Live SSE messages take precedence once the user starts sending
  const renderedMessages = messages.length > 0 ? messages : persistedMessages;

  // Mobile-aware sidebar toggle. The dual-pane layout works fine on
  // tablets+ but on phones the sidebar eats half the screen — collapse
  // it behind a "💬 Past conversations" button instead.
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div
      className="flex gap-3"
      // 5rem reserves space for the top role-switcher header,
      // 3.5rem (56px) reserves for the BottomNav fixed bar (h-14)
      // so the textarea + Send button sit ABOVE the nav on mobile.
      style={{ height: "calc(100vh - 5rem - 3.5rem)" }}
    >
      {/* Sidebar — hidden by default on mobile, slide-over when toggled.
          On tablet+ (md:flex) it shows in-line as a left rail. */}
      <div
        className={sidebarOpen ? "flex" : "hidden md:flex"}
        style={{
          flexDirection: "column",
          position: sidebarOpen ? "fixed" : "relative",
          top: sidebarOpen ? "5rem" : undefined,
          left: sidebarOpen ? 0 : undefined,
          right: sidebarOpen ? 0 : undefined,
          bottom: sidebarOpen ? "3.5rem" : undefined,
          zIndex: sidebarOpen ? 60 : "auto",
          background: sidebarOpen ? "#fff" : undefined,
          boxShadow: sidebarOpen ? "0 10px 30px rgba(0,0,0,0.18)" : undefined,
          padding: sidebarOpen ? "12px" : 0,
        }}
        onClick={() => sidebarOpen && setSidebarOpen(false)}
      >
        <ConversationSidebar
          activeId={effectiveId}
          onSelect={(cid) => { onSelect(cid); setSidebarOpen(false); }}
          onNew={() => { onNew(); setSidebarOpen(false); }}
          // Phase 31 — if the user deletes the conversation they're
          // currently viewing, drop the local view so the page doesn't
          // try to re-fetch the tombstoned id.
          onDeleted={(cid) => {
            if (cid === effectiveId) {
              setActiveId(null);
            }
          }}
        />
      </div>

      <Card className="flex flex-1 flex-col" style={{ minWidth: 0 }}>
        <CardHeader className="border-b">
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="text-base" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {effectiveId
                ? historyQuery.data?.summary.title ?? "Conversation"
                : "New conversation"}
            </CardTitle>
            <button
              type="button"
              className="md:hidden"
              onClick={() => setSidebarOpen(true)}
              style={{
                fontSize: 12, fontWeight: 600,
                padding: "4px 10px", borderRadius: 8,
                border: "1px solid #E2E8F0", background: "#fff", color: "#475569",
                whiteSpace: "nowrap", flexShrink: 0,
              }}
            >
              💬 History
            </button>
          </div>
        </CardHeader>
        {discussingArticle && (
          <div
            style={{
              padding: "8px 14px",
              background: "rgba(223,89,0,0.06)",
              borderBottom: "1px solid rgba(223,89,0,0.18)",
              fontSize: 12,
              lineHeight: 1.4,
            }}
          >
            <div>
              <span style={{ fontWeight: 600, color: "#DF5900" }}>
                📰 Discussing
              </span>{" "}
              <span style={{ color: "#475569" }}>
                for <strong>{discussingArticle.company.replace(/-/g, " ")}</strong>
              </span>
            </div>
            {discussingArticle.title && (
              <div
                style={{
                  fontSize: 11,
                  color: "#475569",
                  marginTop: 3,
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }}
                title={discussingArticle.title}
              >
                {discussingArticle.title}
              </div>
            )}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-3 sm:p-4 space-y-3" style={{ minHeight: 0 }}>
          {renderedMessages.length === 0 && !discussingArticle && (
            <div className="px-2 py-6">
              {/* Phase 31 — welcome state for the no-article entry. The
                  generic "Send a message" placeholder gave users no
                  starting point; the role-anchored chips below are a
                  one-tap launch into the most-useful first question
                  for their role. */}
              <p
                style={{
                  fontSize: 15,
                  fontWeight: 600,
                  color: "#0F172A",
                  marginBottom: 4,
                }}
              >
                Hi {firstName} — you're on {prettyCompany}.
              </p>
              <p
                style={{
                  fontSize: 13,
                  color: "#475569",
                  marginBottom: 16,
                  lineHeight: 1.5,
                }}
              >
                I have access to {prettyCompany}'s recent ESG news, the
                deep-insight engine, and your past conversations. Ask me
                anything in your <strong>{roleLabel}</strong> lens —
                or pick one of these to start:
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {suggestedPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => {
                      // One-tap launch: prime the input AND fire it
                      // so the user doesn't have to confirm a
                      // pre-filled chip.
                      setDraft("");
                      send({
                        conversation_id: effectiveId,
                        message: prompt,
                        article_id: undefined,
                        company_slug: companyId || undefined,
                      });
                    }}
                    disabled={status === "streaming"}
                    style={{
                      textAlign: "left",
                      padding: "10px 12px",
                      fontSize: 13,
                      lineHeight: 1.4,
                      background: "rgba(223,89,0,0.04)",
                      border: "1px solid rgba(223,89,0,0.18)",
                      borderRadius: 8,
                      color: "#0F172A",
                      cursor: status === "streaming" ? "default" : "pointer",
                      opacity: status === "streaming" ? 0.6 : 1,
                    }}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
              <p
                style={{
                  fontSize: 11,
                  color: "#94A3B8",
                  marginTop: 14,
                  lineHeight: 1.5,
                }}
              >
                Past conversations live in the History panel — open it to
                resume, rename, archive, or delete.
              </p>
            </div>
          )}
          {renderedMessages.map((msg, i) => (
            <MessageBubble key={i} msg={msg} onAuthorize={onAuthorize} />
          ))}
          {status === "streaming" && (
            <div className="text-xs text-muted-foreground flex items-center gap-2">
              <Spinner /> streaming…
            </div>
          )}
          {status === "error" && (
            <div className="text-xs text-red-600">
              Stream failed — please retry.
            </div>
          )}
        </div>
        <div
          className="border-t"
          style={{
            padding: "10px 12px",
            display: "flex",
            gap: 8,
            alignItems: "flex-end",
            background: "#fff",
          }}
        >
          <textarea
            placeholder="Ask about a tenant, theme, or article…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            disabled={status === "streaming"}
            rows={2}
            style={{
              flex: 1,
              minWidth: 0,
              padding: "10px 12px",
              fontSize: 14,
              fontFamily: "inherit",
              lineHeight: 1.45,
              border: "1px solid #E2E8F0",
              borderRadius: 8,
              outline: "none",
              resize: "none",
              maxHeight: 140,
              background: status === "streaming" ? "#F8FAFC" : "#fff",
            }}
          />
          <Button
            onClick={onSend}
            disabled={!draft.trim() || status === "streaming"}
            style={{ flexShrink: 0, alignSelf: "stretch" }}
          >
            Send
          </Button>
        </div>
      </Card>
    </div>
  );
}

interface MessageBubbleProps {
  msg: {
    role: "user" | "assistant";
    content: string;
    events?: Array<{ event: string; data: Record<string, unknown> }>;
    toulmin?: Record<string, unknown> | null;
  };
  onAuthorize?: (phrase: string) => void;
}

function MessageBubble({ msg, onAuthorize }: MessageBubbleProps) {
  const isUser = msg.role === "user";
  const bgColour = isUser ? "bg-blue-50" : "bg-gray-50";
  const align = isUser ? "ml-12" : "mr-12";

  const toolEvents = (msg.events ?? []).filter((e) => e.event === "tool_result");
  const advisorEvents = (msg.events ?? []).filter((e) => e.event === "advisor_hint");
  const signoffEvent = (msg.events ?? []).find((e) => e.event === "signoff_request");
  const toulminEvent = msg.toulmin
    ?? (msg.events ?? []).find((e) => e.event === "toulmin_chain")?.data;

  return (
    <div className={`rounded p-3 ${bgColour} ${align}`}>
      <div className="text-[10px] uppercase font-semibold text-muted-foreground mb-1">
        {msg.role}
      </div>
      {isUser ? (
        // User messages: plain text — they typed it, render verbatim
        // (no markdown parsing so a question with `**` isn't reformatted).
        <div className="whitespace-pre-wrap text-sm">{msg.content}</div>
      ) : (
        // Assistant messages: render as markdown so the `**bold**`,
        // bullet lists, and headers the LLM emits are styled instead of
        // shown as raw stars + dashes.
        <div className="text-sm chat-markdown">
          <ReactMarkdown
            components={{
              p: ({ children }) => (
                <p style={{ margin: "0 0 8px", lineHeight: 1.5 }}>{children}</p>
              ),
              strong: ({ children }) => (
                <strong style={{ fontWeight: 600 }}>{children}</strong>
              ),
              em: ({ children }) => (
                <em style={{ fontStyle: "italic", color: "#475569" }}>{children}</em>
              ),
              ul: ({ children }) => (
                <ul style={{ margin: "4px 0 8px", paddingLeft: 18, listStyle: "disc" }}>
                  {children}
                </ul>
              ),
              ol: ({ children }) => (
                <ol style={{ margin: "4px 0 8px", paddingLeft: 20, listStyle: "decimal" }}>
                  {children}
                </ol>
              ),
              li: ({ children }) => (
                <li style={{ marginBottom: 2 }}>{children}</li>
              ),
              h1: ({ children }) => (
                <h3 style={{ fontSize: 15, fontWeight: 700, margin: "10px 0 6px" }}>
                  {children}
                </h3>
              ),
              h2: ({ children }) => (
                <h3 style={{ fontSize: 14, fontWeight: 700, margin: "10px 0 6px" }}>
                  {children}
                </h3>
              ),
              h3: ({ children }) => (
                <h4 style={{ fontSize: 13, fontWeight: 700, margin: "8px 0 4px" }}>
                  {children}
                </h4>
              ),
              code: ({ children }) => (
                <code
                  style={{
                    background: "rgba(15,23,42,0.06)",
                    padding: "1px 5px",
                    borderRadius: 4,
                    fontSize: "0.92em",
                    fontFamily: "ui-monospace, SFMono-Regular, monospace",
                  }}
                >
                  {children}
                </code>
              ),
              hr: () => (
                <hr style={{ border: "none", borderTop: "1px solid #E2E8F0", margin: "10px 0" }} />
              ),
              a: ({ href, children }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "#DF5900", textDecoration: "underline" }}
                >
                  {children}
                </a>
              ),
            }}
          >
            {msg.content}
          </ReactMarkdown>
        </div>
      )}
      {toulminEvent && (
        <ToulminBadge
          chain={toulminEvent as {
            claim?: string;
            grounds?: string[] | string;
            warrant?: string;
            qualifier?: string;
            rebuttal?: string;
          }}
        />
      )}
      {toolEvents.map((e, i) => (
        <ToolInvocationCard
          key={i}
          tool={String((e.data as { tool?: string }).tool ?? "unknown")}
          state="ok"
          result={(e.data as { result?: Record<string, unknown> }).result ?? null}
        />
      ))}
      {signoffEvent && (
        <ToolInvocationCard
          tool={String((signoffEvent.data as { tool?: string }).tool ?? "destructive")}
          state="signoff_required"
          signoffPhrase={
            String((signoffEvent.data as { signoff_phrase?: string }).signoff_phrase ?? "")
          }
          onAuthorize={onAuthorize}
        />
      )}
      {advisorEvents.map((e, i) => (
        <AdvisorHintCard
          key={i}
          hint={
            e.data as {
              hint_id: string;
              coach: string;
              kind: string;
              severity: "low" | "moderate" | "high";
              headline: string;
              body: string;
              cta_label?: string | null;
              cta_target?: string | null;
            }
          }
        />
      ))}
    </div>
  );
}
