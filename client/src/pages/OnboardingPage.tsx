/**
 * Phase 3B: Onboarding flow — matches UX/Intro, Loading 1-4, Profile setup complete.
 * Multi-step: Welcome → Loading (4 stages) → Complete
 */

import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/authStore";
import { auth } from "../lib/api";
import { COLORS } from "../lib/designTokens";

const LOADING_STEPS = [
  { text: "Loading company data...", width: "25%" },
  { text: "Loading industry...", width: "50%" },
  { text: "Loading frameworks...", width: "75%" },
  { text: "Loading competitors...", width: "90%" },
];

type Step = "loading" | "complete";

export default function OnboardingPage() {
  const navigate = useNavigate();
  const loginStore = useAuthStore((s) => s.login);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [step, setStep] = useState<Step>("loading");
  const [loadingIndex, setLoadingIndex] = useState(0);

  // Complete pending login in background while loading screens show
  useEffect(() => {
    const pendingRaw = sessionStorage.getItem("pending_login");
    if (pendingRaw && !isAuthenticated) {
      try {
        const pending = JSON.parse(pendingRaw);
        // Only attempt re-login if we have actual credentials (not just a completion flag)
        if (pending.email && pending.domain) {
          auth.login(pending).then((result) => {
            loginStore(result);
            sessionStorage.removeItem("pending_login");
          }).catch(() => {
            sessionStorage.removeItem("pending_login");
            navigate("/login", { replace: true });
          });
        } else {
          // No credentials stored — redirect to login
          sessionStorage.removeItem("pending_login");
          navigate("/login", { replace: true });
        }
      } catch {
        sessionStorage.removeItem("pending_login");
        navigate("/login", { replace: true });
      }
    }
  }, [isAuthenticated, loginStore, navigate]);

  // Advance loading steps — 2 seconds per step (matches backend processing time)
  useEffect(() => {
    if (step === "loading") {
      if (loadingIndex < LOADING_STEPS.length) {
        const timer = setTimeout(
          () => setLoadingIndex((i) => i + 1),
          2000,
        );
        return () => clearTimeout(timer);
      } else {
        const timer = setTimeout(() => setStep("complete"), 500);
        return () => clearTimeout(timer);
      }
    }
  }, [step, loadingIndex]);

  useEffect(() => {
    if (step === "complete") {
      const timer = setTimeout(() => {
        localStorage.setItem("onboarding_complete", "true");
        sessionStorage.removeItem("pending_login");
        navigate("/home");
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [step, navigate]);

  return (
    <div
      className="max-w-[440px] mx-auto min-h-screen relative"
      style={{ backgroundColor: COLORS.bgWhite, height: "956px" }}
    >
      {/* Snowkap logo — using actual asset */}
      <img
        src="/assets/snowkap-icon.png"
        alt="Snowkap"
        className="absolute"
        style={{ top: "62px", left: "47px", width: "40px", height: "40px" }}
      />

      {/* LOADING STEPS — starts immediately */}
      {step === "loading" && loadingIndex < LOADING_STEPS.length && (
        <div
          className="flex items-center justify-center"
          style={{ height: "100%", paddingLeft: "47px", paddingRight: "47px" }}
        >
          <p style={{ fontSize: "24px", textAlign: "center" }}>
            <span style={{ color: COLORS.brand }}>Loading </span>
            <span style={{ color: COLORS.textMuted }}>
              {LOADING_STEPS[loadingIndex]?.text.replace("Loading ", "") ?? ""}
            </span>
          </p>
        </div>
      )}

      {/* COMPLETE STEP */}
      {step === "complete" && (
        <div
          className="flex items-center justify-center"
          style={{ height: "100%", paddingLeft: "47px", paddingRight: "47px" }}
        >
          <p
            style={{
              fontSize: "24px",
              color: COLORS.brand,
              textAlign: "center",
              width: "346px",
            }}
          >
            Profile Setup is now complete
          </p>
        </div>
      )}

      {/* Progress bar at bottom — matches UX position top:946px */}
      <div
        className="absolute"
        style={{
          bottom: "0px",
          left: "0px",
          height: "10px",
          backgroundColor: COLORS.brand,
          borderRadius: "0 5px 0 0",
          transition: "width 0.4s ease-out",
          width:
            step === "loading" && loadingIndex < LOADING_STEPS.length
              ? (LOADING_STEPS[loadingIndex]?.width ?? "100%")
              : "100%",
        }}
      />
    </div>
  );
}
