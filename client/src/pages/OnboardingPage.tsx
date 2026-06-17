/**
 * /welcome — first-time user lands here after login.
 *
 * User pastes their company domain → we POST /api/me/onboard → redirect to
 * /onboarding/{slug} for the SSE progress stream.
 *
 * Snowkap super-admins can paste any domain. Regular users must paste a
 * domain that matches their email-domain (backend enforces this; we just
 * surface the constraint inline).
 *
 * "Skip for now" sets `onboarding_complete=true` and routes to /home so a
 * super-admin can land on the dashboard immediately without onboarding a
 * new company.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { useAuthStore } from "../stores/authStore";
import { me } from "@/lib/api";
import { COLORS } from "../lib/designTokens";

export default function OnboardingPage() {
  const navigate = useNavigate();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const userDomain = useAuthStore((s) => s.domain);
  const [domain, setDomain] = useState(userDomain || "");
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    const hasPendingFlag = !!sessionStorage.getItem("pending_login");
    if (!isAuthenticated && !hasPendingFlag) {
      navigate("/login", { replace: true });
    }
  }, [isAuthenticated, navigate]);

  const onboardMutation = useMutation({
    mutationFn: (d: string) => me.onboard(d, 10),
    onSuccess: (data) => {
      setSubmitError(null);
      localStorage.setItem("onboarding_complete", "true");
      sessionStorage.removeItem("pending_login");
      navigate(`/onboarding/${data.slug}`);
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Onboarding failed.";
      setSubmitError(msg);
    },
  });

  const trimmed = domain.trim().toLowerCase();
  const canSubmit =
    trimmed.length >= 3 && trimmed.includes(".") && !onboardMutation.isPending;

  const handleSkip = () => {
    localStorage.setItem("onboarding_complete", "true");
    sessionStorage.removeItem("pending_login");
    navigate("/now", { replace: true });  // /home is a dead redirect -> /now (avoid the double hop)
  };

  return (
    <div
      className="max-w-[480px] mx-auto min-h-screen relative"
      style={{ backgroundColor: COLORS.bgWhite, padding: "62px 32px 24px" }}
    >
      <img
        src="/assets/snowkap-icon.png"
        alt="Snowkap"
        style={{ width: 40, height: 40, marginBottom: 24 }}
      />

      <h1
        style={{
          fontSize: 26,
          fontWeight: 700,
          color: COLORS.textPrimary,
          margin: "0 0 10px",
          lineHeight: 1.25,
        }}
      >
        Let's set up your company
      </h1>
      <p
        style={{
          fontSize: 14,
          color: COLORS.textMuted,
          margin: "0 0 28px",
          lineHeight: 1.55,
        }}
      >
        Paste your company's website. Snowkap looks up your industry,
        regulatory region, and sustainability signals — then streams the 3
        most critical ESG stories back in ~4 minutes.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!canSubmit) return;
          setSubmitError(null);
          onboardMutation.mutate(trimmed);
        }}
        style={{ display: "flex", flexDirection: "column", gap: 14 }}
      >
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: COLORS.textPrimary }}>
            Company website
          </span>
          <input
            type="text"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="yourcompany.com"
            autoComplete="off"
            autoFocus
            style={{
              padding: "12px 14px",
              fontSize: 15,
              border: "1px solid #E2E8F0",
              borderRadius: 10,
              fontFamily: "inherit",
              outline: "none",
            }}
          />
          <span
            style={{
              fontSize: 11,
              color: COLORS.textMuted,
              lineHeight: 1.5,
            }}
          >
            Just the domain — no <code>https://</code>, no <code>www.</code>.
            {userDomain ? (
              <>
                {" "}Must match your email domain (<code>{userDomain}</code>) unless
                you're a Snowkap admin.
              </>
            ) : null}
          </span>
        </label>

        {submitError && (
          <div
            style={{
              padding: "10px 14px",
              borderRadius: 8,
              background: "#FEF2F2",
              border: "1px solid #FECACA",
              color: "#DC2626",
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            {submitError}
          </div>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          style={{
            padding: "12px 18px",
            fontSize: 15,
            fontWeight: 600,
            background: canSubmit ? COLORS.brand : "#E2E8F0",
            color: canSubmit ? "#fff" : "#94A3B8",
            border: "none",
            borderRadius: 10,
            cursor: canSubmit ? "pointer" : "not-allowed",
            transition: "background 0.15s",
          }}
        >
          {onboardMutation.isPending ? "Submitting…" : "Start onboarding"}
        </button>

        <button
          type="button"
          onClick={handleSkip}
          style={{
            padding: "10px 0 0",
            fontSize: 12,
            background: "none",
            color: COLORS.textMuted,
            border: "none",
            cursor: "pointer",
            textAlign: "center",
          }}
        >
          Skip for now — explore Snowkap with example companies
        </button>
      </form>
    </div>
  );
}
