/** Bottom navigation — Phase 3I: Feeds / Home (center) / Saved, orange active */

import { useLocation, useNavigate } from "react-router-dom";

const TABS = [
  { path: "/home", label: "Home", icon: HomeIcon },
  { path: "/feed", label: "Feeds", icon: FeedIcon },
  { path: "/saved", label: "Saved", icon: SavedIcon },
] as const;

const ACTIVE_COLOR = "#df5900";
const INACTIVE_COLOR = "#888888";

export function BottomNav() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 h-14 bg-white border-t border-gray-200 flex items-center justify-around z-40 safe-area-bottom"
      style={{ maxWidth: "440px", margin: "0 auto" }}
    >
      {TABS.map(({ path, label, icon: Icon }) => {
        const active = location.pathname === path;
        return (
          <button
            key={path}
            onClick={() => navigate(path)}
            className="flex flex-col items-center justify-center gap-0.5 px-4 py-1.5 transition-colors"
            style={{ color: active ? ACTIVE_COLOR : INACTIVE_COLOR }}
          >
            <Icon active={active} />
            <span style={{ fontSize: "15px", fontWeight: 400 }}>{label}</span>
          </button>
        );
      })}
    </nav>
  );
}

function FeedIcon({ active }: { active: boolean }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" />
    </svg>
  );
}

function HomeIcon({ active }: { active: boolean }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill={active ? "currentColor" : "none"} stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      <polyline points="9 22 9 12 15 12 15 22" />
    </svg>
  );
}

function SavedIcon({ active }: { active: boolean }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill={active ? "currentColor" : "none"} stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    </svg>
  );
}
