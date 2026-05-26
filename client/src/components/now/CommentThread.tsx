/**
 * Phase 34.5 — Article comment thread (Reddit-style, non-anonymous).
 *
 * Self-contained: queries `/api/articles/{id}/comments`, renders the
 * 1-level threaded view, exposes vote arrows + reply composer + author-
 * only soft-delete. Author identity is the JWT subject (the server
 * stamps it; the client just reads `author_name` for display).
 *
 * UI patterns (in order, top → bottom):
 *   1. Top-level composer (textarea + Post button)
 *   2. Threaded list (sorted by vote score DESC, then time ASC)
 *   3. Each comment row: vote arrows + score + body + footer
 *      (author · timestamp · Reply · Delete (if author))
 *   4. Reply row: 1-level indent, no further nesting
 *   5. Inline reply composer (opens when "Reply" tapped)
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { comments, type CommentDto } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { TOKENS } from "@/lib/designTokensV2";

interface Props {
  articleId: string;
}

export function CommentThread({ articleId }: Props) {
  const meEmail = useAuthStore((s) => s.userId) || "";
  const meName = useAuthStore((s) => s.name) || "";
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["article-comments", articleId],
    queryFn: () => comments.list(articleId),
    enabled: !!articleId,
    refetchOnWindowFocus: false,
  });

  const postMutation = useMutation({
    mutationFn: ({ body, parentId }: { body: string; parentId?: string }) =>
      comments.post(articleId, body, parentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["article-comments", articleId] });
    },
  });

  const voteMutation = useMutation({
    mutationFn: ({ commentId, direction }: { commentId: string; direction: -1 | 0 | 1 }) =>
      comments.vote(commentId, direction),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["article-comments", articleId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (commentId: string) => comments.delete(commentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["article-comments", articleId] });
    },
  });

  const threads = query.data?.threads || [];
  const count = query.data?.count || 0;

  return (
    <div>
      <div style={{
        display: "flex", alignItems: "baseline", justifyContent: "space-between",
        marginBottom: 10,
      }}>
        <p style={{
          margin: 0, fontSize: 10, fontWeight: 700, letterSpacing: "0.06em",
          textTransform: "uppercase", color: TOKENS.ink3,
        }}>
          Discussion
        </p>
        <span style={{ fontSize: 11, color: TOKENS.ink4 }}>
          {count === 0 ? "Be the first" : `${count} comment${count === 1 ? "" : "s"}`}
        </span>
      </div>

      {/* Top-level composer */}
      <Composer
        placeholder={`Share your read of this with the team, ${meName.split(" ")[0] || "there"}…`}
        submitting={postMutation.isPending && !postMutation.variables?.parentId}
        onSubmit={(body) => postMutation.mutate({ body })}
      />

      {/* Threads */}
      <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        {query.isLoading && (
          <p style={{ fontSize: 12, color: TOKENS.ink4, fontStyle: "italic" }}>
            Loading the conversation…
          </p>
        )}
        {!query.isLoading && threads.length === 0 && (
          <p style={{ fontSize: 12, color: TOKENS.ink4, fontStyle: "italic", padding: "12px 0" }}>
            No comments yet — kick off the discussion.
          </p>
        )}
        {threads.map((c) => (
          <Comment
            key={c.id}
            comment={c}
            meEmail={meEmail}
            onVote={(direction) =>
              voteMutation.mutate({ commentId: c.id, direction: pickNextVote(c.your_vote, direction) })}
            onDelete={() => deleteMutation.mutate(c.id)}
            onReplySubmit={(body) => postMutation.mutate({ body, parentId: c.id })}
            replyPending={postMutation.isPending && postMutation.variables?.parentId === c.id}
            onReplyVote={(replyId, currentVote, direction) =>
              voteMutation.mutate({ commentId: replyId, direction: pickNextVote(currentVote, direction) })}
            onReplyDelete={(replyId) => deleteMutation.mutate(replyId)}
            articleId={articleId}
          />
        ))}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function pickNextVote(current: number, intended: -1 | 0 | 1): -1 | 0 | 1 {
  // Toggle: if the user clicks the same direction again, retract.
  if (current === intended) return 0;
  return intended;
}

// ───────────────────────────────────────────────────────────────────────────

interface CommentProps {
  comment: CommentDto;
  meEmail: string;
  onVote: (direction: -1 | 1) => void;
  onDelete: () => void;
  onReplySubmit: (body: string) => void;
  replyPending: boolean;
  onReplyVote: (replyId: string, currentVote: number, direction: -1 | 1) => void;
  onReplyDelete: (replyId: string) => void;
  // POW-5c — propagated to CommentBody so each row's "Help me reply"
  // link can deep-link into /ask with the right article_id +
  // focus_comment.
  articleId?: string;
}

function Comment({
  comment, meEmail, onVote, onDelete, onReplySubmit, replyPending, onReplyVote, onReplyDelete, articleId,
}: CommentProps) {
  const [replyOpen, setReplyOpen] = useState(false);
  const isMine = comment.author_email === meEmail;
  const deleted = !!comment.deleted_at;

  return (
    <div style={{
      display: "flex", gap: 10, alignItems: "flex-start",
    }}>
      {/* Vote rail */}
      <VoteRail
        score={comment.vote_score}
        yourVote={comment.your_vote}
        onUp={() => onVote(1)}
        onDown={() => onVote(-1)}
        disabled={deleted}
      />

      {/* Body column */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <CommentBody
          comment={comment}
          isMine={isMine}
          deleted={deleted}
          onDelete={onDelete}
          onReplyClick={() => setReplyOpen((v) => !v)}
          articleIdForAsk={articleId}
        />

        {/* Replies (1 level deep) */}
        {comment.replies.length > 0 && (
          <div style={{
            marginTop: 10, marginLeft: 6,
            paddingLeft: 12,
            borderLeft: `2px solid ${TOKENS.line2}`,
            display: "flex", flexDirection: "column", gap: 10,
          }}>
            {comment.replies.map((r) => (
              <div key={r.id} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                <VoteRail
                  score={r.vote_score}
                  yourVote={r.your_vote}
                  onUp={() => onReplyVote(r.id, r.your_vote, 1)}
                  onDown={() => onReplyVote(r.id, r.your_vote, -1)}
                  disabled={!!r.deleted_at}
                  compact
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <CommentBody
                    comment={r}
                    isMine={r.author_email === meEmail}
                    deleted={!!r.deleted_at}
                    onDelete={() => onReplyDelete(r.id)}
                    articleIdForAsk={articleId}
                  />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Reply composer */}
        {replyOpen && !deleted && (
          <div style={{ marginTop: 10 }}>
            <Composer
              placeholder={`Reply to ${comment.author_name}…`}
              compact
              submitting={replyPending}
              onSubmit={(body) => {
                onReplySubmit(body);
                setReplyOpen(false);
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function CommentBody({
  comment, isMine, deleted, onDelete, onReplyClick, articleIdForAsk,
}: {
  comment: CommentDto;
  isMine: boolean;
  deleted: boolean;
  onDelete: () => void;
  onReplyClick?: () => void;
  // POW-5c — when present, render a "Help me reply" link that deep-links
  // into /ask with focus_comment set to this comment id.
  articleIdForAsk?: string;
}) {
  return (
    <>
      <p style={{
        margin: 0, fontSize: 13.5, lineHeight: 1.5,
        color: deleted ? TOKENS.ink4 : TOKENS.ink,
        fontStyle: deleted ? "italic" : "normal",
        wordBreak: "break-word",
      }}>
        {comment.body}
      </p>
      <div style={{
        marginTop: 5, display: "flex", gap: 10, alignItems: "center",
        fontSize: 11, color: TOKENS.ink4,
      }}>
        <span style={{ fontWeight: 600, color: TOKENS.ink3 }}>{comment.author_name}</span>
        <span>·</span>
        <span>{formatRelative(comment.created_at)}</span>
        {onReplyClick && !deleted && (
          <>
            <span>·</span>
            <button onClick={onReplyClick} className="tap" style={{
              background: "none", border: "none",
              color: TOKENS.brand, fontSize: 11, fontWeight: 600,
              cursor: "pointer", padding: 0,
            }}>
              Reply
            </button>
          </>
        )}
        {isMine && !deleted && (
          <>
            <span>·</span>
            <button onClick={onDelete} className="tap" style={{
              background: "none", border: "none",
              color: TOKENS.ink4, fontSize: 11, fontWeight: 500,
              cursor: "pointer", padding: 0,
            }}>
              Delete
            </button>
          </>
        )}
        {/* POW-5c — "Help me reply" deep-link into /ask focused on this comment */}
        {articleIdForAsk && !deleted && (
          <>
            <span>·</span>
            <a
              href={`/ask?article=${encodeURIComponent(articleIdForAsk)}&include_comments=true&focus_comment=${encodeURIComponent(comment.id)}`}
              className="tap"
              style={{
                background: "none", border: "none",
                color: TOKENS.brand, fontSize: 11, fontWeight: 600,
                cursor: "pointer", padding: 0, textDecoration: "none",
              }}
            >
              ✨ Help me reply
            </a>
          </>
        )}
      </div>
    </>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function VoteRail({
  score, yourVote, onUp, onDown, disabled, compact,
}: {
  score: number;
  yourVote: number;
  onUp: () => void;
  onDown: () => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const size = compact ? 18 : 20;
  return (
    <div style={{
      flex: "0 0 auto",
      display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
      minWidth: 26,
      paddingTop: 1,
    }}>
      <button
        onClick={onUp}
        disabled={disabled}
        aria-label="Upvote"
        className="tap"
        style={{
          width: size, height: size, padding: 0,
          background: "none", border: "none",
          color: yourVote === 1 ? TOKENS.brand : TOKENS.ink4,
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.3 : 1,
        }}
      >
        <svg width={size - 2} height={size - 2} viewBox="0 0 14 14" fill="currentColor">
          <path d="M7 2 12 9H8.5v3h-3V9H2L7 2z"/>
        </svg>
      </button>
      <span style={{
        fontSize: 11, fontWeight: 700,
        color: yourVote !== 0 ? TOKENS.brand : TOKENS.ink2,
        fontVariantNumeric: "tabular-nums",
      }}>
        {score}
      </span>
      <button
        onClick={onDown}
        disabled={disabled}
        aria-label="Downvote"
        className="tap"
        style={{
          width: size, height: size, padding: 0,
          background: "none", border: "none",
          color: yourVote === -1 ? TOKENS.critical : TOKENS.ink4,
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.3 : 1,
        }}
      >
        <svg width={size - 2} height={size - 2} viewBox="0 0 14 14" fill="currentColor">
          <path d="M7 12 2 5h3.5V2h3v3H12L7 12z"/>
        </svg>
      </button>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function Composer({
  placeholder, compact, submitting, onSubmit,
}: {
  placeholder: string;
  compact?: boolean;
  submitting: boolean;
  onSubmit: (body: string) => void;
}) {
  const [body, setBody] = useState("");
  const trimmed = body.trim();
  const canPost = trimmed.length > 0 && !submitting;

  return (
    <div style={{
      display: "flex", gap: 8, alignItems: "flex-start",
      padding: compact ? "8px 10px" : "10px 12px",
      background: TOKENS.bgSoft,
      border: `1px solid ${TOKENS.line}`,
      borderRadius: 12,
    }}>
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder={placeholder}
        rows={compact ? 2 : 3}
        style={{
          flex: 1, minWidth: 0,
          border: "none", background: "transparent",
          resize: "vertical",
          fontFamily: "inherit",
          fontSize: 13.5, lineHeight: 1.5,
          color: TOKENS.ink,
          outline: "none",
        }}
      />
      <button
        onClick={() => { if (canPost) { onSubmit(trimmed); setBody(""); } }}
        disabled={!canPost}
        className="tap"
        style={{
          flex: "0 0 auto",
          padding: "8px 14px",
          borderRadius: 999,
          border: "none",
          background: canPost ? TOKENS.brand : TOKENS.line,
          color: canPost ? "#fff" : TOKENS.ink4,
          fontSize: 12, fontWeight: 700, letterSpacing: "0.02em",
          cursor: canPost ? "pointer" : "not-allowed",
          alignSelf: "flex-end",
        }}
      >
        {submitting ? "Posting…" : "Post"}
      </button>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────

function formatRelative(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  if (secs < 30 * 86400) return `${Math.round(secs / 86400)}d ago`;
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
