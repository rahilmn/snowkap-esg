/**
 * Lightweight inline markdown renderer for chat/Q&A responses.
 * Handles: ### headings, **bold**, *italic*, `code`, - bullets, numbered lists, --- dividers.
 * No external dependencies — pure React JSX.
 */

import React from "react";
import { COLORS } from "../../lib/designTokens";

/** Renders inline markdown: **bold**, *italic*, `code` within a text string. */
export function InlineMarkdown({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const raw = match[0];
    if (raw.startsWith("**")) {
      parts.push(<strong key={match.index}>{raw.slice(2, -2)}</strong>);
    } else if (raw.startsWith("*")) {
      parts.push(<em key={match.index}>{raw.slice(1, -1)}</em>);
    } else if (raw.startsWith("`")) {
      parts.push(
        <code key={match.index} style={{ fontSize: "11px", backgroundColor: "rgba(0,0,0,0.06)", padding: "1px 4px", borderRadius: "3px", fontFamily: "monospace" }}>
          {raw.slice(1, -1)}
        </code>
      );
    }
    last = match.index + raw.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

/** Block-level markdown renderer — handles headings, lists, dividers, paragraphs. */
export function MarkdownAnswer({ text }: { text: string }) {
  const lines = text.split("\n");
  const elements: React.ReactNode[] = [];
  let listItems: string[] = [];
  let listType: "ul" | "ol" | null = null;

  const flushList = () => {
    if (!listItems.length) return;
    const items = listItems.map((item, i) => (
      <li key={i} style={{ fontSize: "13px", color: COLORS.textPrimary, lineHeight: "1.65", marginBottom: "2px" }}>
        <InlineMarkdown text={item} />
      </li>
    ));
    elements.push(
      listType === "ol"
        ? <ol key={elements.length} style={{ margin: "4px 0 8px", paddingLeft: "20px" }}>{items}</ol>
        : <ul key={elements.length} style={{ margin: "4px 0 8px", paddingLeft: "18px", listStyle: "disc" }}>{items}</ul>
    );
    listItems = [];
    listType = null;
  };

  lines.forEach((line: string, i: number) => {
    if (/^###\s+/.test(line)) {
      flushList();
      elements.push(
        <p key={i} style={{ fontSize: "13px", fontWeight: 700, color: COLORS.textPrimary, margin: "10px 0 3px" }}>
          <InlineMarkdown text={line.replace(/^###\s+/, "")} />
        </p>
      );
    } else if (/^##\s+/.test(line)) {
      flushList();
      elements.push(
        <p key={i} style={{ fontSize: "14px", fontWeight: 700, color: COLORS.textPrimary, margin: "10px 0 3px" }}>
          <InlineMarkdown text={line.replace(/^##\s+/, "")} />
        </p>
      );
    } else if (/^[-*]\s+/.test(line)) {
      if (listType === "ol") flushList();
      listType = "ul";
      listItems.push(line.replace(/^[-*]\s+/, ""));
    } else if (/^\d+\.\s+/.test(line)) {
      if (listType === "ul") flushList();
      listType = "ol";
      listItems.push(line.replace(/^\d+\.\s+/, ""));
    } else if (/^---+$/.test(line.trim())) {
      flushList();
      elements.push(<hr key={i} style={{ border: "none", borderTop: `1px solid ${COLORS.textDisabled}`, margin: "8px 0" }} />);
    } else if (line.trim() === "") {
      flushList();
    } else {
      flushList();
      elements.push(
        <p key={i} style={{ fontSize: "13px", color: COLORS.textPrimary, lineHeight: "1.65", margin: "0 0 4px" }}>
          <InlineMarkdown text={line} />
        </p>
      );
    }
  });
  flushList();

  return <div style={{ margin: 0 }}>{elements}</div>;
}
