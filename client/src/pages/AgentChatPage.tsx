import { useState, useRef, useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { StructuredMessageRenderer } from "@/components/panels/StructuredMessageRenderer";
import { ArticleChatContext } from "@/components/panels/ArticleChatContext";
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
  const location = useLocation();
  const articleContext = location.state as {
    articleId?: string;
    articleTitle?: string;
    articleSummary?: string;
    priorityLevel?: string;
    contentType?: string;
    frameworks?: string[];
    impactScore?: number;
    explanation?: string;
    executiveInsight?: string;
    // v2 context
    topRiskName?: string;
    topRiskScore?: number;
    topRiskClass?: string;
    tonePrimary?: string;
    primaryTheme?: string;
    frameworkCount?: number;
    aggregateRisk?: number;
    riskMode?: string;
    relevanceScore?: number;
  } | null;
  const [input, setInput] = useState("");
  const [pendingActions, setPendingActions] = useState<PendingAction[]>([]);
  const currentArticleId = articleContext?.articleId ?? null;
  const [conversationId, setConversationId] = useState(() => `conv_${Date.now()}`);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevArticleIdRef = useRef<string | null>(currentArticleId);
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

  // Reset chat when article changes
  useEffect(() => {
    if (prevArticleIdRef.current !== currentArticleId) {
      clearMessages();
      setPendingActions([]);
      setConversationId(`conv_${Date.now()}`);
      prevArticleIdRef.current = currentArticleId;
    }
  }, [currentArticleId, clearMessages]);

  // Load available agents
  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: agent.listAgents,
  });

  useEffect(() => {
    if (agentsQuery.data) setAgents(agentsQuery.data);
  }, [agentsQuery.data, setAgents]);

  // Handle quick-action prompt from ArticleChatContext
  function handleQuickPrompt(prompt: string) {
    setInput(prompt);
    // Auto-send after a tick so the input is visible
    setTimeout(() => {
      addMessage({ role: "user", content: prompt });
      setLoading(true);
      agent.chat(prompt, selectedAgent ?? undefined, conversationId, articleContext?.articleId)
        .then((result) => {
          addMessage({ role: "assistant", content: result.response, agent: result.agent });
          if (result.pending_actions && result.pending_actions.length > 0) setPendingActions((prev) => [...prev, ...result.pending_actions]);
        })
        .catch((e) => {
          addMessage({ role: "assistant", content: `Error: ${e instanceof Error ? e.message : "Unknown"}` });
        })
        .finally(() => { setLoading(false); setInput(""); });
    }, 100);
  }

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
      const result = await agent.chat(
        question,
        selectedAgent ?? undefined,
        conversationId,
        articleContext?.articleId,  // Pass article_id for ontology-driven context
      );
      addMessage({
        role: "assistant",
        content: result.response,
        agent: result.agent,
      });

      // Check for pending actions that need confirmation
      if (result.pending_actions && result.pending_actions.length > 0) {
        setPendingActions((prev) => [...prev, ...result.pending_actions]);
      }
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : "Failed to get response";
      console.error("Agent chat error:", e);
      addMessage({
        role: "assistant",
        content: `I encountered an issue. ${errMsg.includes("500") ? "The server is processing — please try again in a moment." : errMsg}`,
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
    <div
      className="flex gap-4 md:gap-6 h-[calc(100vh-8rem)]"
      style={{
        background: "radial-gradient(circle at center, #ffffff 0%, #f7f9fb 50%, #e1f6ff 100%)",
      }}
    >
      {/* Mobile Agent Selector — hidden (auto-route handles routing) */}

      {/* Agent Selector Sidebar — hidden on mobile */}
      <div className="hidden md:block w-56 flex-shrink-0 space-y-3">
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
          {messages.length === 0 && articleContext?.articleTitle && (
            <ArticleChatContext
              articleTitle={articleContext.articleTitle}
              priorityLevel={articleContext.priorityLevel}
              relevanceScore={articleContext.relevanceScore}
              primaryTheme={articleContext.primaryTheme}
              topRiskName={articleContext.topRiskName}
              topRiskClass={articleContext.topRiskClass}
              riskMode={articleContext.riskMode}
              frameworkCount={articleContext.frameworkCount}
              onSendPrompt={handleQuickPrompt}
            />
          )}

          {messages.length === 0 && !articleContext?.articleTitle && (
            <div className="text-center text-muted-foreground py-12">
              <p className="text-lg font-medium mb-2">Ask anything about ESG</p>
              <p className="text-sm">The AI will route your question to the best specialist agent.</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-6 max-w-md mx-auto">
                {[
                  "What are the top ESG risks for our company this quarter?",
                  "How do our competitors compare on sustainability?",
                  "Analyze our BRSR compliance gaps with specific remediation steps",
                  "What supply chain risks should we prioritize based on recent news?",
                  "Generate a board briefing on our ESG positioning vs industry peers",
                  "Which facilities face climate risk and what should we do about it?",
                ].map((q) => (
                  <button
                    key={q}
                    className="text-left text-xs rounded-md border p-2 hover:border-primary transition-colors"
                    onClick={() => setInput(q)}
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
              id="agent-chat-input"
              name="agent-chat-input"
              placeholder="Ask about ESG, supply chain, compliance, predictions..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              disabled={isLoading}
              autoComplete="off"
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
        <div className="prose prose-sm max-w-none dark:prose-invert [&>p]:mb-2 [&>p:last-child]:mb-0 [&>ul]:mb-2 [&>ol]:mb-2">
          {isUser ? (
            <ReactMarkdown skipHtml={true}>{message.content}</ReactMarkdown>
          ) : (
            <StructuredMessageRenderer content={message.content} />
          )}
        </div>
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
