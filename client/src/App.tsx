/** App routes — swipe-first navigation (Stage 6.9) */

import { Routes, Route, Navigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { AppLayout } from "@/components/layout/AppLayout";
import { LoginPage } from "@/pages/LoginPage";
import { SwipeFeedPage } from "@/pages/SwipeFeedPage";
import { SavedNewsPage } from "@/pages/SavedNewsPage";
import { AgentChatPage } from "@/pages/AgentChatPage";
import { AdminPage } from "@/pages/AdminPage";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppLayout>
              <Routes>
                <Route path="/" element={<SwipeFeedPage />} />
                <Route path="/saved" element={<SavedNewsPage />} />
                <Route path="/agent" element={<AgentChatPage />} />
                <Route path="/admin" element={<AdminPage />} />
              </Routes>
            </AppLayout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
