/**
 * StructuredMessageRenderer — replaces raw ReactMarkdown in agent chat.
 * Parses agent response text and renders structured blocks as styled components.
 */

import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import { COLORS, RADII } from "../../lib/designTokens";

interface StructuredMessageRendererProps {
  content: string;
}

/* ── Segment types ──────────────────────────────────────── */

type Segment =
  | { kind: "severity"; lines: string[] }
  | { kind: "radar"; lines: string[] }
  | { kind: "table"; lines: string[] }
  | { kind: "actions"; lines: string[] }
  | { kind: "markdown"; text: string };

/* ── Detection helpers ──────────────────────────────────── */

const SEVERITY_BANNER_RE = /━━━\s*SEVERITY\s*━━━/;
const SEVERITY_EMOJI_RE = /^(🔴|🟡|🟢)\s*\*\*(HIGH|MODERATE|LOW)\*\*/;
const PRIORITY_META_RE = /^`Priority:\s*\d+\/\d+\s*·\s*Impact:\s*[\d.]+\/\d+`$/;

const RADAR_BANNER_RE = /━━━\s*ON YOUR RADAR/;
const VERDICT_RE = /^\*\*(ACT NOW|WATCH|IGNORE)\*\*/;

const TABLE_SEPARATOR_RE = /^\|[\s-:|]+\|$/;

const ACTION_ITEM_RE = /^→\s+/;

function isSeverityStart(line: string): boolean {
  return SEVERITY_BANNER_RE.test(line) || SEVERITY_EMOJI_RE.test(line);
}

function isRadarStart(line: string): boolean {
  return RADAR_BANNER_RE.test(line) || VERDICT_RE.test(line);
}

function isTableLine(line: string): boolean {
  return line.trimStart().startsWith("|") && line.trimEnd().endsWith("|");
}

/* ── Parser ─────────────────────────────────────────────── */

function parseSegments(content: string): Segment[] {
  const rawLines = content.split("\n");
  const segments: Segment[] = [];
  let mdBuffer: string[] = [];

  const flushMd = () => {
    if (mdBuffer.length > 0) {
      const text = mdBuffer.join("\n").trim();
      if (text) segments.push({ kind: "markdown", text });
      mdBuffer = [];
    }
  };

  let i = 0;
  while (i < rawLines.length) {
    const line = rawLines[i]!;
    const trimmed = line.trim();

    // ── Severity block ──
    if (isSeverityStart(trimmed)) {
      flushMd();
      const block: string[] = [trimmed];
      i++;
      // Collect the next few lines that belong to the severity context
      while (i < rawLines.length) {
        const next = rawLines[i]!.trim();
        if (next === "") {
          // blank line ends the block unless next line is priority meta
          const peekLine = rawLines[i + 1];
          if (peekLine !== undefined && PRIORITY_META_RE.test(peekLine.trim())) {
            i++;
            continue;
          }
          break;
        }
        if (SEVERITY_EMOJI_RE.test(next) || PRIORITY_META_RE.test(next)) {
          block.push(next);
          i++;
          continue;
        }
        // Additional severity context line (short description)
        if (block.length < 5 && !isRadarStart(next) && !isTableLine(next) && !ACTION_ITEM_RE.test(next)) {
          block.push(next);
          i++;
          continue;
        }
        break;
      }
      segments.push({ kind: "severity", lines: block });
      continue;
    }

    // ── Radar / verdict block ──
    if (isRadarStart(trimmed)) {
      flushMd();
      const block: string[] = [trimmed];
      i++;
      while (i < rawLines.length) {
        const next = rawLines[i]!.trim();
        if (next === "") break;
        if (isSeverityStart(next) || isTableLine(next) || ACTION_ITEM_RE.test(next)) break;
        block.push(next);
        i++;
      }
      segments.push({ kind: "radar", lines: block });
      continue;
    }

    // ── Markdown table ──
    if (isTableLine(trimmed)) {
      flushMd();
      const block: string[] = [];
      while (i < rawLines.length && isTableLine(rawLines[i]!.trim())) {
        block.push(rawLines[i]!.trim());
        i++;
      }
      segments.push({ kind: "table", lines: block });
      continue;
    }

    // ── Action items (consecutive → lines) ──
    if (ACTION_ITEM_RE.test(trimmed)) {
      flushMd();
      const block: string[] = [];
      while (i < rawLines.length && ACTION_ITEM_RE.test(rawLines[i]!.trim())) {
        block.push(rawLines[i]!.trim());
        i++;
      }
      segments.push({ kind: "actions", lines: block });
      continue;
    }

    // ── Default: markdown ──
    mdBuffer.push(line);
    i++;
  }

  flushMd();
  return segments;
}

/* ── Render helpers ─────────────────────────────────────── */

function severityColor(lines: string[]): { bg: string; text: string } {
  const joined = lines.join(" ");
  if (/🔴|HIGH/.test(joined)) return { bg: "rgba(255, 64, 68, 0.12)", text: "#ff4044" };
  if (/🟡|MODERATE/.test(joined)) return { bg: "rgba(245, 166, 35, 0.12)", text: "#d4920a" };
  if (/🟢|LOW/.test(joined)) return { bg: "rgba(24, 168, 125, 0.12)", text: "#18a87d" };
  return { bg: COLORS.bgLight, text: COLORS.textPrimary };
}

function SeverityBlock({ lines }: { lines: string[] }) {
  const { bg, text } = severityColor(lines);

  return (
    <div
      style={{
        background: bg,
        borderRadius: RADII.card,
        padding: "12px 16px",
        marginBottom: 8,
      }}
    >
      {lines.map((line, idx) => {
        if (PRIORITY_META_RE.test(line)) {
          // Strip backticks and render as monospace metadata
          const clean = line.replace(/^`|`$/g, "");
          return (
            <div
              key={idx}
              style={{
                fontFamily: "monospace",
                fontSize: 12,
                color: text,
                opacity: 0.85,
                marginTop: 4,
              }}
            >
              {clean}
            </div>
          );
        }
        // Main severity line — strip markdown bold markers for display
        const display = line.replace(/\*\*/g, "");
        return (
          <div
            key={idx}
            style={{
              fontSize: idx === 0 ? 15 : 13,
              fontWeight: idx === 0 ? 700 : 500,
              color: text,
              lineHeight: 1.5,
            }}
          >
            {display}
          </div>
        );
      })}
    </div>
  );
}

function RadarBlock({ lines }: { lines: string[] }) {
  // Determine action type from first line
  const firstLine = lines[0] || "";
  let accentColor: string = COLORS.brand;
  if (/ACT NOW/.test(firstLine)) accentColor = "#ff4044";
  else if (/WATCH/.test(firstLine)) accentColor = "#d4920a";
  else if (/IGNORE/.test(firstLine)) accentColor = COLORS.textMuted;

  return (
    <div
      style={{
        background: COLORS.bgWhite,
        border: `1px solid ${COLORS.cardBorder}`,
        borderLeft: `4px solid ${accentColor}`,
        borderRadius: RADII.card,
        padding: "12px 16px",
        marginBottom: 8,
      }}
    >
      {lines.map((line, idx) => {
        const display = line.replace(/\*\*/g, "").replace(/━+\s*ON YOUR RADAR\s*━*/, "ON YOUR RADAR");
        return (
          <div
            key={idx}
            style={{
              fontSize: idx === 0 ? 14 : 13,
              fontWeight: idx === 0 ? 700 : 400,
              color: idx === 0 ? accentColor : COLORS.textSecondary,
              lineHeight: 1.55,
              marginTop: idx > 0 ? 2 : 0,
            }}
          >
            {display}
          </div>
        );
      })}
    </div>
  );
}

function StyledTable({ lines }: { lines: string[] }) {
  // Parse header, separator, body rows
  if (lines.length < 2) return null;

  const parseRow = (row: string): string[] =>
    row
      .split("|")
      .slice(1, -1) // drop leading/trailing empty from split
      .map((cell) => cell.trim());

  const headerCells = parseRow(lines[0]!);
  // Find separator index (line with ---)
  let sepIdx = lines.findIndex((l) => TABLE_SEPARATOR_RE.test(l));
  if (sepIdx === -1) sepIdx = 1;
  const bodyRows = lines.slice(sepIdx + 1).map(parseRow);

  const cellStyle = (value: string): React.CSSProperties => {
    const upper = value.toUpperCase();
    if (upper.includes("CRITICAL")) return { color: "#ff4044", fontWeight: 600 };
    if (upper.includes("HIGH")) return { color: COLORS.brand, fontWeight: 600 };
    if (upper.includes("LOW")) return { color: COLORS.textMuted };
    return {};
  };

  return (
    <div
      style={{
        overflowX: "auto",
        marginBottom: 8,
        borderRadius: RADII.card,
        border: `1px solid ${COLORS.cardBorder}`,
      }}
    >
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: 13,
        }}
      >
        <thead>
          <tr>
            {headerCells.map((cell, ci) => (
              <th
                key={ci}
                style={{
                  background: COLORS.textPrimary,
                  color: COLORS.bgWhite,
                  fontWeight: 600,
                  fontSize: 12,
                  padding: "8px 12px",
                  textAlign: "left",
                  whiteSpace: "nowrap",
                  borderBottom: `1px solid ${COLORS.cardBorder}`,
                }}
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bodyRows.map((row, ri) => (
            <tr
              key={ri}
              style={{
                background: ri % 2 === 0 ? COLORS.bgWhite : COLORS.bgLight,
              }}
            >
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  style={{
                    padding: "7px 12px",
                    borderBottom: `1px solid ${COLORS.cardBorder}`,
                    color: COLORS.textPrimary,
                    lineHeight: 1.45,
                    ...cellStyle(cell),
                  }}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ActionItems({ lines }: { lines: string[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
      {lines.map((line, idx) => {
        const text = line.replace(ACTION_ITEM_RE, "");
        return (
          <div
            key={idx}
            style={{
              background: COLORS.bgWhite,
              borderLeft: `3px solid ${COLORS.brand}`,
              borderRadius: `0 ${RADII.card} ${RADII.card} 0`,
              padding: "8px 14px",
              fontSize: 13,
              color: COLORS.textPrimary,
              lineHeight: 1.5,
              border: `1px solid ${COLORS.cardBorder}`,
              borderLeftColor: COLORS.brand,
              borderLeftWidth: 3,
            }}
          >
            {text}
          </div>
        );
      })}
    </div>
  );
}

function MarkdownBlock({ text }: { text: string }) {
  return (
    <div className="prose prose-sm max-w-none dark:prose-invert [&>p]:mb-2 [&>p:last-child]:mb-0 [&>ul]:mb-2 [&>ol]:mb-2">
      <ReactMarkdown skipHtml={true}>{text}</ReactMarkdown>
    </div>
  );
}

/* ── Main component ─────────────────────────────────────── */

export function StructuredMessageRenderer({ content }: StructuredMessageRendererProps) {
  const segments = useMemo(() => parseSegments(content), [content]);

  return (
    <div>
      {segments.map((seg, idx) => {
        switch (seg.kind) {
          case "severity":
            return <SeverityBlock key={idx} lines={seg.lines} />;
          case "radar":
            return <RadarBlock key={idx} lines={seg.lines} />;
          case "table":
            return <StyledTable key={idx} lines={seg.lines} />;
          case "actions":
            return <ActionItems key={idx} lines={seg.lines} />;
          case "markdown":
            return <MarkdownBlock key={idx} text={seg.text} />;
        }
      })}
    </div>
  );
}
