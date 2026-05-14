/**
 * Phase C — useChatStream hook.
 *
 * Wraps `streamChat` in a small state machine so a React component can:
 *
 *   const { send, messages, status } = useChatStream();
 *   send({ conversation_id: cid, message: "summarise water risk" });
 *
 * The hook buffers tokens into the most recent assistant message,
 * exposes status (`idle | streaming | done | error`), and tears down
 * the underlying fetch on unmount.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { streamChat } from "@/lib/api";

interface StreamMessage {
  role: "user" | "assistant";
  content: string;
  events?: Array<{ event: string; data: Record<string, unknown> }>;
}

export type StreamStatus = "idle" | "streaming" | "done" | "error";

export function useChatStream(initial: StreamMessage[] = []) {
  const [messages, setMessages] = useState<StreamMessage[]>(initial);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => () => {
    cancelRef.current?.();
  }, []);

  const send = useCallback((req: {
    conversation_id: string | null;
    message: string;
    signoff?: string;
  }) => {
    setStatus("streaming");
    setMessages((prev) => [
      ...prev,
      { role: "user", content: req.message },
      { role: "assistant", content: "", events: [] },
    ]);
    cancelRef.current = streamChat(
      req,
      (event, data) => {
        if (event === "stream_start" && data?.conversation_id) {
          setConversationId(String(data.conversation_id));
        }
        if (event === "token" && typeof data?.delta === "string") {
          setMessages((prev) => {
            const next = prev.slice();
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = {
                ...last, content: last.content + String(data.delta),
              };
            }
            return next;
          });
        } else {
          // Append metadata-style events for richer UIs
          setMessages((prev) => {
            const next = prev.slice();
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                events: [...(last.events ?? []), { event, data }],
              };
            }
            return next;
          });
          if (event === "done") setStatus("done");
          if (event === "error") setStatus("error");
        }
      },
      (err) => {
        // Network or HTTP failure — surface a synthetic error event
        setStatus("error");
        setMessages((prev) => {
          const next = prev.slice();
          const last = next[next.length - 1];
          if (last?.role === "assistant") {
            next[next.length - 1] = {
              ...last,
              events: [...(last.events ?? []), {
                event: "error",
                data: { message: String(err) },
              }],
            };
          }
          return next;
        });
      },
    );
  }, []);

  return { messages, status, send, conversationId };
}
