/**
 * LoginPage — matches UX/Create account/ "Identity Setup" design.
 * Underline-style inputs, black CTA button, Snowkap logo.
 *
 * Phase 22.3 — two-step magic-link OTP. When the API returns a
 * `{step:"verify"}` challenge (RESEND_API_KEY configured server-side)
 * we transition to step="otp", collect the 6-digit code, and finalise
 * via auth.verify(). When it returns a full LoginResponse (legacy /
 * dev-mode), we keep the original single-step behaviour.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { auth } from "@/lib/api";
import { COLORS, RADII } from "@/lib/designTokens";
import type { LoginResponse } from "@/types";

type Step = "domain" | "confirm" | "returning" | "otp";

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

  // Phase 22.3 — OTP step state. `pendingEmail` is the email we issued
  // the OTP to (sticky across resend); `otpCode` is the 6-digit input;
  // `otpExpiresIn` is a soft countdown; `otpFromSignup` distinguishes
  // signup-verify (replays signup data) from returning-user-verify.
  // `resendCooldown` prevents the user from spamming Resend and
  // tripping the LOGIN_LIMITER (5/min, 20/hr) — would otherwise lock
  // them out of their own account for an hour.
  const [pendingEmail, setPendingEmail] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [otpExpiresIn, setOtpExpiresIn] = useState(0);
  const [otpFromSignup, setOtpFromSignup] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);

  useEffect(() => {
    if (step !== "otp" || otpExpiresIn <= 0) return;
    const t = setInterval(() => setOtpExpiresIn((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [step, otpExpiresIn]);

  useEffect(() => {
    if (resendCooldown <= 0) return;
    const t = setInterval(() => setResendCooldown((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [resendCooldown]);

  function finalise(result: LoginResponse) {
    loginStore(result);
    sessionStorage.setItem("pending_login", "1");
    navigate("/welcome", { replace: true });
  }

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
      if (auth.isVerifyChallenge(result)) {
        setPendingEmail(result.email);
        setOtpExpiresIn(result.expires_in || 600);
        setOtpFromSignup(true);
        setOtpCode("");
        setStep("otp");
        return;
      }
      finalise(result);
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
      if (auth.isVerifyChallenge(result)) {
        setPendingEmail(result.email);
        setOtpExpiresIn(result.expires_in || 600);
        setOtpFromSignup(false);
        setOtpCode("");
        setStep("otp");
        return;
      }
      finalise(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : typeof e === "string" ? e : "Check your email");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyOtp() {
    if (otpCode.replace(/\D/g, "").length !== 6) {
      setError("Enter the 6-digit code from your email.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = otpFromSignup
        ? { email: pendingEmail, code: otpCode.trim(), name, company_name: companyName, domain, designation }
        : { email: pendingEmail, code: otpCode.trim() };
      const result = await auth.verify(payload);
      finalise(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Invalid code");
    } finally {
      setLoading(false);
    }
  }

  async function handleResendOtp() {
    if (resendCooldown > 0) return;
    setLoading(true);
    setError("");
    // Optimistically arm the cooldown so a double-click during the
    // in-flight request doesn't fire two backend calls.
    setResendCooldown(30);
    try {
      const result = otpFromSignup
        ? await auth.login({ email: pendingEmail, domain, designation, company_name: companyName, name })
        : await auth.returningUser(pendingEmail);
      if (auth.isVerifyChallenge(result)) {
        setOtpExpiresIn(result.expires_in || 600);
        setOtpCode("");
      } else {
        // Server flipped to legacy mode mid-flow — just sign them in.
        finalise(result);
      }
    } catch (e: unknown) {
      // On error, release the cooldown so the user can immediately
      // retry (they didn't actually consume a server-side bucket slot).
      setResendCooldown(0);
      setError(e instanceof Error ? e.message : "Could not resend code");
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
          {step === "domain" ? "Welcome"
            : step === "returning" ? "Welcome Back"
            : step === "otp" ? "Check your inbox"
            : "Identity Setup"}
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

          {/* Phase 22.3 — OTP entry. Reached when /auth/login or
              /auth/returning-user returned a {step:"verify"} challenge. */}
          {step === "otp" && (
            <div className="space-y-6">
              <p style={{ fontSize: "14px", color: COLORS.textSecondary, lineHeight: 1.5 }}>
                We sent a 6-digit code to <strong>{pendingEmail}</strong>. Enter it
                below to finish signing in. The code expires in{" "}
                {Math.max(0, Math.floor(otpExpiresIn / 60))}m{" "}
                {String(Math.max(0, otpExpiresIn % 60)).padStart(2, "0")}s.
              </p>
              <UnderlineInput
                label="6-digit code"
                name="otp"
                value={otpCode}
                onChange={(v) => setOtpCode(v.replace(/\D/g, "").slice(0, 6))}
                placeholder="123456"
                onKeyDown={(e) => e.key === "Enter" && handleVerifyOtp()}
              />
              <button
                onClick={handleVerifyOtp}
                disabled={otpCode.length !== 6 || loading}
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
                  cursor: loading || otpCode.length !== 6 ? "not-allowed" : "pointer",
                  opacity: loading || otpCode.length !== 6 ? 0.6 : 1,
                  marginTop: "16px",
                  boxShadow: "0px 4px 4px rgba(0,0,0,0.12)",
                }}
              >
                {loading ? "Verifying..." : "Verify & Sign In"}
              </button>
              <div className="flex justify-between" style={{ marginTop: "8px" }}>
                <button
                  onClick={handleResendOtp}
                  disabled={loading || resendCooldown > 0}
                  style={{
                    fontSize: "13px",
                    color: resendCooldown > 0 ? COLORS.textMuted : COLORS.brand,
                    background: "none",
                    border: "none",
                    cursor: loading || resendCooldown > 0 ? "not-allowed" : "pointer",
                  }}
                >
                  {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : "Resend code"}
                </button>
                <button
                  onClick={() => { setStep(otpFromSignup ? "confirm" : "returning"); setError(""); }}
                  style={{ fontSize: "13px", color: COLORS.textSecondary, background: "none", border: "none", cursor: "pointer" }}
                >
                  &larr; Use a different email
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
