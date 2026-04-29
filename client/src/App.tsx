/**
 * App routes — Production flow:
 * New user: / → /splash (3s) → /login → /welcome → /home
 * Returning user: / → /home (skips everything)
 */

import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { admin as adminApi } from "@/lib/api";
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
import SettingsCampaignsPage from "@/pages/SettingsCampaignsPage";
import SettingsOnboardPage from "@/pages/SettingsOnboardPage";

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

/**
 * Phase 13 B7 — Sync server-side email-backend liveness into the auth
 * store on app boot + after each login. Components that gate UI on the
 * Share button read `useAuthStore((s) => s.emailConfigured)`.
 */
function EmailConfigSync() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setEmailConfig = useAuthStore((s) => s.setEmailConfig);
  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;
    adminApi
      .emailConfigStatus()
      .then((cfg) => {
        if (cancelled) return;
        setEmailConfig(cfg);
      })
      .catch(() => {
        // Swallow — leaves emailConfigured=false which is the safe default.
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, setEmailConfig]);
  return null;
}

export function App() {
  return (
    <>
      <EmailConfigSync />
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
                {/* Phase 10: drip campaigns (gated inside the page by manage_drip_campaigns) */}
                <Route path="/settings/campaigns" element={<SettingsCampaignsPage />} />
                {/* Phase 16.1: admin onboarding for new prospect companies */}
                <Route path="/settings/onboard" element={<SettingsOnboardPage />} />
                {/* Catch any unknown route → home */}
                <Route path="*" element={<Navigate to="/home" replace />} />
              </Routes>
            </AppLayout>
          </ProtectedRoute>
        }
      />
    </Routes>
    </>
  );
}
