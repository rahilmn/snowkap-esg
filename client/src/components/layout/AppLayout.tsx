/** App layout — MinimalHeader + BottomNav + Floating Chatbot (hidden on /agent) */

import { type ReactNode } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { MinimalHeader } from "./MinimalHeader";
import { BottomNav } from "./BottomNav";
import { useSyncPerspectiveWithRole } from "@/stores/perspectiveStore";

export function AppLayout({ children }: { children: ReactNode }) {
  // Phase 10 / Phase D: pick the default perspective panel (CFO/CEO/ESG
  // Analyst) from the user's active role — unless they've explicitly clicked
  // the PerspectiveSwitcher, in which case their choice wins.
  useSyncPerspectiveWithRole();

  const navigate = useNavigate();
  const location = useLocation();
  const isAgentPage = location.pathname === "/agent";

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-background">
      <MinimalHeader />
      <main className="flex-1 overflow-y-auto">{children}</main>

      {/* Floating Chatbot Button — hidden when inside chat */}
      {!isAgentPage && (
        <button
          onClick={() => navigate("/agent")}
          title="Ask AI Agent"
          style={{
            position: "fixed",
            bottom: "80px",
            right: "20px",
            width: "56px",
            height: "56px",
            borderRadius: "50%",
            backgroundColor: "#fff",
            boxShadow: "0px 4px 16px rgba(0,0,0,0.18)",
            border: "none",
            cursor: "pointer",
            zIndex: 45,
            padding: "8px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <img
            src="/assets/chatbot-icon.png"
            alt="AI Chat"
            style={{ width: "40px", height: "40px", objectFit: "contain" }}
          />
        </button>
      )}

      <BottomNav />
    </div>
  );
}
