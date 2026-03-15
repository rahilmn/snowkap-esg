import { useState, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { agent } from "@/lib/api";
import { useChatStore } from "@/stores/chatStore";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import type { ChatMessage } from "@/types";

interface PendingAction {
  id: string;
  type: string;
  description: string;
  resource: string;
  status: string;
}

export function AgentChatPage() {
  const [input, setInput] = useState("");
  const [pendingActions, setPendingActions] = useState<PendingAction[]>([]);
  const [conversationId] = useState(() => `conv_${Date.now()}`);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const {
    messages,
    isLoading,
    selectedAgent,
    availableAgents,
    addMessage,
    setLoading,
    selectAgent,
    setAgents,
    clearMessages,
  } = useChatStore();

  // Load available agents
  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: agent.listAgents,
  });

  useEffect(() => {
    if (agentsQuery.data) setAgents(agentsQuery.data);
  }, [agentsQuery.data, setAgents]);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    if (!input.trim() || isLoading) return;

    const question = input.trim();
    setInput("");

    addMessage({ role: "user", content: question });
    setLoading(true);

    try {
      const result = await agent.chat(question, selectedAgent ?? undefined, conversationId);
      addMessage({
        role: "assistant",
        content: result.response,
        agent: result.agent,
      });

      // Check for pending actions that need confirmation
      if (result.pending_actions?.length) {
        setPendingActions((prev) => [...prev, ...result.pending_actions!]);
      }
    } catch (e) {
      addMessage({
        role: "assistant",
        content: `Error: ${e instanceof Error ? e.message : "Failed to get response"}`,
      });
    } finally {
      setLoading(false);
    }
  }

  async function handleConfirmAction(actionId: string) {
    try {
      const result = await agent.confirmAction(actionId, conversationId);
      setPendingActions((prev) => prev.filter((a) => a.id !== actionId));
      addMessage({
        role: "assistant",
        content: `Action confirmed and executed. Result: ${JSON.stringify(result.result)}`,
      });
    } catch (e) {
      addMessage({
        role: "assistant",
        content: `Failed to execute action: ${e instanceof Error ? e.message : "Unknown error"}`,
      });
    }
  }

  async function handleRejectAction(actionId: string) {
    try {
      await agent.rejectAction(actionId, conversationId);
      setPendingActions((prev) => prev.filter((a) => a.id !== actionId));
      addMessage({
        role: "assistant",
        content: "Action rejected.",
      });
    } catch {
      // Silently remove from UI even if backend call fails
      setPendingActions((prev) => prev.filter((a) => a.id !== actionId));
    }
  }

  return (
    <div className="flex gap-6 h-[calc(100vh-8rem)]">
      {/* Agent Selector Sidebar */}
      <div className="w-56 flex-shrink-0 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Specialists</h2>
        </div>
        <button
          className={`w-full text-left rounded-md border p-2 text-xs transition-colors ${
            !selectedAgent ? "border-primary bg-primary/5" : "hover:border-primary/50"
          }`}
          onClick={() => selectAgent(null)}
        >
          <p className="font-medium">Auto-route</p>
          <p className="text-muted-foreground">AI selects the best agent</p>
        </button>
        {availableAgents.map((a) => (
          <button
            key={a.id}
            className={`w-full text-left rounded-md border p-2 text-xs transition-colors ${
              selectedAgent === a.id ? "border-primary bg-primary/5" : "hover:border-primary/50"
            }`}
            onClick={() => selectAgent(a.id)}
          >
            <p className="font-medium">{a.name}</p>
            <div className="flex flex-wrap gap-1 mt-1">
              {a.keywords?.slice(0, 3).map((kw) => (
                <span key={kw} className="bg-muted px-1 rounded text-[10px]">{kw}</span>
              ))}
            </div>
          </button>
        ))}
      </div>

      {/* Chat Area */}
      <Card className="flex-1 flex flex-col">
        <CardHeader className="flex-row items-center justify-between border-b py-3">
          <CardTitle className="text-base">
            {selectedAgent
              ? availableAgents.find((a) => a.id === selectedAgent)?.name ?? "Agent"
              : "ESG AI Agent"}
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={clearMessages}>
            Clear
          </Button>
        </CardHeader>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-muted-foreground py-12">
              <p className="text-lg font-medium mb-2">Ask anything about ESG</p>
              <p className="text-sm">The AI will route your question to the best specialist agent.</p>
              <div className="grid grid-cols-2 gap-2 mt-6 max-w-md mx-auto">
                {[
                  "What's my supply chain risk?",
                  "Show me compliance gaps",
                  "Summarize recent ESG trends",
                  "Draft a BRSR disclosure section",
                ].map((q) => (
                  <button
                    key={q}
                    className="text-left text-xs rounded-md border p-2 hover:border-primary transition-colors"
                    onClick={() => {
                      setInput(q);
                    }}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <MessageBubble key={i} message={msg} />
          ))}

          {/* Pending Actions — Confirmation Dialogs */}
          {pendingActions.map((action) => (
            <ConfirmationCard
              key={action.id}
              action={action}
              onConfirm={handleConfirmAction}
              onReject={handleRejectAction}
            />
          ))}

          {isLoading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="h-4 w-4" />
              Analyzing...
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="border-t p-4">
          <div className="flex gap-2">
            <Input
              placeholder="Ask about ESG, supply chain, compliance, predictions..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              disabled={isLoading}
            />
            <Button onClick={handleSend} disabled={!input.trim() || isLoading}>
              Send
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-lg p-3 text-sm ${
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted"
        }`}
      >
        {!isUser && message.agent && (
          <div className="flex items-center gap-2 mb-1">
            <Badge variant="outline" className="text-[10px]">
              {message.agent.name}
            </Badge>
          </div>
        )}
        <p className="whitespace-pre-line">{message.content}</p>
      </div>
    </div>
  );
}

function ConfirmationCard({
  action,
  onConfirm,
  onReject,
}: {
  action: PendingAction;
  onConfirm: (id: string) => void;
  onReject: (id: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-lg border-2 border-amber-300 bg-amber-50 dark:bg-amber-950/20 p-3 text-sm">
        <div className="flex items-center gap-2 mb-2">
          <Badge className="bg-amber-500 text-white text-[10px]">Action Required</Badge>
        </div>
        <p className="font-medium mb-1">{action.description}</p>
        <p className="text-xs text-muted-foreground mb-3">
          Type: {action.type} | Resource: {action.resource}
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            onClick={async () => {
              setConfirming(true);
              await onConfirm(action.id);
              setConfirming(false);
            }}
            disabled={confirming}
          >
            {confirming ? <Spinner className="h-3 w-3 mr-1" /> : null}
            Confirm
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => onReject(action.id)}
            disabled={confirming}
          >
            Reject
          </Button>
        </div>
      </div>
    </div>
  );
}
