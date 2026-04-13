/**
 * App routes — Production flow:
 * New user: / → /splash (3s) → /login → /welcome → /home
 * Returning user: / → /home (skips everything)
 */

import { Routes, Route, Navigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { AppLayout } from "@/components/layout/AppLayout";
import { LoginPage } from "@/pages/LoginPage";
import { SwipeFeedPage } from "@/pages/SwipeFeedPage";
import { SavedNewsPage } from "@/pages/SavedNewsPage";
import { AgentChatPage } from "@/pages/AgentChatPage";
import SplashPage from "@/pages/SplashPage";
import IntroPage from "@/pages/IntroPage";
import OnboardingPage from "@/pages/OnboardingPage";
import HomePage from "@/pages/HomePage";
import PreferencesPage from "@/pages/PreferencesPage";

/** Entry point — decides where to send the user */
function EntryRedirect() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (!isAuthenticated) {
    return <Navigate to="/splash" replace />;
  }
  // Authenticated users skip onboarding — auto-mark complete
  if (!localStorage.getItem("onboarding_complete")) {
    localStorage.setItem("onboarding_complete", "true");
  }
  return <Navigate to="/home" replace />;
}

/** Protects app routes — redirects to splash if not logged in */
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  if (!isAuthenticated) return <Navigate to="/splash" replace />;

  // If authenticated, auto-mark onboarding as complete (handles domain changes / fresh browsers)
  if (!localStorage.getItem("onboarding_complete")) {
    localStorage.setItem("onboarding_complete", "true");
  }

  return <>{children}</>;
}

/** Allows access during pending login flow (e.g. /welcome onboarding page) */
function PendingLoginRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const hasPendingLogin = !!sessionStorage.getItem("pending_login");

  if (!isAuthenticated && !hasPendingLogin) return <Navigate to="/splash" replace />;

  return <>{children}</>;
}

/** Skips public pages if already authenticated */
function PublicRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (isAuthenticated) {
    const onboardingComplete = localStorage.getItem("onboarding_complete");
    if (!onboardingComplete) return <Navigate to="/welcome" replace />;
    return <Navigate to="/home" replace />;
  }
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      {/* Entry point — redirects based on auth state */}
      <Route path="/" element={<EntryRedirect />} />

      {/* Public: Splash → Intro → Login */}
      <Route path="/splash" element={<PublicRoute><SplashPage /></PublicRoute>} />
      <Route path="/intro" element={<PublicRoute><IntroPage /></PublicRoute>} />
      <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />

      {/* Onboarding (authenticated or pending login completing in background) */}
      <Route path="/welcome" element={<PendingLoginRoute><OnboardingPage /></PendingLoginRoute>} />

      {/* Protected app routes */}
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppLayout>
              <Routes>
                <Route path="/home" element={<HomePage />} />
                <Route path="/feed" element={<SwipeFeedPage />} />
                <Route path="/saved" element={<SavedNewsPage />} />
                <Route path="/agent" element={<AgentChatPage />} />
                <Route path="/preferences" element={<PreferencesPage />} />
                {/* Catch any unknown route → home */}
                <Route path="*" element={<Navigate to="/home" replace />} />
              </Routes>
            </AppLayout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
