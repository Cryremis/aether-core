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

marked.use({
  renderer: {
    code(codeArg: unknown, langArg?: string) {
      const text = typeof codeArg === "object" && codeArg && "text" in codeArg ? String((codeArg as { text: unknown }).text ?? "") : String(codeArg ?? "");
      const language = typeof codeArg === "object" && codeArg && "lang" in codeArg ? String((codeArg as { lang: unknown }).lang ?? "") : (langArg ?? "plaintext");
      const validLang = hljs.getLanguage(language) ? language : "plaintext";
      const highlighted = hljs.highlight(text, { language: validLang }).value;
      const encodedCode = encodeURIComponent(text);
      const lineCount = text.split("\n").length;
      const shouldCollapse = lineCount > 15;

      return `
        <details class="code-block-wrapper ${shouldCollapse ? "collapsible" : ""}" ${shouldCollapse ? "" : "open"}>
          <summary class="code-header">
            <div class="code-header-left">
              <svg class="code-toggle-icon" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
              <span class="code-lang">${validLang}</span>
            </div>
            <div class="code-header-right">
              ${shouldCollapse ? `<span class="code-expand-label">${lineCount} 行</span>` : ""}
              <button class="copy-button" data-code="${encodedCode}" type="button">
                <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                <span>复制</span>
              </button>
            </div>
          </summary>
          <div class="code-content-wrapper">
            <div class="code-content-inner">
              <pre><code class="hljs language-${validLang}">${highlighted}</code></pre>
            </div>
          </div>
        </details>
      `;
    },
  },
});

export function renderMarkdown(value?: string | null) {
  const safeValue = typeof value === "string" ? value : "";
  return { __html: marked.parse(safeValue) as string };
}

export function renderAssistantSegments(blocks: AssistantBlock[]): AssistantSegment[] {
  const segments: AssistantSegment[] = [];
  let currentBubble: Array<Extract<AssistantBlock, { kind: "reasoning" | "content" }>> = [];

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
    currentBubble.push(block as Extract<AssistantBlock, { kind: "reasoning" | "content" }>);
  }

  if (currentBubble.length > 0) {
    segments.push({ id: `bubble-${currentBubble[0].id}`, kind: "bubble", blocks: currentBubble });
  }

  return segments;
}

export function formatTokenCount(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2).replace(/\\.00$/, "")}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\\.0$/, "")}k`;
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
