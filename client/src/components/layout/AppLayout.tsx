/** App layout.
 *
 * The Power of Now routes (`/now`, `/wiki`, `/forum`, `/ask`,
 * `/onboarding/*`, `/welcome/*`) own their own iPhone-frame chrome
 * and bottom navigation, so the layout renders the children directly
 * with NO wrapping MinimalHeader / BottomNav / floating chatbot.
 *
 * Legacy/admin routes (`/chat`, `/preferences`, `/profile`, `/settings/*`,
 * `/home`, `/feed`, `/saved`, `/agent`) keep the legacy desktop chrome
 * until POW-6 retires them.
 */

import { type ReactNode } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { MinimalHeader } from "./MinimalHeader";
import { BottomNav } from "./BottomNav";

// Power-of-Now routes that own their full-screen chrome and must NOT be
// wrapped in the legacy MinimalHeader / BottomNav / floating chatbot.
const POWER_OF_NOW_ROUTES = [
  "/now",
  "/wiki",
  "/forum",
  "/ask",
  "/onboarding",
  "/welcome",
] as const;

function isPowerOfNowRoute(pathname: string): boolean {
  return POWER_OF_NOW_ROUTES.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

export function AppLayout({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();

  // Power-of-Now routes render their children full-bleed — no legacy
  // chrome on top. This is what fixed the "Power of Now UI flashes then
  // the old UI paints over it" regression.
  if (isPowerOfNowRoute(location.pathname)) {
    return <>{children}</>;
  }

  const isChatPage = location.pathname === "/agent" || location.pathname === "/chat";

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-background">
      <MinimalHeader />
      <main className="flex-1 overflow-y-auto">{children}</main>

      {/* Floating Chatbot Button — hidden when inside a chat surface */}
      {!isChatPage && (
        <button
          onClick={() => navigate("/ask")}
          title="Ask AI"
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
