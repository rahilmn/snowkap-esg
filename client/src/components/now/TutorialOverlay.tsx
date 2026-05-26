/**
 * Phase 34.3 — First-launch swipe tutorial.
 *
 * Renders a translucent overlay with 4 directional cues so a first-
 * time user discovers the swipe model. Dismissed on tap or first
 * swipe (whichever happens first). The dismissed state persists in
 * localStorage under `pon-tut-1` so the overlay never re-shows after
 * a single dismissal.
 */
import { TOKENS } from "@/lib/designTokensV2";

interface Props {
  onDismiss: () => void;
}

export function TutorialOverlay({ onDismiss }: Props) {
  return (
    <div
      onClick={onDismiss}
      style={{
        position: "absolute", inset: 0,
        background: "rgba(15, 17, 21, 0.78)",
        backdropFilter: "blur(2px)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 20,
        zIndex: 25,
        cursor: "pointer",
        padding: 36,
        textAlign: "center",
      }}
    >
      <p className="serif" style={{
        margin: 0, color: "white",
        fontSize: 24, fontWeight: 600,
        letterSpacing: "-0.01em", lineHeight: 1.25,
      }}>
        Your Now,<br/>at your fingertips.
      </p>
      <p style={{
        margin: 0, color: "rgba(255,255,255,0.7)",
        fontSize: 13, lineHeight: 1.55,
        maxWidth: 280,
      }}>
        Swipe to navigate the news that matters to you.
      </p>

      <div style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr",
        gap: "10px 16px",
        marginTop: 12,
        color: "white",
        fontSize: 13,
        alignItems: "center",
      }}>
        <Cue arrow="←" label="Next story"/>
        <Cue arrow="→" label="Previous story"/>
        <Cue arrow="↑" label="Read full article" accent/>
        <Cue arrow="↓" label="Save to your Wiki" positive/>
      </div>

      <p style={{
        margin: "12px 0 0", color: "rgba(255,255,255,0.5)",
        fontSize: 11, letterSpacing: "0.04em",
        textTransform: "uppercase",
      }}>
        Tap anywhere to dismiss
      </p>
    </div>
  );
}

function Cue({ arrow, label, accent, positive }: { arrow: string; label: string; accent?: boolean; positive?: boolean }) {
  const color = accent ? TOKENS.brand : positive ? TOKENS.positive : "white";
  return (
    <>
      <span style={{
        fontSize: 22, lineHeight: 1, fontWeight: 600,
        color,
        width: 36, height: 36,
        borderRadius: 999,
        border: `1px solid ${color}55`,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
      }}>{arrow}</span>
      <span style={{ textAlign: "left" }}>{label}</span>
    </>
  );
}
