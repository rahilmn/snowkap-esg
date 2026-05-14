/** W2 — Profile page (self-service company onboarding).
 *
 * Any signed-in user lands here from the user-menu "Profile" link. They can
 * type their company domain and start the onboarding pipeline themselves —
 * no admin involvement.
 *
 * Sections:
 *   1. My Profile — read-only email + role + domain
 *   2. My Company — domain input + "Onboard my company" CTA
 *   3. My Onboarded Companies — quick links back to dashboards they've already
 *      kicked off (read from the admin/tenants list, filtered to their email
 *      domain — non-admins won't see other tenants).
 *
 * Backend: api/routes/profile.py → POST /api/me/onboard {domain}.
 *   The endpoint enforces email-domain match (so pilot@acme.com can only
 *   onboard acme.com). Snowkap super-admins bypass that check. On success
 *   the user is sent to /onboarding/{slug} which polls status + redirects
 *   to /home?company={slug} when ready.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";
import { me } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { COLORS } from "@/lib/designTokens";
import { PersonaMCQ } from "@/components/persona/PersonaMCQ";

export default function ProfilePage() {
  const userId = useAuthStore((s) => s.userId);
  const name = useAuthStore((s) => s.name);
  const designation = useAuthStore((s) => s.designation);
  const userDomain = useAuthStore((s) => s.domain);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const navigate = useNavigate();
  const [domain, setDomain] = useState(userDomain || "");
  const [submitError, setSubmitError] = useState<string | null>(null);

  const onboardMutation = useMutation({
    mutationFn: (d: string) => me.onboard(d, 10),
    onSuccess: (data) => {
      setSubmitError(null);
      navigate(`/onboarding/${data.slug}`);
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Onboarding failed.";
      setSubmitError(msg);
    },
  });

  // Phase 6 — gate the "complete your profile" banner on the persona MCQ
  // state so a user who already filled it out doesn't see the nudge.
  const personaQ = useQuery({
    queryKey: ["persona", "self"],
    queryFn: () => me.getPersona(),
    enabled: !!isAuthenticated && !!userId,
  });

  if (!isAuthenticated || !userId) {
    return (
      <div style={{ padding: 32, textAlign: "center" }}>
        <p style={{ color: COLORS.textMuted }}>Sign in to access your profile.</p>
      </div>
    );
  }

  const trimmed = domain.trim().toLowerCase();
  const canSubmit = trimmed.length >= 3 && trimmed.includes(".") && !onboardMutation.isPending;

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 24px 64px" }}>
      <header style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0, color: COLORS.textPrimary }}>
          Your Profile
        </h1>
        <p style={{ fontSize: 13, color: COLORS.textMuted, margin: "8px 0 0", lineHeight: 1.55 }}>
          Personalise Snowkap for your company. Just paste your website
          below — Snowkap looks up your industry, regulatory region, and
          sustainability painpoints, then runs an analysis pipeline tuned
          to your business. Typically ready in <strong>~4 minutes</strong>.
        </p>
      </header>

      <Section title="My account">
        <Row label="Name" value={name || "—"} />
        <Row label="Email" value={userId} />
        <Row label="Role" value={designation || "—"} />
        <Row label="Email domain" value={userDomain || "—"} />
      </Section>

      {/* Phase 6 — Persona MCQ. Gated behind the auth check above so we
          don't fire the request as an anonymous user. The "Complete your
          profile" hint shows only when the user hasn't saved yet. */}
      <Section
        title="My intelligence preferences"
        subtitle={
          personaQ.data?.mcq_completed === false
            ? "Snowkap can sharpen the feed in 90 seconds — pick the topics, frameworks, and regions that matter to your role."
            : "Update what Snowkap pays attention to on your home feed."
        }
      >
        <PersonaMCQ />
      </Section>

      <Section title="Onboard my company">
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
              style={{
                padding: "10px 12px", fontSize: 14,
                border: "1px solid #E2E8F0", borderRadius: 8,
                fontFamily: "inherit", outline: "none",
              }}
            />
            <span style={{ fontSize: 11, color: COLORS.textMuted, lineHeight: 1.5 }}>
              Just the domain — no <code>https://</code>, no <code>www.</code>. Must match
              your email domain (<code>{userDomain || "your-email.com"}</code>) unless you're a
              Snowkap admin.
            </span>
          </label>

          {submitError && (
            <div style={{
              padding: "10px 14px", borderRadius: 8,
              background: "#FEF2F2", border: "1px solid #FECACA",
              color: "#DC2626", fontSize: 13, lineHeight: 1.5,
            }}>
              {submitError}
            </div>
          )}

          <div style={{ display: "flex", gap: 12, marginTop: 4 }}>
            <Button type="submit" disabled={!canSubmit}>
              {onboardMutation.isPending ? "Submitting…" : "Onboard my company"}
            </Button>
          </div>
        </form>
      </Section>
    </div>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section style={{
      background: "#fff", border: "1px solid #E2E8F0",
      borderRadius: 12, padding: 24, marginBottom: 20,
    }}>
      <h2 style={{
        fontSize: 14, fontWeight: 700, margin: "0 0 6px",
        color: COLORS.textPrimary, textTransform: "uppercase", letterSpacing: "0.05em",
      }}>
        {title}
      </h2>
      {subtitle && (
        <p style={{
          fontSize: 12, color: COLORS.textMuted, margin: "0 0 16px",
          lineHeight: 1.55,
        }}>
          {subtitle}
        </p>
      )}
      {children}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "8px 0", borderBottom: "1px solid #F1F5F9",
    }}>
      <span style={{ fontSize: 12, color: COLORS.textMuted, fontWeight: 500 }}>{label}</span>
      <span style={{ fontSize: 13, color: COLORS.textPrimary, fontWeight: 500 }}>{value}</span>
    </div>
  );
}
