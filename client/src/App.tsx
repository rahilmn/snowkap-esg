import { Routes, Route, Navigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { AppLayout } from "@/components/layout/AppLayout";
import { LoginPage } from "@/pages/LoginPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { NewsFeedPage } from "@/pages/NewsFeedPage";
import { PredictionsPage } from "@/pages/PredictionsPage";
import { OntologyPage } from "@/pages/OntologyPage";
import { AgentChatPage } from "@/pages/AgentChatPage";
import { CompaniesPage } from "@/pages/CompaniesPage";
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
                <Route path="/" element={<DashboardPage />} />
                <Route path="/news" element={<NewsFeedPage />} />
                <Route path="/predictions" element={<PredictionsPage />} />
                <Route path="/ontology" element={<OntologyPage />} />
                <Route path="/agent" element={<AgentChatPage />} />
                <Route path="/companies" element={<CompaniesPage />} />
                <Route path="/admin" element={<AdminPage />} />
              </Routes>
            </AppLayout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
