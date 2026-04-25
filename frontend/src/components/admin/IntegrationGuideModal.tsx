import type { PlatformIntegrationGuide } from "../../api/client";

type IntegrationGuideModalProps = {
  integrationGuide: PlatformIntegrationGuide | null;
  integrationGuideBusy: boolean;
  integrationGuideError: string;
  integrationGuidePlatformName: string;
  renderHighlightedSnippet: (snippet: string | undefined) => Array<string | JSX.Element> | null;
  onCopy: (value: string) => void;
  onClose: () => void;
};

export function IntegrationGuideModal({
  integrationGuide,
  integrationGuideBusy,
  integrationGuideError,
  integrationGuidePlatformName,
  renderHighlightedSnippet,
  onCopy,
  onClose,
}: IntegrationGuideModalProps) {
  if (!(integrationGuide || integrationGuideBusy || integrationGuideError)) return null;

  const integrationGuideDetails = [
    { title: "推荐接入顺序", body: "按页面顺序复制即可：先复制前端代码，再复制 .env 示例，最后复制后端 Bind 示例。默认代码已经填好平台信息，只需要检查你们平台自己的用户对象和公网地址。" },
    { title: "前端这段代码负责什么", body: "它负责浮球、抽屉、iframe、会话 key 持久化这些通用逻辑。大多数平台只需要确认 /static/aethercore-embed.js 是否能访问，以及 getUserId 能拿到当前登录用户 ID。" },
    { title: ".env 怎么用", body: ".env 示例里已经填好 AetherCore 地址、platform_key 和 platform_secret。你只需要把 PLATFORM_PUBLIC_BASE_URL 换成你们平台自己的公网根地址。如果你们框架不自动加载 .env，也可以把同样的值直接配成环境变量。" },
    { title: "后端 Bind 示例怎么用", body: "后端示例是完整 FastAPI 写法，不依赖你们项目里的 settings 对象。复制后通常只需要按你们平台的登录体系调整 request.state.user 这一行，其它逻辑就是调用 AetherCore 并返回 token 和 session_id。" },
    { title: "需要你自己替换的内容", body: "{{YOUR_PLATFORM_BASE_URL}} 需要换成你们平台对外可访问的根地址。高亮只用于提示你这里要检查，复制按钮复制出去的仍然是原始代码。" },
    { title: "如果你只想先快速接起来", body: "可以先按最小场景接入，不注入任何 tools、skills、apis，只验证浮球和对话工作台是否正常。安全加固和宿主能力注入可以后续再补。" },
    { title: "关于安全", body: "更推荐把 host_secret 放在服务端，由服务端代理 bind，这样更稳。如果你们当前阶段更在意快速验证，也可以先按自己的方式做 PoC，只要清楚这样会带来额外风险即可。" },
  ];

  return (
    <div className="guide-modal-backdrop" onClick={onClose}>
      <div className="guide-modal" onClick={(e) => e.stopPropagation()}>
        <div className="guide-modal__header">
          <div>
            <h4>接入教程</h4>
            <p>{integrationGuidePlatformName || integrationGuide?.display_name || "平台接入说明"}：按顺序复制代码块，按提示替换高亮内容即可。</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭接入教程">×</button>
        </div>

        {integrationGuideBusy ? <div className="admin-panel__empty">接入教程加载中...</div> : null}
        {integrationGuideError ? <div className="admin-panel__error">{integrationGuideError}</div> : null}

        {integrationGuide ? (
          <div className="guide-modal__body">
            <section className="guide-section">
              <div className="guide-split-layout">
                <div className="guide-split-layout__code">
                  <div className="guide-section">
                    <h5>前端复制代码</h5>
                    <div className="guide-section__head">
                      <span className="guide-section__label">复制后放到全局布局页</span>
                      <button type="button" className="action-button small primary" onClick={() => onCopy(integrationGuide.snippets.frontend)}>复制</button>
                    </div>
                    <pre className="guide-code-block"><code>{renderHighlightedSnippet(integrationGuide.snippets.frontend)}</code></pre>
                  </div>

                  <div className="guide-section">
                    <h5>后端 .env 示例</h5>
                    <div className="guide-section__head">
                      <span className="guide-section__label">推荐放到你们后端服务的环境变量或 .env 文件</span>
                      <button type="button" className="action-button small primary" onClick={() => onCopy(integrationGuide.snippets.backend_env)}>复制</button>
                    </div>
                    <pre className="guide-code-block"><code>{renderHighlightedSnippet(integrationGuide.snippets.backend_env)}</code></pre>
                  </div>

                  <div className="guide-section">
                    <h5>后端 Bind 示例（FastAPI）</h5>
                    <div className="guide-section__head">
                      <span className="guide-section__label">直接复制到你们后端项目，按注释接入当前登录用户</span>
                      <button type="button" className="action-button small primary" onClick={() => onCopy(integrationGuide.snippets.backend_fastapi)}>复制</button>
                    </div>
                    <pre className="guide-code-block"><code>{renderHighlightedSnippet(integrationGuide.snippets.backend_fastapi)}</code></pre>
                  </div>
                </div>

                <div className="guide-side-panel">
                  {integrationGuideDetails.map((item) => (
                    <div key={item.title} className="guide-side-panel__item">
                      <strong>{item.title}</strong>
                      <p>{item.body}</p>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
