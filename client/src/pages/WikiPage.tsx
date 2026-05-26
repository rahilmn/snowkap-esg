/**
 * Phase 34.7 — Personal Wiki page.
 *
 * Server-side bookmark surface backed by `/api/me/bookmarks`. Replaces
 * the legacy localStorage-only `savedStore` view. On first mount the
 * page kicks off a one-shot migration of any localStorage bookmarks
 * the user accumulated before Phase 34.7 shipped (idempotent — the
 * backend skips duplicates).
 *
 * Layout (Power-of-Now reference: screens.jsx::WikiScreen):
 *   - IPhoneFrame wrapper (desktop frame, full-bleed on mobile)
 *   - Stats strip: Saved · Notes · Topics
 *   - Sections: Pinned / Climate / Capital / Social / Custom — only
 *     sections with ≥1 bookmark render
 *   - Each bookmark card: title (resolved from saved cache), note
 *     (inline-editable), section selector
 *   - Empty state: "Swipe down on a story to start your Wiki."
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bookmarks, type BookmarkDto, type WikiSection } from "@/lib/api";
import { useSavedStore } from "@/stores/savedStore";
import { IPhoneFrame } from "@/components/ui/IPhoneFrame";
import { TOKENS } from "@/lib/designTokensV2";
import type { Article } from "@/types";

const SECTIONS: WikiSection[] = ["pinned", "climate", "capital", "social", "custom"];

const SECTION_LABEL: Record<WikiSection, string> = {
  pinned: "Pinned",
  climate: "Climate & Carbon",
  capital: "Capital & Disclosure",
  social: "Social & Workforce",
  custom: "Custom",
};

export function WikiPage() {
  const qc = useQueryClient();
  const savedArticles = useSavedStore((s) => s.savedArticles);
  const localIds = useSavedStore((s) => s.savedIds);
  const [migrated, setMigrated] = useState<boolean>(() => {
    try { return localStorage.getItem("pon-wiki-migrated-v1") === "1"; } catch { return false; }
  });

  // Fetch server-side bookmarks.
  const query = useQuery({
    queryKey: ["wiki-bookmarks"],
    queryFn: () => bookmarks.list(),
    refetchOnWindowFocus: false,
  });

  // One-shot localStorage → server migration on first load.
  const migrateMutation = useMutation({
    mutationFn: (items: Array<{ article_id: string }>) => bookmarks.bulkAdd(items),
    onSuccess: () => {
      try { localStorage.setItem("pon-wiki-migrated-v1", "1"); } catch { /* private mode */ }
      setMigrated(true);
      qc.invalidateQueries({ queryKey: ["wiki-bookmarks"] });
    },
  });

  useEffect(() => {
    if (migrated || migrateMutation.isPending || !query.isFetched) return;
    const idsToMigrate = Array.from(localIds).map((id) => ({ article_id: id }));
    if (idsToMigrate.length === 0) {
      try { localStorage.setItem("pon-wiki-migrated-v1", "1"); } catch { /* */ }
      setMigrated(true);
      return;
    }
    migrateMutation.mutate(idsToMigrate);
  }, [migrated, query.isFetched, localIds, migrateMutation]);

  // Wiki v1.1 — cross-device sync. On every mount, reconcile the
  // in-memory `savedIds` Set with the canonical server bookmark list so
  // a bookmark created on Phone A immediately reflects on Phone B's
  // /now deck (and a removal on A clears the badge on B).
  useEffect(() => {
    if (!query.isFetched) return;
    useSavedStore.getState().syncFromServer().catch(() => { /* non-fatal */ });
  }, [query.isFetched, query.data]);

  const serverBookmarks: BookmarkDto[] = query.data?.bookmarks || [];
  const articleById = useMemo(() => {
    const map = new Map<string, Article>();
    for (const a of savedArticles) map.set(a.id, a);
    return map;
  }, [savedArticles]);

  const sectionGroups = useMemo(() => {
    const groups: Record<WikiSection, BookmarkDto[]> = {
      pinned: [], climate: [], capital: [], social: [], custom: [],
    };
    for (const b of serverBookmarks) {
      const sec: WikiSection = SECTIONS.includes(b.section) ? b.section : "custom";
      groups[sec].push(b);
    }
    return groups;
  }, [serverBookmarks]);

  const totalBookmarks = serverBookmarks.length;
  const totalNotes = serverBookmarks.filter((b) => (b.note || "").trim().length > 0).length;
  const totalTopics = SECTIONS.filter((s) => sectionGroups[s].length > 0).length;

  const noteMutation = useMutation({
    mutationFn: ({ articleId, note }: { articleId: string; note: string }) =>
      bookmarks.patch(articleId, { note }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wiki-bookmarks"] }),
  });

  const sectionMutation = useMutation({
    mutationFn: ({ articleId, section }: { articleId: string; section: WikiSection }) =>
      bookmarks.patch(articleId, { section }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wiki-bookmarks"] }),
  });

  const removeMutation = useMutation({
    mutationFn: (articleId: string) => bookmarks.remove(articleId),
    onSuccess: (_data, articleId) => {
      // Also clear the local cache so the SwipeDeck stops showing it as bookmarked.
      useSavedStore.getState().unsaveArticle(articleId);
      qc.invalidateQueries({ queryKey: ["wiki-bookmarks"] });
    },
  });

  return (
    <IPhoneFrame>
      <div style={{ position: "absolute", inset: 0, paddingTop: 6, display: "flex", flexDirection: "column" }}>
        {/* Header */}
        <div style={{ padding: "12px 20px 4px" }}>
          <span style={{
            fontSize: 11, color: TOKENS.ink4, letterSpacing: "0.04em",
            fontWeight: 600, textTransform: "uppercase",
          }}>
            Personal Wiki
          </span>
          <h1 className="serif" style={{
            margin: "2px 0 4px", fontSize: 22, fontWeight: 500, color: TOKENS.ink,
            letterSpacing: "-0.015em",
          }}>
            Your saved stories
          </h1>
          <p style={{ margin: 0, fontSize: 12, color: TOKENS.ink3, lineHeight: 1.4 }}>
            Saved articles + notes. Swipe down on a story in /now to add it here.
          </p>
        </div>

        {/* Stats strip */}
        <div style={{
          margin: "14px 20px 14px",
          padding: 14,
          background: "linear-gradient(135deg, #fafaf8 0%, #f4f4f6 100%)",
          border: `1px solid ${TOKENS.line}`,
          borderRadius: 16,
          display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
          gap: 14,
        }}>
          <Stat n={totalBookmarks} label="Saved"/>
          <Stat n={totalNotes} label="Notes"/>
          <Stat n={totalTopics} label="Topics"/>
        </div>

        {/* Wiki v1.1 — Ask AI about my Wiki CTA. Only renders when the
            user has at least one bookmark; an empty wiki has nothing
            to summarise. */}
        {totalBookmarks > 0 && (
          <div style={{ margin: "0 20px 14px" }}>
            <a
              href="/ask?wiki=true"
              className="tap"
              style={{
                display: "block",
                background: TOKENS.brand, color: "#fff",
                padding: "11px 14px", borderRadius: 12,
                fontSize: 13.5, fontWeight: 600,
                textAlign: "center", textDecoration: "none",
                cursor: "pointer",
              }}
            >
              ✨ Ask AI about my Wiki
            </a>
          </div>
        )}

        {/* Scrollable section list */}
        <div className="app-scroll" style={{ flex: 1, overflowY: "auto", paddingBottom: 90 }}>
          {query.isLoading ? (
            <div style={{ padding: 40, textAlign: "center", color: TOKENS.ink4, fontSize: 13 }}>
              Loading your wiki…
            </div>
          ) : totalBookmarks === 0 ? (
            <div style={{ padding: "40px 22px", textAlign: "center" }}>
              <div style={{ fontSize: 32, lineHeight: 1, marginBottom: 8 }}>📒</div>
              <div style={{ marginTop: 12, fontSize: 14, color: TOKENS.ink3, lineHeight: 1.5 }}>
                Swipe ↓ on a story to start your Wiki.
              </div>
            </div>
          ) : (
            SECTIONS.map((sec) => {
              const items = sectionGroups[sec];
              if (items.length === 0) return null;
              return (
                <div key={sec} style={{ padding: "0 20px 22px" }}>
                  <div style={{
                    display: "flex", alignItems: "baseline", justifyContent: "space-between",
                    marginBottom: 8,
                  }}>
                    <h3 className="serif" style={{
                      margin: 0, fontSize: 17, fontWeight: 500, letterSpacing: "-0.01em",
                      color: TOKENS.ink,
                    }}>{SECTION_LABEL[sec]}</h3>
                    <span style={{ fontSize: 11, color: TOKENS.ink4 }}>
                      {items.length} item{items.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {items.map((b) => (
                      <WikiCard
                        key={b.article_id}
                        bookmark={b}
                        article={articleById.get(b.article_id) || null}
                        onSaveNote={(note) => noteMutation.mutate({ articleId: b.article_id, note })}
                        onMoveSection={(section) => sectionMutation.mutate({ articleId: b.article_id, section })}
                        onRemove={() => removeMutation.mutate(b.article_id)}
                      />
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Bottom nav — mirrors /now */}
        <div style={{
          position: "relative", height: 70,
          borderTop: `1px solid ${TOKENS.line}`, background: "#fff",
          display: "flex", alignItems: "center", justifyContent: "space-around",
        }}>
          {(["Now", "Forum", "Wiki", "Ask"] as const).map((label) => (
            <a
              key={label}
              href={label === "Now" ? "/now" : label === "Wiki" ? "/wiki" : label === "Forum" ? "/forum" : "/ask"}
              className="tap"
              style={{
                fontSize: 11, fontWeight: 600,
                color: label === "Wiki" ? TOKENS.brand : TOKENS.ink4,
                background: "transparent", border: "none", cursor: "pointer",
                padding: "8px 14px", borderRadius: 10,
                textDecoration: "none",
              }}
            >
              {label}
            </a>
          ))}
        </div>
      </div>
    </IPhoneFrame>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <div style={{ textAlign: "left" }}>
      <div className="serif" style={{
        fontSize: 26, fontWeight: 500, color: TOKENS.ink,
        letterSpacing: "-0.02em", lineHeight: 1,
      }}>{n}</div>
      <div style={{ fontSize: 11, color: TOKENS.ink3, marginTop: 4, letterSpacing: "0.02em" }}>{label}</div>
    </div>
  );
}

interface WikiCardProps {
  bookmark: BookmarkDto;
  article: Article | null;
  onSaveNote: (note: string) => void;
  onMoveSection: (section: WikiSection) => void;
  onRemove: () => void;
}

function WikiCard({ bookmark, article, onSaveNote, onMoveSection, onRemove }: WikiCardProps) {
  const [editing, setEditing] = useState<boolean>(false);
  const [draftNote, setDraftNote] = useState<string>(bookmark.note || "");

  const title = article?.title || "Saved article";
  const source = article?.source || "—";

  const stamp = useMemo(() => {
    try {
      const d = new Date(bookmark.bookmarked_at);
      const days = Math.round((Date.now() - d.getTime()) / 86400000);
      if (days <= 0) return "today";
      if (days === 1) return "yesterday";
      if (days < 30) return `${days}d ago`;
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch { return ""; }
  }, [bookmark.bookmarked_at]);

  return (
    <div style={{
      background: "#fff", border: `1px solid ${TOKENS.line}`,
      borderRadius: 14, padding: "12px 14px",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 6, marginBottom: 6,
        fontSize: 10.5, color: TOKENS.ink4, fontWeight: 600,
        letterSpacing: "0.03em",
      }}>
        <span style={{ textTransform: "uppercase" }}>{source}</span>
        <span style={{ marginLeft: "auto", fontWeight: 500 }}>{stamp}</span>
      </div>
      <div style={{
        fontSize: 13.5, fontWeight: 600, color: TOKENS.ink, lineHeight: 1.35, marginBottom: 6,
      }}>{title}</div>

      {/* Note (editable) */}
      {editing ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
          <textarea
            value={draftNote}
            onChange={(e) => setDraftNote(e.target.value)}
            placeholder="Add a note for future-you…"
            rows={2}
            style={{
              width: "100%", borderRadius: 8, padding: 8,
              border: `1px solid ${TOKENS.line}`, fontSize: 12.5, resize: "vertical",
              fontFamily: "inherit",
            }}
            autoFocus
          />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button
              onClick={() => { setEditing(false); setDraftNote(bookmark.note || ""); }}
              style={{
                background: "transparent", color: TOKENS.ink3, border: "none",
                fontSize: 11.5, cursor: "pointer", padding: "5px 10px",
              }}
            >
              Cancel
            </button>
            <button
              onClick={() => { onSaveNote(draftNote.trim()); setEditing(false); }}
              style={{
                background: TOKENS.brand, color: "#fff", border: "none",
                fontSize: 11.5, fontWeight: 600, cursor: "pointer",
                padding: "5px 12px", borderRadius: 6,
              }}
            >
              Save note
            </button>
          </div>
        </div>
      ) : bookmark.note ? (
        <button
          onClick={() => { setDraftNote(bookmark.note || ""); setEditing(true); }}
          style={{
            background: "#fffbf3", border: `1px solid #f0e2c8`,
            borderRadius: 10, padding: "8px 10px",
            fontSize: 12, color: TOKENS.ink2, fontStyle: "italic",
            textAlign: "left", width: "100%", cursor: "pointer",
            lineHeight: 1.4,
          }}
        >
          "{bookmark.note}"
        </button>
      ) : (
        <button
          onClick={() => setEditing(true)}
          style={{
            background: "transparent", border: `1px dashed ${TOKENS.line}`,
            borderRadius: 10, padding: "6px 10px",
            fontSize: 11.5, color: TOKENS.ink4,
            textAlign: "left", width: "100%", cursor: "pointer",
          }}
        >
          + Add a note
        </button>
      )}

      {/* Controls */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8, marginTop: 8,
        paddingTop: 8, borderTop: `1px dashed ${TOKENS.line}`,
        fontSize: 11.5,
      }}>
        <label style={{ color: TOKENS.ink4 }}>Section</label>
        <select
          value={bookmark.section}
          onChange={(e) => onMoveSection(e.target.value as WikiSection)}
          style={{
            background: "#fff", border: `1px solid ${TOKENS.line}`,
            borderRadius: 6, padding: "3px 6px",
            fontSize: 11.5, color: TOKENS.ink2,
          }}
        >
          {SECTIONS.map((s) => (
            <option key={s} value={s}>{SECTION_LABEL[s]}</option>
          ))}
        </select>
        <button
          onClick={onRemove}
          style={{
            marginLeft: "auto",
            background: "transparent", color: TOKENS.critical, border: "none",
            fontSize: 11.5, cursor: "pointer", padding: 0,
          }}
        >
          Remove
        </button>
      </div>
    </div>
  );
}
