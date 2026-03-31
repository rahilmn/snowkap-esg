/**
 * Intro Page — matches UX/Intro/Setup.html exactly.
 * Shows between Splash and Login:
 * "Welcome to Snowkap ESG Intelligence."
 * "Where data meets decisive action."
 * "Let's get started →"
 */

import { useNavigate } from "react-router-dom";
import { COLORS } from "../lib/designTokens";

export default function IntroPage() {
  const navigate = useNavigate();

  return (
    <div
      className="min-h-screen flex justify-center"
      style={{ backgroundColor: COLORS.bgWhite }}
    >
      <div
        className="max-w-[440px] w-full relative"
        style={{ height: "956px" }}
      >
        {/* Snowkap logo — top left */}
        <img
          src="/assets/snowkap-icon.png"
          alt="Snowkap"
          style={{ position: "absolute", top: "62px", left: "47px", width: "40px", height: "40px" }}
        />

        {/* Bot icon — top right */}
        <img
          src="/assets/chatbot-icon.png"
          alt=""
          style={{ position: "absolute", top: "61px", right: "47px", width: "32px", height: "32px" }}
        />

        {/* Main content */}
        <div style={{ paddingTop: "200px", paddingLeft: "47px", paddingRight: "47px" }}>
          {/* Heading */}
          <h1
            style={{
              fontSize: "36px",
              color: COLORS.textPrimary,
              letterSpacing: "-0.02em",
              lineHeight: "1.2",
              fontWeight: 400,
            }}
          >
            Welcome to
            <br />
            Snowkap ESG Intelligence.
          </h1>

          {/* Orange subtitle */}
          <h2
            style={{
              fontSize: "36px",
              color: COLORS.brand,
              letterSpacing: "-0.02em",
              lineHeight: "1.2",
              fontWeight: 400,
              marginTop: "8px",
            }}
          >
            Where data meets decisive action.
          </h2>

          {/* CTA */}
          <button
            onClick={() => navigate("/login")}
            style={{
              marginTop: "48px",
              fontSize: "24px",
              color: COLORS.textPrimary,
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: 0,
              fontWeight: 400,
            }}
          >
            Let&apos;s get started &rarr;
          </button>

          {/* Decorative line */}
          <div
            style={{
              marginTop: "8px",
              width: "160px",
              height: "2px",
              backgroundColor: COLORS.textPrimary,
            }}
          />
        </div>
      </div>
    </div>
  );
}
