import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { auth } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";

type Step = "domain" | "designation" | "confirm" | "sent" | "returning";

const DESIGNATIONS = [
  "CEO",
  "CFO",
  "CTO",
  "COO",
  "Head of Sustainability",
  "Sustainability Manager",
  "ESG Manager",
  "ESG Analyst",
  "Data Analyst",
  "Consultant",
];

export function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const login = useAuthStore((s) => s.login);

  const [step, setStep] = useState<Step>("domain");
  const [domain, setDomain] = useState("");
  const [designation, setDesignation] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [industry, setIndustry] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [returningEmail, setReturningEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Handle magic link verification from URL
  const verifyToken = searchParams.get("token");
  if (verifyToken) {
    return <VerifyToken token={verifyToken} onSuccess={login} navigate={navigate} />;
  }

  async function handleResolveDomain() {
    setLoading(true);
    setError("");
    try {
      const result = await auth.resolveDomain(domain);
      if (result.company_name) setCompanyName(result.company_name);
      if (result.industry) setIndustry(result.industry);
      setStep("designation");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to resolve domain");
    } finally {
      setLoading(false);
    }
  }

  async function handleSendMagicLink() {
    setLoading(true);
    setError("");
    try {
      await auth.sendMagicLink({
        email,
        domain,
        designation,
        company_name: companyName,
        name,
      });
      setStep("sent");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to send magic link");
    } finally {
      setLoading(false);
    }
  }

  async function handleReturningUser() {
    setLoading(true);
    setError("");
    try {
      await auth.returningUser(returningEmail);
      setStep("sent");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to send magic link");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-emerald-50 via-white to-blue-50 p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-foreground">SNOWKAP</h1>
          <p className="text-muted-foreground mt-1">ESG Intelligence Platform</p>
        </div>

        {/* Step indicator */}
        {step !== "sent" && step !== "returning" && (
          <div className="flex items-center justify-center gap-2 mb-6">
            {["domain", "designation", "confirm"].map((s, i) => (
              <div key={s} className="flex items-center gap-2">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                    step === s
                      ? "bg-primary text-primary-foreground"
                      : ["domain", "designation", "confirm"].indexOf(step) > i
                        ? "bg-primary/20 text-primary"
                        : "bg-muted text-muted-foreground"
                  }`}
                >
                  {i + 1}
                </div>
                {i < 2 && <div className="w-8 h-px bg-border" />}
              </div>
            ))}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>
              {step === "domain" && "Enter your company domain"}
              {step === "designation" && "Select your designation"}
              {step === "confirm" && "Confirm & sign in"}
              {step === "sent" && "Check your email"}
              {step === "returning" && "Welcome back"}
            </CardTitle>
            <CardDescription>
              {step === "domain" && "No passwords needed. We'll verify via your work email."}
              {step === "designation" && "This determines your dashboard view and permissions."}
              {step === "confirm" && "We'll send a magic link to your work email."}
              {step === "sent" && "Click the link in your email to sign in."}
              {step === "returning" && "Enter your email to receive a login link."}
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            {error && (
              <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}

            {/* Step 1: Domain */}
            {step === "domain" && (
              <>
                <div>
                  <label className="text-sm font-medium mb-1.5 block">Company Domain</label>
                  <Input
                    placeholder="e.g. mahindra.com"
                    value={domain}
                    onChange={(e) => setDomain(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleResolveDomain()}
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    Your corporate email domain (not gmail, yahoo, etc.)
                  </p>
                </div>
                <Button className="w-full" onClick={handleResolveDomain} disabled={!domain.trim() || loading}>
                  {loading ? <Spinner className="mr-2 h-4 w-4" /> : null}
                  Continue
                </Button>
                <div className="relative my-4">
                  <div className="absolute inset-0 flex items-center"><span className="w-full border-t" /></div>
                  <div className="relative flex justify-center text-xs uppercase">
                    <span className="bg-card px-2 text-muted-foreground">or</span>
                  </div>
                </div>
                <Button variant="outline" className="w-full" onClick={() => setStep("returning")}>
                  I already have an account
                </Button>
              </>
            )}

            {/* Step 2: Designation */}
            {step === "designation" && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  {DESIGNATIONS.map((d) => (
                    <button
                      key={d}
                      className={`rounded-md border p-3 text-sm text-left transition-colors hover:border-primary ${
                        designation === d ? "border-primary bg-primary/5 font-medium" : "border-border"
                      }`}
                      onClick={() => setDesignation(d)}
                    >
                      {d}
                    </button>
                  ))}
                </div>
                <div>
                  <label className="text-sm font-medium mb-1.5 block">Or enter custom</label>
                  <Input
                    placeholder="Your designation"
                    value={designation}
                    onChange={(e) => setDesignation(e.target.value)}
                  />
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => setStep("domain")}>Back</Button>
                  <Button className="flex-1" onClick={() => setStep("confirm")} disabled={!designation.trim()}>
                    Continue
                  </Button>
                </div>
              </>
            )}

            {/* Step 3: Confirm */}
            {step === "confirm" && (
              <>
                <div className="rounded-md bg-muted p-4 space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Domain</span>
                    <span className="font-medium">{domain}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Designation</span>
                    <span className="font-medium">{designation}</span>
                  </div>
                  {industry && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Industry</span>
                      <span className="font-medium">{industry}</span>
                    </div>
                  )}
                </div>
                <div>
                  <label className="text-sm font-medium mb-1.5 block">Your Name</label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Rahil Sharma"
                  />
                </div>
                <div>
                  <label className="text-sm font-medium mb-1.5 block">Company Name</label>
                  <Input
                    value={companyName}
                    onChange={(e) => setCompanyName(e.target.value)}
                    placeholder="e.g. Mahindra Logistics Ltd"
                  />
                </div>
                <div>
                  <label className="text-sm font-medium mb-1.5 block">Work Email</label>
                  <Input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder={`you@${domain}`}
                    onKeyDown={(e) => e.key === "Enter" && handleSendMagicLink()}
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    Must be a @{domain} email address
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => setStep("designation")}>Back</Button>
                  <Button
                    className="flex-1"
                    onClick={handleSendMagicLink}
                    disabled={!email.trim() || !companyName.trim() || !name.trim() || loading}
                  >
                    {loading ? <Spinner className="mr-2 h-4 w-4" /> : null}
                    Send Magic Link
                  </Button>
                </div>
              </>
            )}

            {/* Magic Link Sent */}
            {step === "sent" && (
              <div className="text-center py-4">
                <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
                <p className="text-sm text-muted-foreground">
                  We sent a login link to your email. Click it to sign in.
                </p>
                <Button variant="outline" className="mt-4" onClick={() => setStep("domain")}>
                  Start Over
                </Button>
              </div>
            )}

            {/* Returning User */}
            {step === "returning" && (
              <>
                <div>
                  <label className="text-sm font-medium mb-1.5 block">Work Email</label>
                  <Input
                    type="email"
                    value={returningEmail}
                    onChange={(e) => setReturningEmail(e.target.value)}
                    placeholder="you@company.com"
                    onKeyDown={(e) => e.key === "Enter" && handleReturningUser()}
                  />
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => setStep("domain")}>Back</Button>
                  <Button
                    className="flex-1"
                    onClick={handleReturningUser}
                    disabled={!returningEmail.trim() || loading}
                  >
                    {loading ? <Spinner className="mr-2 h-4 w-4" /> : null}
                    Send Login Link
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// Sub-component for magic link verification
function VerifyToken({
  token,
  onSuccess,
  navigate,
}: {
  token: string;
  onSuccess: (data: {
    token: string;
    user_id: string;
    tenant_id: string;
    company_id: string | null;
    designation: string;
    permissions: string[];
    domain: string;
    name: string | null;
  }) => void;
  navigate: ReturnType<typeof useNavigate>;
}) {
  const [error, setError] = useState("");
  const [verifying, setVerifying] = useState(true);

  useState(() => {
    auth
      .verify(token)
      .then((result) => {
        onSuccess(result);
        navigate("/", { replace: true });
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Verification failed");
        setVerifying(false);
      });
  });

  return (
    <div className="min-h-screen flex items-center justify-center">
      {verifying ? (
        <div className="text-center">
          <Spinner className="h-8 w-8 mx-auto mb-4" />
          <p className="text-muted-foreground">Verifying your login...</p>
        </div>
      ) : (
        <Card className="max-w-sm">
          <CardContent className="pt-6 text-center">
            <p className="text-destructive mb-4">{error}</p>
            <Button onClick={() => navigate("/login", { replace: true })}>
              Back to Login
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
