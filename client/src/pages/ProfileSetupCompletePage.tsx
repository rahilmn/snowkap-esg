/**
 * Phase 34.2 — Profile-setup-complete confirmation screen.
 *
 * Sits between the profile-submission step and the live onboarding
 * progress page. Gives the user a celebratory "all set" moment in the
 * Power-of-Now visual language, then auto-advances to
 * `/onboarding/{slug}` after 1.8 seconds (or instantly on tap).
 *
 * Route: `/welcome/profile-setup-complete?slug={slug}`
 */
import { useEffect, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { IPhoneFrame } from "@/components/ui/IPhoneFrame";
import { TOKENS } from "@/lib/designTokensV2";

export default function ProfileSetupCompletePage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const slug = params.get("slug") || "";
  const company = params.get("company") || "";

  const target = useMemo(() => (slug ? `/onboarding/${slug}` : "/home"), [slug]);

  useEffect(() => {
    const id = setTimeout(() => navigate(target, { replace: true }), 1800);
    return () => clearTimeout(id);
  }, [navigate, target]);

  return (
    <IPhoneFrame>
      <div style={{
        position: "absolute", inset: 0,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        padding: "0 32px", textAlign: "center",
        cursor: "pointer",
        background: "linear-gradient(180deg, #fffaf2 0%, #fff5ea 100%)",
      }} onClick={() => navigate(target, { replace: true })}>
        {/* Success orb */}
        <div style={{
          width: 96, height: 96, borderRadius: 999,
          background: `radial-gradient(circle at 30% 30%, #ffd1a8 0%, ${TOKENS.brand} 90%)`,
          display: "flex", alignItems: "center", justifyContent: "center",
          marginBottom: 28,
          boxShadow: "0 22px 50px rgba(223, 89, 0, 0.32)",
        }}>
          <svg width="42" height="42" viewBox="0 0 24 24" fill="none">
            <path
              d="M5 12.5l5 5L19 7"
              stroke="#fff" strokeWidth="2.6"
              strokeLinecap="round" strokeLinejoin="round"
            />
          </svg>
        </div>

        <span style={{
          fontSize: 11, color: TOKENS.brand, letterSpacing: "0.08em",
          fontWeight: 700, textTransform: "uppercase", marginBottom: 8,
        }}>
          Profile set
        </span>

        <h1 className="serif" style={{
          margin: 0,
          fontSize: 30, fontWeight: 500, color: TOKENS.ink,
          letterSpacing: "-0.02em", lineHeight: 1.15,
        }}>
          You're all set.
        </h1>

        <p style={{
          margin: "14px 0 0",
          fontSize: 14, color: TOKENS.ink3, lineHeight: 1.5,
          maxWidth: 280,
        }}>
          {company
            ? <>We're fetching the latest ESG news for <strong style={{ color: TOKENS.ink2 }}>{company}</strong> and analysing the top three critical signals.</>
            : <>We're fetching the latest ESG news and analysing the top three critical signals for you.</>}
        </p>

        <p style={{
          margin: "26px 0 0",
          fontSize: 11.5, color: TOKENS.ink4, letterSpacing: "0.02em",
        }}>
          Tap to continue · auto-advance in 2s
        </p>
      </div>
    </IPhoneFrame>
  );
}
