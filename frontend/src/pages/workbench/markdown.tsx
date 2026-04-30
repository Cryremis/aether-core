import { useMemo } from "react";
import { marked } from "marked";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import plaintext from "highlight.js/lib/languages/plaintext";
import powershell from "highlight.js/lib/languages/powershell";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import "highlight.js/styles/github-dark-dimmed.css";

import type { AssistantBlock, AssistantSegment } from "./types";

hljs.registerLanguage("bash", bash);
hljs.registerLanguage("sh", bash);
hljs.registerLanguage("shell", bash);
hljs.registerLanguage("css", css);
hljs.registerLanguage("diff", diff);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("js", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("md", markdown);
hljs.registerLanguage("plaintext", plaintext);
hljs.registerLanguage("text", plaintext);
hljs.registerLanguage("txt", plaintext);
hljs.registerLanguage("powershell", powershell);
hljs.registerLanguage("ps1", powershell);
hljs.registerLanguage("python", python);
hljs.registerLanguage("py", python);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("ts", typescript);
hljs.registerLanguage("tsx", typescript);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("yml", yaml);

marked.setOptions({ breaks: true, gfm: true });

type MarkdownPart = { type: "html"; content: string } | { type: "code"; code: string; language: string; lineCount: number; key: string; streaming?: boolean };

function parseMarkdownWithCodeBlocks(text: string): MarkdownPart[] {
  const safeText = text || "";
    const parts: MarkdownPart[] = [];
    const codeBlockRegex = /```(\w*)?\s*\n?([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;
    let codeBlockIndex = 0;

    while ((match = codeBlockRegex.exec(safeText)) !== null) {
        if (match.index > lastIndex) {
            const htmlPart = safeText.slice(lastIndex, match.index);
            parts.push({ type: "html", content: marked.parse(htmlPart) as string });
        }
        const language = (match[1] || "plaintext").trim();
        const code = match[2] || "";
        const lineCount = code.split("\n").length;
        parts.push({ type: "code", code, language, lineCount, key: `code-block-${codeBlockIndex}`, streaming: false });
        codeBlockIndex++;
        lastIndex = match.index + match[0].length;
    }

    if (lastIndex < safeText.length) {
        const remaining = safeText.slice(lastIndex);
        const unclosedMatch = remaining.match(/```(\w*)?\s*\n?([\s\S]*)$/);
        if (unclosedMatch) {
            const language = (unclosedMatch[1] || "plaintext").trim();
            const code = unclosedMatch[2] || "";
            const lineCount = code.split("\n").length;
            parts.push({ type: "code", code, language, lineCount, key: `code-block-${codeBlockIndex}`, streaming: true });
        } else {
            parts.push({ type: "html", content: marked.parse(remaining) as string });
        }
    }

    if (parts.length === 0 && safeText.trim()) {
        parts.push({ type: "html", content: marked.parse(safeText) as string });
    }

    return parts;
}

function CodeBlock({ code, language, lineCount, streaming = false }: { code: string; language: string; lineCount: number; streaming?: boolean }) {
    const validLang = hljs.getLanguage(language) ? language : "plaintext";
    const highlighted = useMemo(() => hljs.highlight(code, { language: validLang }).value, [code, validLang]);
    const encodedCode = encodeURIComponent(code);
    const isOpen = streaming || lineCount <= 15;
    const shouldCollapse = !streaming && lineCount > 15;

    return (
        <details
            className={`code-block-wrapper ${shouldCollapse ? "collapsible" : ""} ${streaming ? "streaming" : ""}`}
            open={isOpen}
        >
            <summary className="code-header">
                <div className="code-header-left">
                    <svg className="code-toggle-icon" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
                    <span className="code-lang">{validLang}</span>
                </div>
                <div className="code-header-right">
                    {shouldCollapse ? <span className="code-expand-label">{lineCount} 行</span> : ""}
                    <button className="copy-button" data-code={encodedCode} type="button">
                        <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                        <span>复制</span>
                    </button>
                </div>
            </summary>
            <div className="code-content-wrapper">
                <div className="code-content-inner">
                    <pre><code className={`hljs language-${validLang}`}>{highlighted}</code></pre>
                </div>
            </div>
        </details>
    );
}

export function MemoizedMarkdown({ content }: { content?: string }) {
    const safeContent = content || "";
    const parts = useMemo(() => parseMarkdownWithCodeBlocks(safeContent), [safeContent]);

    if (parts.length === 0) {
        return null;
    }

    return (
        <div className="markdown-body">
            {parts.map((part, index) =>
                part.type === "code" ? (
                    <CodeBlock key={part.key} code={part.code} language={part.language} lineCount={part.lineCount} streaming={part.streaming} />
                ) : (
                    <div key={`html-${index}`} dangerouslySetInnerHTML={{ __html: part.content }} />
                ),
            )}
        </div>
    );
}

export function renderAssistantSegments(blocks: AssistantBlock[]): AssistantSegment[] {
    const segments: AssistantSegment[] = [];
    let currentBubble: Array<Extract<AssistantBlock, { kind: "reasoning" | "content" | "runtime_notice" }>> = [];

    for (const block of blocks) {
        if (block.kind === "tool") {
            if (currentBubble.length > 0) {
                segments.push({ id: `bubble-${currentBubble[0].id}`, kind: "bubble", blocks: currentBubble });
                currentBubble = [];
            }
            segments.push({ id: `tool-${block.id}`, kind: "tool", block });
            continue;
        }
        if (block.kind === "elapsed") {
            continue;
        }
        currentBubble.push(block as Extract<AssistantBlock, { kind: "reasoning" | "content" | "runtime_notice" }>);
    }

    if (currentBubble.length > 0) {
        segments.push({ id: `bubble-${currentBubble[0].id}`, kind: "bubble", blocks: currentBubble });
    }

    return segments;
}

export function formatTokenCount(value: number) {
    if (!Number.isFinite(value) || value <= 0) return "0";
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2).replace(/\.00$/, "")}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
    return String(Math.round(value));
}

export function formatElapsedMs(ms: number | null) {
    if (ms === null) return null;
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.round((ms % 60000) / 1000);
    return `${minutes}m ${seconds}s`;
}
