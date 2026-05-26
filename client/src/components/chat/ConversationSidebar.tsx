/**
 * Phase C — Conversation sidebar (read-only listing + actions).
 *
 * Renders the user's recent conversations on the left side of the chat
 * page. Clicking a row rehydrates the conversation by id. Each row has
 * inline actions (rename, archive) — fork is deferred to a context menu.
 *
 * Today this is unrendered (no parent imports it yet) — it's a small
 * helper available for the next chat-page refactor.
 */
import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";

import { conversations, type ConversationSummary } from "@/lib/api";
import { Button } from "@/components/ui/Button";

interface ConversationSidebarProps {
  activeId: string | null;
  onSelect: (cid: string) => void;
  onNew: () => void;
  // Phase 31 — when the user deletes the currently-active conversation
  // the parent needs to clear its local view (drop messages + reset
  // activeId so the page doesn't try to re-fetch a tombstoned id).
  onDeleted?: (cid: string) => void;
}

export function ConversationSidebar({ activeId, onSelect, onNew, onDeleted }: ConversationSidebarProps) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => conversations.list({ limit: 50 }),
    staleTime: 30_000,
  });
  const archive = useMutation({
    mutationFn: (cid: string) => conversations.archive(cid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const rename = useMutation({
    mutationFn: ({ cid, title }: { cid: string; title: string }) =>
      conversations.rename(cid, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const del = useMutation({
    mutationFn: (cid: string) => conversations.delete(cid),
    onSuccess: (_, cid) => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
      if (onDeleted) onDeleted(cid);
    },
  });
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  return (
    <aside className="flex h-full w-64 flex-col border-r border-gray-200 bg-gray-50 p-3">
      <Button onClick={onNew} className="mb-3 w-full">+ New conversation</Button>
      {isLoading && <div className="text-xs text-gray-500">Loading…</div>}
      <ul className="flex-1 overflow-y-auto text-sm">
        {data?.conversations.map((c: ConversationSummary) => (
          <li
            key={c.conversation_id}
            className={`mb-1 rounded px-2 py-1 cursor-pointer ${
              c.conversation_id === activeId ? "bg-blue-100" : "hover:bg-white"
            }`}
            onClick={() => onSelect(c.conversation_id)}
          >
            {renamingId === c.conversation_id ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (renameValue.trim()) {
                    rename.mutate({ cid: c.conversation_id, title: renameValue.trim() });
                  }
                  setRenamingId(null);
                }}
              >
                <input
                  className="w-full rounded border px-1 text-xs"
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={() => setRenamingId(null)}
                  autoFocus
                />
              </form>
            ) : (
              <div className="flex items-center justify-between">
                <span className="truncate" title={c.title ?? "untitled"}>
                  {c.title ?? "untitled"}
                </span>
                <span className="text-[10px] text-gray-400 ml-1">{c.message_count}</span>
              </div>
            )}
            <div className="flex gap-2 mt-1 text-[10px] text-gray-500">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setRenameValue(c.title ?? "");
                  setRenamingId(c.conversation_id);
                }}
                className="hover:text-blue-600"
              >
                rename
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  archive.mutate(c.conversation_id);
                }}
                className="hover:text-amber-600"
              >
                archive
              </button>
              {confirmDeleteId === c.conversation_id ? (
                <span className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => {
                      del.mutate(c.conversation_id);
                      setConfirmDeleteId(null);
                    }}
                    className="text-red-600 font-semibold hover:underline"
                  >
                    confirm
                  </button>
                  <span className="text-gray-300">·</span>
                  <button
                    onClick={() => setConfirmDeleteId(null)}
                    className="hover:text-gray-700"
                  >
                    cancel
                  </button>
                </span>
              ) : (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmDeleteId(c.conversation_id);
                  }}
                  className="hover:text-red-600"
                  title="Permanently delete this conversation"
                >
                  delete
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
