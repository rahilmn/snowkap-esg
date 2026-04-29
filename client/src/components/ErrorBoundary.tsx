/**
 * Top-level React Error Boundary (Phase 16 — debug enable for live demos).
 *
 * Pre-Phase-16 the app had no boundary — any runtime error in a child
 * component (e.g. ArticleDetailSheet) cascaded to a white screen with no
 * actionable signal. Console errors were the only way to see what crashed.
 *
 * This boundary catches the error, surfaces the message + stack to the
 * user, and provides a "Reload" affordance. In production, swap the
 * verbose stack panel for a friendly message + Sentry beacon.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo });
    // Log to console.error so console-level alerting (Sentry breadcrumbs,
    // browser DevTools) sees it. The Phase 13 B5 ESLint rule allows
    // console.error.
    console.error("[ErrorBoundary] Caught render error:", error);
    console.error("[ErrorBoundary] Component stack:", errorInfo.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          padding: "32px 24px",
          maxWidth: 720,
          margin: "0 auto",
          fontFamily: "system-ui, -apple-system, sans-serif",
        }}>
          <h1 style={{
            fontSize: 22, fontWeight: 700, color: "#DC2626", margin: "0 0 12px",
          }}>
            Something broke while rendering this view.
          </h1>
          <p style={{ color: "#475569", fontSize: 14, lineHeight: 1.6 }}>
            The error has been logged. Tell the dev team what you clicked
            just before this happened, then refresh.
          </p>

          <div style={{
            marginTop: 20, padding: 16, borderRadius: 8,
            background: "#FEF2F2", border: "1px solid #FECACA",
            fontFamily: "monospace", fontSize: 12,
            color: "#7F1D1D", overflow: "auto",
          }}>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>
              {this.state.error.name}: {this.state.error.message}
            </div>
            {this.state.error.stack && (
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 11, color: "#9F1239" }}>
                {this.state.error.stack.split("\n").slice(0, 10).join("\n")}
              </pre>
            )}
          </div>

          {this.state.errorInfo?.componentStack && (
            <details style={{ marginTop: 16 }}>
              <summary style={{ cursor: "pointer", fontSize: 12, color: "#64748B" }}>
                Component stack
              </summary>
              <pre style={{
                marginTop: 8, padding: 12, borderRadius: 6,
                background: "#F1F5F9", fontSize: 11, color: "#475569",
                whiteSpace: "pre-wrap", overflow: "auto", maxHeight: 240,
              }}>
                {this.state.errorInfo.componentStack}
              </pre>
            </details>
          )}

          <div style={{ marginTop: 24, display: "flex", gap: 12 }}>
            <button
              onClick={() => {
                this.setState({ error: null, errorInfo: null });
              }}
              style={{
                padding: "8px 18px", borderRadius: 8,
                background: "#DF5900", color: "#fff", border: "none",
                fontSize: 13, fontWeight: 600, cursor: "pointer",
              }}
            >
              Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{
                padding: "8px 18px", borderRadius: 8,
                background: "#fff", color: "#0F172A",
                border: "1px solid #CBD5E1",
                fontSize: 13, fontWeight: 600, cursor: "pointer",
              }}
            >
              Hard reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
