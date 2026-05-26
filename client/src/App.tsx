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
import { NowPage } from "@/pages/NowPage";
import { WikiPage } from "@/pages/WikiPage";
import { ForumPage } from "@/pages/ForumPage";
import { AskPage } from "@/pages/AskPage";
import { PersistentChatPage } from "@/pages/PersistentChatPage";  // Phase C — power-user surface (kept reachable, not in nav)
import SplashPage from "@/pages/SplashPage";
import IntroPage from "@/pages/IntroPage";
import OnboardingPage from "@/pages/OnboardingPage";
import PreferencesPage from "@/pages/PreferencesPage";
import SettingsCampaignsPage from "@/pages/SettingsCampaignsPage";
import SettingsOnboardPage from "@/pages/SettingsOnboardPage";
import AdminDiscoveryPage from "@/pages/AdminDiscoveryPage";
import AdvisorPage from "@/pages/AdvisorPage";
import AdminAutoresearcherPage from "@/pages/AdminAutoresearcherPage";
import SettingsBatchOnboardPage from "@/pages/SettingsBatchOnboardPage";
import ProfilePage from "@/pages/ProfilePage";  // W2: self-service profile
import OnboardingProgressPage from "@/pages/OnboardingProgressPage";  // W2: onboarding poll page
import ProfileSetupCompletePage from "@/pages/ProfileSetupCompletePage";  // Phase 34.2: FTUX confirmation step

/** Entry point — decides where to send the user.
 *  Phase 34 — Power-of-Now is the canonical mobile-first surface; the
 *  legacy `/home` dashboard remains reachable directly for desktop
 *  power-users but is no longer the default landing.
 */
function EntryRedirect() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (!isAuthenticated) {
    return <Navigate to="/splash" replace />;
  }
  // Authenticated users skip onboarding — auto-mark complete
  if (!localStorage.getItem("onboarding_complete")) {
    localStorage.setItem("onboarding_complete", "true");
  }
  return <Navigate to="/now" replace />;
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
    return <Navigate to="/now" replace />;
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
                {/* Power of Now surfaces (canonical post-POW-6) */}
                <Route path="/now" element={<NowPage />} />
                <Route path="/wiki" element={<WikiPage />} />
                <Route path="/forum" element={<ForumPage />} />
                <Route path="/ask" element={<AskPage />} />
                {/* POW-6 — legacy `/home`, `/feed`, `/saved`, `/agent`
                    retired. Any stray link/bookmark falls through to
                    the `path="*"` catch-all at the bottom of this
                    Routes block and lands on /now. */}
                <Route path="/home" element={<Navigate to="/now" replace />} />
                <Route path="/feed" element={<Navigate to="/now" replace />} />
                <Route path="/saved" element={<Navigate to="/wiki" replace />} />
                <Route path="/agent" element={<Navigate to="/ask" replace />} />
                {/* PersistentChatPage stays reachable for power-users only
                    (not exposed in the bottom nav). New chat surface is /ask. */}
                <Route path="/chat" element={<PersistentChatPage />} />
                <Route path="/preferences" element={<PreferencesPage />} />
                {/* W2: self-service profile + onboarding for any signed-in user */}
                <Route path="/profile" element={<ProfilePage />} />
                <Route path="/onboarding/:slug" element={<OnboardingProgressPage />} />
                {/* Phase 34.2 — Power-of-Now FTUX celebratory confirmation between profile + loading */}
                <Route path="/welcome/profile-setup-complete" element={<ProfileSetupCompletePage />} />
                {/* Phase 10: drip campaigns (gated inside the page by manage_drip_campaigns) */}
                <Route path="/settings/campaigns" element={<SettingsCampaignsPage />} />
                {/* Phase 16.1: admin onboarding for new prospect companies */}
                <Route path="/settings/onboard" element={<SettingsOnboardPage />} />
                {/* Phase 25 W6: batch onboarding from HubSpot CSV */}
                <Route path="/settings/onboard/batch" element={<SettingsBatchOnboardPage />} />
                {/* Phase 24 W2: self-evolving ontology review queue */}
                <Route path="/settings/discovery" element={<AdminDiscoveryPage />} />
                {/* Base Version Adoption L6: advisor review queue */}
                <Route path="/settings/advisor" element={<AdvisorPage />} />
                {/* Autoresearcher Phase B: calibration loop dashboard */}
                <Route path="/settings/autoresearcher" element={<AdminAutoresearcherPage />} />
                {/* Catch any unknown route → /now (Power of Now is canonical) */}
                <Route path="*" element={<Navigate to="/now" replace />} />
              </Routes>
            </AppLayout>
          </ProtectedRoute>
        }
      />
    </Routes>
    </>
  );
}
