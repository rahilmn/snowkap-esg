import { Link, useLocation } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { path: "/", label: "Dashboard", icon: "LayoutDashboard", permission: "view_dashboard" },
  { path: "/news", label: "News Feed", icon: "Newspaper", permission: "view_news" },
  { path: "/predictions", label: "Predictions", icon: "TrendingUp", permission: "view_predictions" },
  { path: "/ontology", label: "Ontology", icon: "Share2", permission: "view_ontology" },
  { path: "/agent", label: "AI Agent", icon: "Bot", permission: "view_dashboard" },
  { path: "/companies", label: "Companies", icon: "Building2", permission: "view_dashboard" },
  { path: "/admin", label: "Admin", icon: "Shield", permission: "manage_users" },
];

// Simple SVG icons to avoid dependency on lucide-react at build time
const ICONS: Record<string, string> = {
  LayoutDashboard: "M3 3h7v7H3V3zm11 0h7v7h-7V3zm-11 11h7v7H3v-7zm11 0h7v7h-7v-7z",
  Newspaper: "M4 4h16v2H4V4zm0 4h10v2H4V8zm0 4h16v2H4v-2zm0 4h10v2H4v-2z",
  TrendingUp: "M3 17l6-6 4 4 8-8M14 7h7v7",
  Share2: "M18 8a3 3 0 100-6 3 3 0 000 6zM6 15a3 3 0 100-6 3 3 0 000 6zM18 22a3 3 0 100-6 3 3 0 000 6zM8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98",
  Bot: "M12 2a2 2 0 012 2c0 .74-.4 1.39-1 1.73V7h1a7 7 0 017 7h1a1 1 0 110 2h-1v1a7 7 0 01-7 7H9a7 7 0 01-7-7v-1H1a1 1 0 110-2h1a7 7 0 017-7h1V5.73c-.6-.34-1-.99-1-1.73a2 2 0 012-2z",
  Building2: "M6 22V2h12v20M6 12H2v10h4M18 12h4v10h-4M10 6h4M10 10h4M10 14h4M10 18h4",
  Shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
};

export function Sidebar() {
  const location = useLocation();
  const permissions = useAuthStore((s) => s.permissions);

  return (
    <aside className="hidden md:flex w-56 flex-col border-r bg-card">
      <div className="flex h-14 items-center border-b px-4">
        <Link to="/" className="flex items-center gap-2">
          <div className="h-7 w-7 rounded bg-primary flex items-center justify-center">
            <span className="text-xs font-bold text-primary-foreground">S</span>
          </div>
          <span className="font-semibold text-sm">SNOWKAP ESG</span>
        </Link>
      </div>

      <nav className="flex-1 space-y-1 p-3">
        {NAV_ITEMS.filter((item) => permissions.includes(item.permission)).map((item) => {
          const isActive = item.path === "/" ? location.pathname === "/" : location.pathname.startsWith(item.path);
          return (
            <Link
              key={item.path}
              to={item.path}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )}
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d={ICONS[item.icon] ?? ""} />
              </svg>
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
