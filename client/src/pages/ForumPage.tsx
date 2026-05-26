/**
 * Phase 34.6 — Forum page (user-generated threads + replies).
 *
 * Two states inside one page:
 *   1. List view — tag filter chips + new-thread composer + thread list
 *   2. Detail view — selected thread body + replies + reply composer
 *
 * Switches via local state (no separate route). Identity = JWT sub claim
 * (server-side). Tag taxonomy fixed: BRSR / Climate / CBAM / Governance
 * / Audit.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { forum, FORUM_TAGS, type ForumTag, type ForumThreadDto } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { IPhoneFrame } from "@/components/ui/IPhoneFrame";
import { TOKENS } from "@/lib/designTokensV2";

export function ForumPage() {
  const meEmail = useAuthStore((s) => s.userId) || "";
  const [tagFilter, setTagFilter] = useState<ForumTag | null>(null);
  const [openThreadId, setOpenThreadId] = useState<string | null>(null);
  const [composing, setComposing] = useState<boolean>(false);

  return (
    <IPhoneFrame>
      <div style={{ position: "absolute", inset: 0, paddingTop: 6, display: "flex", flexDirection: "column" }}>
        {openThreadId ? (
          <ForumThreadView
            threadId={openThreadId}
            meEmail={meEmail}
            onBack={() => setOpenThreadId(null)}
          />
        ) : (
          <ForumListView
            tagFilter={tagFilter}
            onTagFilter={setTagFilter}
            onOpenThread={setOpenThreadId}
            composing={composing}
            onToggleCompose={() => setComposing((c) => !c)}
          />
        )}
        {/* Bottom nav */}
        <div style={{
          position: "relative", height: 70,
          borderTop: `1px solid ${TOKENS.line}`, background: "#fff",
          display: "flex", alignItems: "center", justifyContent: "space-around",
        }}>
          {(["Now", "Forum", "Wiki", "Ask"] as const).map((label) => {
            const href = label === "Now" ? "/now" : label === "Wiki" ? "/wiki" : label === "Forum" ? "/forum" : "/ask";
            return (
              <a
                key={label}
                href={href}
                className="tap"
                style={{
                  fontSize: 11, fontWeight: 600,
                  color: label === "Forum" ? TOKENS.brand : TOKENS.ink4,
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

// ──────────────────────────────────────────────────────────────────────────
// List view: tag chips + composer + thread cards
// ──────────────────────────────────────────────────────────────────────────

interface ListProps {
  tagFilter: ForumTag | null;
  onTagFilter: (t: ForumTag | null) => void;
  onOpenThread: (id: string) => void;
  composing: boolean;
  onToggleCompose: () => void;
}

function ForumListView({ tagFilter, onTagFilter, onOpenThread, composing, onToggleCompose }: ListProps) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["forum-threads", tagFilter],
    queryFn: () => forum.listThreads(tagFilter || undefined, 100),
    refetchOnWindowFocus: false,
  });

  const [draftTitle, setDraftTitle] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [draftTag, setDraftTag] = useState<ForumTag>("BRSR");

  const createMutation = useMutation({
    mutationFn: () => forum.createThread(draftTitle.trim(), draftBody.trim(), draftTag),
    onSuccess: () => {
      setDraftTitle(""); setDraftBody("");
      qc.invalidateQueries({ queryKey: ["forum-threads"] });
      onToggleCompose();
    },
  });

  const canPost = draftTitle.trim().length >= 3 && draftBody.trim().length > 0;

  return (
    <>
      <div style={{ padding: "12px 20px 4px" }}>
        <span style={{
          fontSize: 11, color: TOKENS.ink4, letterSpacing: "0.04em",
          fontWeight: 600, textTransform: "uppercase",
        }}>
          Forum
        </span>
        <h1 className="serif" style={{
          margin: "2px 0 4px", fontSize: 22, fontWeight: 500, color: TOKENS.ink,
          letterSpacing: "-0.015em",
        }}>
          Open discussions
        </h1>
        <p style={{ margin: 0, fontSize: 12, color: TOKENS.ink3, lineHeight: 1.4 }}>
          Start a thread or join an existing one. Non-anonymous.
        </p>
      </div>

      {/* Tag chips */}
      <div style={{
        display: "flex", gap: 8, padding: "12px 20px 6px",
        overflowX: "auto", scrollbarWidth: "none",
      }}>
        <Chip active={tagFilter === null} label="All" onClick={() => onTagFilter(null)} />
        {FORUM_TAGS.map((t) => (
          <Chip key={t} active={tagFilter === t} label={t} onClick={() => onTagFilter(t)} />
        ))}
      </div>

      {/* New thread CTA */}
      <div style={{ padding: "6px 20px 8px" }}>
        <button
          onClick={onToggleCompose}
          style={{
            width: "100%",
            background: composing ? TOKENS.line : TOKENS.brand,
            color: composing ? TOKENS.ink2 : "#fff",
            border: "none", borderRadius: 10,
            padding: "10px 14px",
            fontWeight: 600, fontSize: 13, cursor: "pointer",
          }}
        >
          {composing ? "Cancel" : "Start a new thread"}
        </button>
      </div>

      {composing && (
        <div style={{ padding: "0 20px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
          <input
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            placeholder="Thread title (3-200 chars)"
            maxLength={200}
            style={{
              border: `1px solid ${TOKENS.line}`, borderRadius: 8,
              padding: "8px 10px", fontSize: 13, fontFamily: "inherit",
            }}
          />
          <textarea
            value={draftBody}
            onChange={(e) => setDraftBody(e.target.value)}
            placeholder="What's on your mind?"
            rows={4}
            maxLength={8000}
            style={{
              border: `1px solid ${TOKENS.line}`, borderRadius: 8,
              padding: "8px 10px", fontSize: 13, resize: "vertical",
              fontFamily: "inherit",
            }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <label style={{ fontSize: 12, color: TOKENS.ink3 }}>Tag</label>
            <select
              value={draftTag}
              onChange={(e) => setDraftTag(e.target.value as ForumTag)}
              style={{
                border: `1px solid ${TOKENS.line}`, borderRadius: 6,
                padding: "4px 8px", fontSize: 12.5,
              }}
            >
              {FORUM_TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <button
              onClick={() => canPost && createMutation.mutate()}
              disabled={!canPost || createMutation.isPending}
              style={{
                marginLeft: "auto",
                background: canPost ? TOKENS.brand : TOKENS.line,
                color: canPost ? "#fff" : TOKENS.ink4,
                border: "none", borderRadius: 8,
                padding: "6px 14px", fontWeight: 600, fontSize: 12.5,
                cursor: canPost ? "pointer" : "default",
              }}
            >
              {createMutation.isPending ? "Posting…" : "Post"}
            </button>
          </div>
        </div>
      )}

      {/* Threads */}
      <div className="app-scroll" style={{ flex: 1, overflowY: "auto", paddingBottom: 90 }}>
        {query.isLoading ? (
          <div style={{ padding: 40, textAlign: "center", color: TOKENS.ink4, fontSize: 13 }}>
            Loading discussions…
          </div>
        ) : (query.data?.threads || []).length === 0 ? (
          <div style={{ padding: "40px 22px", textAlign: "center" }}>
            <div style={{ fontSize: 32, lineHeight: 1, marginBottom: 8 }}>💬</div>
            <div style={{ marginTop: 8, fontSize: 13.5, color: TOKENS.ink3 }}>
              {tagFilter
                ? `No threads tagged ${tagFilter} yet.`
                : "No threads yet. Start the first one."}
            </div>
          </div>
        ) : (
          (query.data?.threads || []).map((t) => (
            <ThreadCard key={t.id} thread={t} onOpen={() => onOpenThread(t.id)} />
          ))
        )}
      </div>
    </>
  );
}

function Chip({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="tap"
      style={{
        flexShrink: 0,
        background: active ? TOKENS.ink : "#fff",
        color: active ? "#fff" : TOKENS.ink2,
        border: `1px solid ${active ? TOKENS.ink : TOKENS.line}`,
        borderRadius: 999,
        padding: "6px 14px",
        fontSize: 11.5, fontWeight: 600,
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );
}

function ThreadCard({ thread, onOpen }: { thread: ForumThreadDto; onOpen: () => void }) {
  const stamp = useMemo(() => {
    try {
      const d = new Date(thread.created_at);
      const mins = Math.round((Date.now() - d.getTime()) / 60000);
      if (mins < 60) return `${Math.max(mins, 1)}m ago`;
      const hrs = Math.round(mins / 60);
      if (hrs < 48) return `${hrs}h ago`;
      const days = Math.round(hrs / 24);
      if (days < 30) return `${days}d ago`;
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch { return ""; }
  }, [thread.created_at]);

  return (
    <button
      onClick={onOpen}
      className="tap"
      style={{
        display: "block", width: "calc(100% - 40px)", margin: "0 20px 10px",
        background: "#fff", border: `1px solid ${TOKENS.line}`,
        borderRadius: 14, padding: "12px 14px",
        textAlign: "left", cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        {thread.pinned && (
          <span style={{
            fontSize: 9.5, fontWeight: 700, color: "#8a6a08",
            background: "#fffbf3", border: "1px solid #f0e2c8",
            padding: "1px 6px", borderRadius: 999,
            letterSpacing: "0.04em", textTransform: "uppercase",
          }}>Pinned</span>
        )}
        <span style={{
          fontSize: 10, fontWeight: 700, color: TOKENS.brand,
          background: "#fff5ed", border: "1px solid #f8d6b8",
          padding: "1px 8px", borderRadius: 999,
          letterSpacing: "0.05em",
        }}>{thread.tag}</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: TOKENS.ink4 }}>{stamp}</span>
      </div>
      <div style={{ fontSize: 13.5, fontWeight: 600, color: TOKENS.ink, lineHeight: 1.35, marginBottom: 4 }}>
        {thread.title}
      </div>
      <div style={{ fontSize: 12, color: TOKENS.ink3, lineHeight: 1.4 }}>
        {thread.body.length > 140 ? thread.body.slice(0, 140) + "…" : thread.body}
      </div>
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        marginTop: 8, paddingTop: 8,
        borderTop: `1px dashed ${TOKENS.line}`,
        fontSize: 11, color: TOKENS.ink4,
      }}>
        <span>{thread.author_name}</span>
        <span style={{ marginLeft: "auto" }}>
          {thread.reply_count} repl{thread.reply_count === 1 ? "y" : "ies"}
        </span>
      </div>
    </button>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Detail view
// ──────────────────────────────────────────────────────────────────────────

function ForumThreadView({ threadId, meEmail, onBack }: { threadId: string; meEmail: string; onBack: () => void }) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["forum-thread", threadId],
    queryFn: () => forum.getThread(threadId),
    refetchOnWindowFocus: false,
  });
  const [draftReply, setDraftReply] = useState("");

  const replyMutation = useMutation({
    mutationFn: () => forum.addReply(threadId, draftReply.trim()),
    onSuccess: () => {
      setDraftReply("");
      qc.invalidateQueries({ queryKey: ["forum-thread", threadId] });
      qc.invalidateQueries({ queryKey: ["forum-threads"] });
    },
  });

  const deleteReplyMutation = useMutation({
    mutationFn: (replyId: string) => forum.deleteReply(replyId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["forum-thread", threadId] }),
  });

  const deleteThreadMutation = useMutation({
    mutationFn: () => forum.deleteThread(threadId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["forum-threads"] });
      onBack();
    },
  });

  const thread = query.data?.thread;
  const replies = query.data?.replies || [];

  return (
    <div className="app-scroll" style={{ flex: 1, overflowY: "auto", paddingBottom: 90 }}>
      <div style={{
        padding: "12px 20px 12px",
        borderBottom: `1px solid ${TOKENS.line}`,
        background: "#fff",
        position: "sticky", top: 0, zIndex: 5,
      }}>
        <button
          onClick={onBack}
          style={{
            background: "transparent", border: "none", color: TOKENS.ink3,
            fontSize: 13, cursor: "pointer", padding: 0,
          }}
        >
          ← Back to forum
        </button>
      </div>
      {query.isLoading || !thread ? (
        <div style={{ padding: 40, textAlign: "center", color: TOKENS.ink4 }}>Loading…</div>
      ) : (
        <>
          <div style={{ padding: "16px 20px 10px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
              <span style={{
                fontSize: 10, fontWeight: 700, color: TOKENS.brand,
                background: "#fff5ed", border: "1px solid #f8d6b8",
                padding: "1px 8px", borderRadius: 999,
                letterSpacing: "0.05em",
              }}>{thread.tag}</span>
              <span style={{ marginLeft: "auto", fontSize: 11, color: TOKENS.ink4 }}>
                {thread.author_name}
              </span>
            </div>
            <h2 className="serif" style={{
              margin: 0, fontSize: 19, fontWeight: 500, color: TOKENS.ink,
              letterSpacing: "-0.012em", lineHeight: 1.3,
            }}>
              {thread.title}
            </h2>
            <p style={{
              margin: "12px 0 0", fontSize: 13.5, color: TOKENS.ink2, lineHeight: 1.5,
              whiteSpace: "pre-wrap",
            }}>
              {thread.body}
            </p>

            {/* Forum v1.1 — Ask AI deep-link. Opens /ask with the
                forum_thread URL param so AskPage seeds the conversation
                with the thread title + body + replies via the
                forum-thread MCP tool. */}
            <a
              href={`/ask?forum_thread=${encodeURIComponent(threadId)}`}
              className="tap"
              style={{
                display: "block",
                marginTop: 14,
                background: TOKENS.brand, color: "#fff",
                padding: "10px 14px", borderRadius: 10,
                fontSize: 13.5, fontWeight: 600,
                textAlign: "center", textDecoration: "none",
                cursor: "pointer",
              }}
            >
              ✨ Discuss this thread with AI
            </a>

            {thread.author_email === meEmail && !thread.deleted_at && (
              <button
                onClick={() => deleteThreadMutation.mutate()}
                style={{
                  marginTop: 12,
                  background: "transparent", border: "none", color: TOKENS.critical,
                  fontSize: 11.5, cursor: "pointer", padding: 0,
                }}
              >
                Delete thread
              </button>
            )}
          </div>

          <div style={{
            padding: "10px 20px",
            borderTop: `1px solid ${TOKENS.line}`,
            background: "#fafaf8",
          }}>
            <p style={{
              margin: 0, fontSize: 10, fontWeight: 700,
              letterSpacing: "0.06em", textTransform: "uppercase",
              color: TOKENS.ink3,
            }}>
              {replies.length} repl{replies.length === 1 ? "y" : "ies"}
            </p>
          </div>

          <div style={{ padding: "10px 20px" }}>
            {replies.length === 0 ? (
              <div style={{ fontSize: 12.5, color: TOKENS.ink4, padding: "12px 0" }}>
                No replies yet — kick off the conversation.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {replies.map((r) => (
                  <div key={r.id} style={{
                    background: "#fff", border: `1px solid ${TOKENS.line}`,
                    borderRadius: 12, padding: "10px 12px",
                  }}>
                    <div style={{
                      display: "flex", alignItems: "baseline", gap: 8,
                      marginBottom: 4, fontSize: 11, color: TOKENS.ink4,
                    }}>
                      <span style={{ fontWeight: 600, color: TOKENS.ink2 }}>{r.author_name}</span>
                      <span style={{ marginLeft: "auto" }}>
                        {new Date(r.created_at).toLocaleString(undefined, {
                          month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                        })}
                      </span>
                    </div>
                    <div style={{ fontSize: 13, color: TOKENS.ink, lineHeight: 1.45, whiteSpace: "pre-wrap" }}>
                      {r.body}
                    </div>
                    {r.author_email === meEmail && !r.deleted_at && (
                      <button
                        onClick={() => deleteReplyMutation.mutate(r.id)}
                        style={{
                          marginTop: 6,
                          background: "transparent", border: "none", color: TOKENS.critical,
                          fontSize: 10.5, cursor: "pointer", padding: 0,
                        }}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Reply composer */}
          <div style={{ padding: "10px 20px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
            <textarea
              value={draftReply}
              onChange={(e) => setDraftReply(e.target.value)}
              placeholder="Add your reply…"
              rows={3}
              maxLength={4000}
              style={{
                border: `1px solid ${TOKENS.line}`, borderRadius: 8,
                padding: "8px 10px", fontSize: 13, resize: "vertical",
                fontFamily: "inherit",
              }}
            />
            <button
              onClick={() => draftReply.trim() && replyMutation.mutate()}
              disabled={!draftReply.trim() || replyMutation.isPending}
              style={{
                alignSelf: "flex-end",
                background: draftReply.trim() ? TOKENS.brand : TOKENS.line,
                color: draftReply.trim() ? "#fff" : TOKENS.ink4,
                border: "none", borderRadius: 8,
                padding: "6px 14px", fontWeight: 600, fontSize: 12.5,
                cursor: draftReply.trim() ? "pointer" : "default",
              }}
            >
              {replyMutation.isPending ? "Posting…" : "Post reply"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
