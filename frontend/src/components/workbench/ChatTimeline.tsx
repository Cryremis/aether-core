import { useEffect, useState, type RefObject } from "react";
import { MemoizedMarkdown, renderAssistantSegments, formatElapsedMs } from "../../pages/workbench/markdown";
import type { ChatMessage } from "../../pages/workbench/types";
import { WorkbenchIcons as Icons } from "./WorkbenchIcons";

type ChatTimelineProps = {
  contentRef?: RefObject<HTMLDivElement | null>;
  loading: boolean;
  messages: ChatMessage[];
};

function LiveElapsedBadge({ startTime }: { startTime: number }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setElapsed(Date.now() - startTime);
    }, 100);
    return () => window.clearInterval(timer);
  }, [startTime]);

  return <div className="elapsed-badge">{formatElapsedMs(elapsed)}</div>;
}

export function ChatTimeline({ contentRef, loading, messages }: ChatTimelineProps) {
  return (
    <div ref={contentRef} className="chat-container">
      {loading ? (
        <div className="welcome-screen anim-enter">
          <div className="welcome-icon"><Icons.Sparkles /></div>
          <h2>AetherCore Workbench</h2>
          <p>正在加载会话内容...</p>
        </div>
      ) : null}

      {!loading && messages.length === 0 ? (
        <div className="welcome-screen anim-enter">
          <div className="welcome-icon"><Icons.Sparkles /></div>
          <h2>AetherCore Workbench</h2>
          <p>输入任务指令，或在左侧上传文件与技能定义。</p>
        </div>
      ) : null}

      {messages.map((message) =>
        message.role === "user" ? (
          <div key={message.id} className="message-row user msg-anim">
            <div className="bubble user-bubble">
              <MemoizedMarkdown content={message.content} />
            </div>
          </div>
        ) : message.role === "elicitation_response" ? (
          <div key={message.id} className="message-row message-row--elicitation-response msg-anim">
            <div className="elicitation-response-bubble">
              <div className="elicitation-response-bubble__eyebrow">
                <span className="elicitation-response-bubble__dot" />
                <span>问题已回复</span>
              </div>
              <div className="elicitation-response-bubble__title">{message.title}</div>
              <div className="elicitation-response-bubble__summary">{message.summary}</div>
              <div className="elicitation-response-bubble__answers">
                {message.answers.map((answer) => (
                  <div key={answer.id} className="elicitation-response-bubble__answer">
                    <span>{answer.header}</span>
                    <strong>{answer.value}</strong>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div key={message.id} className="message-row assistant msg-anim">
            <div className="assistant-content">
              {renderAssistantSegments(message.blocks).map((segment) =>
                segment.kind === "tool" ? (
                  <details key={segment.id} className={`tool-card ${segment.block.status}`}>
                    <summary className="tool-header">
                      <div className="tool-title">
                        <svg className="tool-arrow" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
                        {segment.block.title}
                      </div>
                      <div className="tool-status">
                        {segment.block.status === "running" ? <span className="status-run"><Icons.Loader /> 执行中...</span> : <span className="status-done"><Icons.Check /> 完成</span>}
                      </div>
                    </summary>
                    <div className="tool-body-wrapper">
                      <div className="tool-body-inner">
                        <div className="tool-body">
                          {segment.block.argumentsText ? (
                            <div className="tool-section">
                              <div className="section-label">输入参数</div>
                              <pre className="code-block input">{segment.block.argumentsText}</pre>
                            </div>
                          ) : null}
                          {segment.block.outputText ? (
                            <div className="tool-section">
                              <div className="section-label">输出结果</div>
                              <pre className="code-block output">{segment.block.outputText}</pre>
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  </details>
                ) : (
                  <div key={segment.id} className="text-bubble">
                    {segment.blocks.map((block) =>
                      block.kind === "reasoning" ? (
                        <details key={block.id} className="reasoning-block" open>
                          <summary><Icons.Sparkles /> 思考过程</summary>
                          <div className="reasoning-content">
                            <MemoizedMarkdown content={block.content} />
                          </div>
                        </details>
                      ) : (
                        <MemoizedMarkdown key={block.id} content={block.content} />
                      ),
                    )}
                  </div>
                ),
              )}
            </div>
            {message.streaming && message.startTime ? (
              <LiveElapsedBadge startTime={message.startTime} />
            ) : message.elapsedMs !== null && message.elapsedMs >= 0 ? (
              <div className="elapsed-badge">{formatElapsedMs(message.elapsedMs)}</div>
            ) : null}
          </div>
        ),
      )}
    </div>
  );
}
