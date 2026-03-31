/**
 * Splash screen — Video background + "The Power of" text + NOW LOGO SVG.
 * Auto-advances to /intro after 4 seconds or on tap.
 */

import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

export default function SplashPage() {
  const navigate = useNavigate();

  useEffect(() => {
    const timer = setTimeout(() => navigate("/intro"), 4000);
    return () => clearTimeout(timer);
  }, [navigate]);

  return (
    <div
      className="fixed inset-0 cursor-pointer overflow-hidden"
      onClick={() => navigate("/intro")}
      style={{ backgroundColor: "#000" }}
    >
      <div className="max-w-[440px] mx-auto h-full relative">
        {/* Video background */}
        <video
          autoPlay
          muted
          loop
          playsInline
          className="absolute inset-0 w-full h-full object-cover"
          style={{ opacity: 0.6 }}
        >
          <source src="/assets/splash-video.mp4" type="video/mp4" />
        </video>

        {/* "The Power of" text + NOW LOGO */}
        <div className="absolute" style={{ top: "340px", left: "47px", right: "47px" }}>
          <h1
            style={{
              fontSize: "40px",
              fontWeight: 700,
              color: "#ffffff",
              letterSpacing: "-0.02em",
              lineHeight: "1.2",
            }}
          >
            The Power of
          </h1>
          <img
            src="/assets/now-logo.svg"
            alt="Now"
            style={{ width: "280px", marginTop: "12px" }}
          />
        </div>

        {/* Snowkap branding — bottom center */}
        <div className="absolute" style={{ bottom: "30px", left: 0, right: 0, textAlign: "center" }}>
          <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.6)", letterSpacing: "0.1em" }}>
            SNOWKAP
          </p>
          <p style={{ fontSize: "12px", color: "rgba(255,255,255,0.4)", marginTop: "4px" }}>
            ESG Intelligence Platform
          </p>
        </div>
      </div>
    </div>
  );
}
