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
import { useEffect, useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ConversationSidebar } from "@/components/chat/ConversationSidebar";
import { ToulminBadge } from "@/components/chat/ToulminBadge";
import { ToolInvocationCard } from "@/components/chat/ToolInvocationCard";
import { AdvisorHintCard } from "@/components/chat/AdvisorHintCard";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { useChatStream } from "@/hooks/useChatStream";
import { conversations } from "@/lib/api";

export function PersistentChatPage() {
  const qc = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const { messages, status, send, conversationId } = useChatStream();

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

  const onSend = useCallback(() => {
    const text = draft.trim();
    if (!text || status === "streaming") return;
    setDraft("");
    send({ conversation_id: effectiveId, message: text });
  }, [draft, status, send, effectiveId]);

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

  return (
    <div className="flex h-[calc(100vh-6rem)] gap-3">
      <ConversationSidebar
        activeId={effectiveId}
        onSelect={onSelect}
        onNew={onNew}
      />
      <Card className="flex flex-1 flex-col">
        <CardHeader className="border-b">
          <CardTitle className="text-base">
            {effectiveId
              ? historyQuery.data?.summary.title ?? "Conversation"
              : "New conversation"}
          </CardTitle>
        </CardHeader>
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {renderedMessages.length === 0 && (
            <div className="text-center text-sm text-muted-foreground py-12">
              <p>Send a message to start a persistent conversation.</p>
              <p className="mt-2 text-xs">
                Replies stream in real time. Past conversations live in the
                sidebar.
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
        <div className="border-t p-3 flex gap-2">
          <Input
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
          />
          <Button onClick={onSend} disabled={!draft.trim() || status === "streaming"}>
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
      <div className="whitespace-pre-wrap text-sm">{msg.content}</div>
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
