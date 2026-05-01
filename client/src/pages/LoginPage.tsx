/**
 * LoginPage — matches UX/Create account/ "Identity Setup" design.
 * Underline-style inputs, black CTA button, Snowkap logo.
 * Keeps existing 3-way auth logic (domain → designation → confirm).
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { auth } from "@/lib/api";
import { COLORS, RADII } from "@/lib/designTokens";

type Step = "domain" | "confirm" | "returning";

/* Underline-only input matching UX Create Account design */
function UnderlineInput({
  label, value, onChange, placeholder, type = "text", onKeyDown, name,
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; type?: string; onKeyDown?: (e: React.KeyboardEvent) => void;
  name?: string;
}) {
  const inputId = name || label.toLowerCase().replace(/\s+/g, "-");
  return (
    <div>
      <label
        htmlFor={inputId}
        style={{ fontSize: "16px", color: COLORS.textMuted, display: "block", marginBottom: "8px" }}
      >
        {label}
      </label>
      <input
        id={inputId}
        name={inputId}
        type={type}
        autoComplete={type === "email" ? "email" : name || "off"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        onKeyDown={onKeyDown}
        style={{
          width: "345px",
          maxWidth: "100%",
          border: "none",
          borderBottom: `1px solid ${COLORS.textMuted}`,
          outline: "none",
          fontSize: "16px",
          padding: "8px 0",
          background: "transparent",
          color: COLORS.textPrimary,
        }}
      />
    </div>
  );
}

export function LoginPage() {
  const navigate = useNavigate();
  const loginStore = useAuthStore((s) => s.login);

  const [step, setStep] = useState<Step>("domain");
  const [domain, setDomain] = useState("");
  const [designation, setDesignation] = useState("ESG Analyst");
  const [companyName, setCompanyName] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [returningEmail, setReturningEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleResolveDomain() {
    setLoading(true);
    setError("");
    try {
      const result = await auth.resolveDomain(domain);
      if (result.company_name) setCompanyName(result.company_name);
      // Designation step removed — default everyone to ESG Analyst and skip
      // straight to the confirm step. Users switch cognitive lens in-app via
      // the PerspectiveSwitcher, not via a static role at login.
      setDesignation("ESG Analyst");
      setStep("confirm");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : typeof e === "string" ? e : "Failed to resolve domain");
    } finally {
      setLoading(false);
    }
  }

  async function handleLogin() {
    setLoading(true);
    setError("");
    try {
      const result = await auth.login({ email, domain, designation, company_name: companyName, name });
      loginStore(result);
      // Presence flag for PendingLoginRoute. We don't store credentials
      // anymore — the login is already complete and the auth store is
      // hydrated, so OnboardingPage just plays the welcome animation.
      sessionStorage.setItem("pending_login", "1");
      navigate("/welcome", { replace: true });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to sign in");
    } finally {
      setLoading(false);
    }
  }

  async function handleReturningUser() {
    setLoading(true);
    setError("");
    try {
      const result = await auth.returningUser(returningEmail);
      loginStore(result);
      navigate("/welcome", { replace: true });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : typeof e === "string" ? e : "Check your email");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex justify-center" style={{ backgroundColor: COLORS.bgWhite }}>
      <div className="max-w-[440px] w-full min-h-screen relative" style={{ height: "956px" }}>
        {/* Snowkap logo */}
        <img
          src="/assets/snowkap-icon.png"
          alt="Snowkap"
          style={{ position: "absolute", top: "62px", left: "47px", width: "40px", height: "40px" }}
        />

        {/* Identity Setup heading */}
        <h1
          style={{
            position: "absolute",
            top: "173px",
            left: "47px",
            fontSize: "36px",
            color: COLORS.brand,
            letterSpacing: "-0.02em",
          }}
        >
          {step === "domain" ? "Welcome" : step === "returning" ? "Welcome Back" : "Identity Setup"}
        </h1>

        {/* Form content */}
        <div style={{ position: "absolute", top: "240px", left: "47px", right: "47px" }}>
          {error && (
            <p style={{ color: "#ff4044", fontSize: "14px", marginBottom: "16px" }}>{error}</p>
          )}

          {/* Step 1: Domain */}
          {step === "domain" && (
            <div className="space-y-6">
              <UnderlineInput
                label="Company Domain"
                name="domain"
                value={domain}
                onChange={setDomain}
                placeholder="e.g. company.com"
                onKeyDown={(e) => e.key === "Enter" && handleResolveDomain()}
              />
              <button
                onClick={handleResolveDomain}
                disabled={!domain.trim() || loading}
                style={{
                  width: "345px",
                  maxWidth: "100%",
                  height: "54px",
                  backgroundColor: COLORS.darkCard,
                  color: COLORS.bgWhite,
                  borderRadius: RADII.button,
                  fontSize: "20px",
                  fontWeight: 500,
                  border: "none",
                  cursor: loading ? "not-allowed" : "pointer",
                  opacity: loading ? 0.6 : 1,
                  marginTop: "32px",
                  boxShadow: "0px 4px 4px rgba(0,0,0,0.12)",
                }}
              >
                {loading ? "Resolving..." : "Continue"}
              </button>
              <p
                onClick={() => setStep("returning")}
                style={{ fontSize: "14px", color: COLORS.brand, cursor: "pointer", marginTop: "16px" }}
              >
                Already have an account? Sign in &rarr;
              </p>
            </div>
          )}

          {/* Step 2 (designation) removed — login goes domain → confirm. */}

          {/* Step 3: Confirm — Name + Company + Email only. Designation is
              no longer collected at login; users switch cognitive lens in-app
              via the PerspectiveSwitcher. */}
          {step === "confirm" && (
            <div className="space-y-6">
              <UnderlineInput label="Full Name" name="fullname" value={name} onChange={setName} placeholder="e.g. John Smith" />
              <UnderlineInput label="Company Name" name="company" value={companyName} onChange={setCompanyName} placeholder="e.g. Acme Corporation" />

              <UnderlineInput
                label="Email"
                name="email"
                value={email}
                onChange={setEmail}
                placeholder={domain ? `you@${domain}` : "you@company.com"}
                type="email"
                onKeyDown={(e) => e.key === "Enter" && handleLogin()}
              />

              <button
                onClick={handleLogin}
                disabled={!email.trim() || !companyName.trim() || !name.trim() || loading}
                style={{
                  width: "345px",
                  maxWidth: "100%",
                  height: "54px",
                  backgroundColor: COLORS.darkCard,
                  color: COLORS.bgWhite,
                  borderRadius: RADII.button,
                  fontSize: "20px",
                  fontWeight: 500,
                  border: "none",
                  cursor: loading ? "not-allowed" : "pointer",
                  opacity: loading || !email.trim() || !name.trim() ? 0.6 : 1,
                  marginTop: "16px",
                  boxShadow: "0px 4px 4px rgba(0,0,0,0.12)",
                }}
              >
                {loading ? "Signing in..." : "Create Account"}
              </button>

              <button
                onClick={() => setStep("domain")}
                style={{ fontSize: "14px", color: COLORS.textSecondary, background: "none", border: "none", cursor: "pointer", marginTop: "8px" }}
              >
                &larr; Back
              </button>
            </div>
          )}

          {/* Returning user */}
          {step === "returning" && (
            <div className="space-y-6">
              <UnderlineInput
                label="Work Email"
                name="work-email"
                value={returningEmail}
                onChange={setReturningEmail}
                placeholder="you@company.com"
                type="email"
                onKeyDown={(e) => e.key === "Enter" && handleReturningUser()}
              />
              <button
                onClick={handleReturningUser}
                disabled={!returningEmail.trim() || loading}
                style={{
                  width: "345px",
                  maxWidth: "100%",
                  height: "54px",
                  backgroundColor: COLORS.darkCard,
                  color: COLORS.bgWhite,
                  borderRadius: RADII.button,
                  fontSize: "20px",
                  fontWeight: 500,
                  border: "none",
                  cursor: loading ? "not-allowed" : "pointer",
                  opacity: loading ? 0.6 : 1,
                  marginTop: "16px",
                  boxShadow: "0px 4px 4px rgba(0,0,0,0.12)",
                }}
              >
                {loading ? "Signing in..." : "Sign In"}
              </button>
              <button
                onClick={() => setStep("domain")}
                style={{ fontSize: "14px", color: COLORS.textSecondary, background: "none", border: "none", cursor: "pointer", marginTop: "8px" }}
              >
                &larr; New account
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
