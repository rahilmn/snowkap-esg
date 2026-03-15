import { create } from "zustand";
import type { ChatMessage, AgentInfo } from "@/types";

interface ChatState {
  messages: ChatMessage[];
  isLoading: boolean;
  selectedAgent: string | null;
  availableAgents: AgentInfo[];

  addMessage: (msg: ChatMessage) => void;
  setLoading: (loading: boolean) => void;
  selectAgent: (agentId: string | null) => void;
  setAgents: (agents: AgentInfo[]) => void;
  clearMessages: () => void;
}

export const useChatStore = create<ChatState>()((set) => ({
  messages: [],
  isLoading: false,
  selectedAgent: null,
  availableAgents: [],

  addMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg] })),
  setLoading: (loading) => set({ isLoading: loading }),
  selectAgent: (agentId) => set({ selectedAgent: agentId }),
  setAgents: (agents) => set({ availableAgents: agents }),
  clearMessages: () => set({ messages: [] }),
}));
