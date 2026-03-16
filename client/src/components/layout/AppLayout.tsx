/** App layout — MinimalHeader + BottomNav, no sidebar (Stage 6.8) */

import { type ReactNode } from "react";
import { MinimalHeader } from "./MinimalHeader";
import { BottomNav } from "./BottomNav";

export function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col h-screen overflow-hidden bg-background">
      <MinimalHeader />
      <main className="flex-1 overflow-y-auto">{children}</main>
      <BottomNav />
    </div>
  );
}
