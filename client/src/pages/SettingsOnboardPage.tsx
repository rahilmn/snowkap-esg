/** Phase 16.1 — Settings / Onboard Company
 *
 * Admin-only page at /settings/onboard. Gated by `manage_drip_campaigns`.
 *
 * Lets a sales / super-admin user add a new prospect company without
 * shelling into the server to run `python scripts/onboard_company.py`.
 * The flow:
 *   1. User enters company name (+ optional ticker hint or domain).
 *   2. POST /api/admin/onboard → returns 202 with the computed slug.
 *   3. Frontend polls /api/admin/onboard/{slug}/status every 5s and
 *      surfaces progress (Fetching 10 articles… → Analysing 3/10… → Ready).
 *   4. On `ready`, shows "Open dashboard →" linking to /home?company={slug}.
 *   5. On `failed`, shows the error string from the status row + "Retry".
 *
 * Backend is Phase 11B endpoints — see api/routes/admin_onboard.py.
 */

import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin as adminApi } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

type OnboardState = "pending" | "fetching" | "analysing" | "ready" | "failed";

const STATE_COPY: Record<OnboardState, { label: string; sub: string; color: string }> = {
  pending: {
    label: "Queued",
    sub: "Waiting for the pipeline pool to pick this up.",
    color: "#94A3B8",
  },
  fetching: {
    label: "Fetching ESG articles",
    sub: "Pulling 10 ESG-filtered articles via NewsAPI.ai + Google News.",
    color: "#3B82F6",
  },
  analysing: {
    label: "Running analysis pipeline",
    sub: "12 stages per article: NLP, themes, frameworks, primitives, recommendations.",
    color: "#DF5900",
  },
  ready: {
    label: "Ready",
    sub: "Open the dashboard to see live insights for this company.",
    color: "#16A34A",
  },
  failed: {
    label: "Failed",
    sub: "Pipeline error — see details below.",
    color: "#DC2626",
  },
};

export default function SettingsOnboardPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  if (!hasPermission("manage_drip_campaigns")) {
    return <Navigate to="/home" replace />;
  }
  return <SettingsOnboardInner />;
}

function SettingsOnboardInner() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [tickerHint, setTickerHint] = useState("");
  const [domain, setDomain] = useState("");
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const onboardMutation = useMutation({
    mutationFn: () =>
      adminApi.onboard({
        name: name.trim(),
        ticker_hint: tickerHint.trim() || undefined,
        domain: domain.trim() || undefined,
        limit: 10,
      }),
    onSuccess: (data) => {
      setActiveSlug(data.slug);
      setSubmitError(null);
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Onboarding failed.";
      setSubmitError(msg);
      setActiveSlug(null);
    },
  });

  // Poll status every 5s while a slug is in-flight.
  // Stop polling once state ∈ {ready, failed} to avoid hammering the API.
  const statusQuery = useQuery({
    queryKey: ["onboard-status", activeSlug],
    queryFn: () => (activeSlug ? adminApi.onboardStatus(activeSlug) : Promise.resolve(null)),
    enabled: !!activeSlug,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 5_000;
      if (data.state === "ready" || data.state === "failed") return false;
      return 5_000;
    },
  });

  // When the status flips to ready, refresh the global tenants/companies cache
  // so the new prospect appears in the CompanySwitcher.
  useEffect(() => {
    if (statusQuery.data?.state === "ready") {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
    }
  }, [statusQuery.data?.state, queryClient]);

  const reset = () => {
    setActiveSlug(null);
    setName("");
    setTickerHint("");
    setDomain("");
    setSubmitError(null);
  };

  const status = statusQuery.data;
  const stateCopy = status ? STATE_COPY[status.state as OnboardState] : null;

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 24px 64px" }}>
      <header style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0, color: "#0F172A" }}>
          Personalize Snowkap for any company
        </h1>
        <p style={{ fontSize: 13, color: "#64748B", margin: "8px 0 0", lineHeight: 1.55 }}>
          Just paste a company website. The system auto-detects the listing
          across NSE / BSE / NYSE / NASDAQ / LSE / Xetra / Euronext / HKEX,
          fetches financials, and tunes 28 ESG news queries to the company's
          regulatory region — then runs 10 articles through the full 12-stage
          analysis pipeline. Typically ready in <strong>~4 minutes</strong>.
        </p>
      </header>

      {!activeSlug && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const haveAny = !!(domain.trim() || name.trim() || tickerHint.trim());
            if (!haveAny || onboardMutation.isPending) return;
            onboardMutation.mutate();
          }}
          style={{
            display: "flex", flexDirection: "column", gap: 18,
            background: "#fff", border: "1px solid #E2E8F0",
            borderRadius: 12, padding: 24,
          }}
        >
          {/* Phase 16 — domain is the primary entry. Name + ticker are
              fall-backs surfaced via a collapsible "advanced" section so
              the default UX is one field. */}
          <Field
            label="Company website"
            value={domain}
            onChange={setDomain}
            placeholder="tatachemicals.com"
            hint="Just the domain — no https://, no www. The resolver finds the rest."
          />

          <details>
            <summary style={{
              cursor: "pointer", fontSize: 12, color: "#64748B",
              userSelect: "none", padding: "4px 0",
            }}>
              Advanced: ticker or company name (optional)
            </summary>
            <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 10 }}>
              <Field
                label="Company name (optional)"
                value={name}
                onChange={setName}
                placeholder="Tata Chemicals"
                hint="Use only if domain resolution doesn't find your company."
              />
              <Field
                label="Ticker hint (optional)"
                value={tickerHint}
                onChange={setTickerHint}
                placeholder="TATACHEM.NS"
                hint="Yfinance symbol. Trumps name + domain when set."
              />
            </div>
          </details>

          {submitError && (
            <div style={{
              padding: "10px 14px", borderRadius: 8,
              background: "#FEF2F2", border: "1px solid #FECACA",
              color: "#DC2626", fontSize: 13,
            }}>
              {submitError}
            </div>
          )}

          <div style={{ display: "flex", gap: 12, marginTop: 4 }}>
            <Button
              type="submit"
              disabled={(!domain.trim() && !name.trim() && !tickerHint.trim()) || onboardMutation.isPending}
            >
              {onboardMutation.isPending ? "Submitting…" : "Personalize Snowkap"}
            </Button>
          </div>
        </form>
      )}

      {activeSlug && status && stateCopy && (
        <div style={{
          background: "#fff", border: "1px solid #E2E8F0",
          borderRadius: 12, padding: 24,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div style={{
                display: "inline-block", padding: "4px 10px", borderRadius: 12,
                background: `${stateCopy.color}1A`, color: stateCopy.color,
                fontSize: 11, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase",
              }}>
                {stateCopy.label}
              </div>
              <h2 style={{ fontSize: 18, fontWeight: 700, margin: "10px 0 6px", color: "#0F172A" }}>
                {name}{" "}
                <span style={{ fontSize: 13, color: "#94A3B8", fontWeight: 500 }}>
                  ({activeSlug})
                </span>
              </h2>
              <p style={{ fontSize: 13, color: "#64748B", margin: 0, lineHeight: 1.5 }}>
                {stateCopy.sub}
              </p>
            </div>
            {(status.state === "fetching" || status.state === "analysing" || status.state === "pending") && (
              <Spinner />
            )}
          </div>

          <div style={{ marginTop: 18, display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
            <Stat label="Fetched" value={status.fetched} total={10} />
            <Stat label="Analysed" value={status.analysed} total={10} />
            <Stat label="HOME tier" value={status.home_count} />
          </div>

          {status.state === "failed" && status.error && (
            <pre style={{
              marginTop: 16, padding: "12px 14px", borderRadius: 8,
              background: "#FEF2F2", border: "1px solid #FECACA",
              color: "#7F1D1D", fontSize: 12, whiteSpace: "pre-wrap", overflow: "auto",
              maxHeight: 240,
            }}>
              {status.error}
            </pre>
          )}

          <div style={{ display: "flex", gap: 12, marginTop: 20, justifyContent: "flex-end" }}>
            {status.state === "ready" && (
              <Button onClick={() => navigate(`/home?company=${activeSlug}`)}>
                Open dashboard →
              </Button>
            )}
            {(status.state === "ready" || status.state === "failed") && (
              <Button variant="ghost" onClick={reset}>
                Onboard another
              </Button>
            )}
          </div>
        </div>
      )}

      <footer style={{ marginTop: 36, fontSize: 11, color: "#94A3B8", lineHeight: 1.6 }}>
        Works for listed companies across NSE, BSE, NYSE, NASDAQ, LSE, Xetra,
        Euronext, and HKEX. If the resolver picks the wrong listing (or fails
        to find one), pass an explicit ticker hint — e.g. <code>TATACHEM.NS</code>,
        <code> AAPL</code>, <code>SAP.DE</code>, <code>BARC.L</code>.
      </footer>
    </div>
  );
}

interface FieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  hint?: string;
  required?: boolean;
}

function Field({ label, value, onChange, placeholder, hint, required }: FieldProps) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: "#0F172A", letterSpacing: 0.2 }}>
        {label}
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        style={{
          padding: "10px 14px", borderRadius: 8,
          border: "1px solid #CBD5E1", fontSize: 14, color: "#0F172A",
          background: "#fff",
        }}
      />
      {hint && (
        <span style={{ fontSize: 11, color: "#94A3B8", lineHeight: 1.4 }}>
          {hint}
        </span>
      )}
    </label>
  );
}

interface StatProps {
  label: string;
  value: number;
  total?: number;
}

function Stat({ label, value, total }: StatProps) {
  return (
    <div style={{
      padding: "12px 14px", borderRadius: 8,
      background: "#F8FAFC", border: "1px solid #E2E8F0",
    }}>
      <div style={{ fontSize: 11, color: "#64748B", letterSpacing: 0.4, textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize: 18, fontWeight: 700, color: "#0F172A", marginTop: 4 }}>
        {value}
        {typeof total === "number" && (
          <span style={{ fontSize: 12, fontWeight: 500, color: "#94A3B8", marginLeft: 4 }}>
            / {total}
          </span>
        )}
      </div>
    </div>
  );
}
