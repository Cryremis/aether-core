/**
 * AetherCore universal embed shell.
 *
 * Copy this file into any host platform and initialize it after the user
 * session is available. The host backend only needs to expose one bind API
 * that returns { token, session_id } or { data: { token, session_id } }.
 */
(function () {
  const DEFAULTS = {
    bindUrl: "/api/v1/aethercore/embed/bind",
    workbenchUrl: "http://localhost:5178",
    platformKey: "custom",
    title: "AetherCore",
    subtitle: "嵌入式工作台",
    rootId: "aethercore-embed-root",
    storagePrefix: "aethercore_conversation_key",
    sessionStoragePrefix: "aethercore_last_session",
    conversationIdStoragePrefix: "aethercore_last_conversation_id",
    assistantPreview: {
      enabled: false,
      autoHideMs: 8000,
      maxLength: 500,
      streamThrottleMs: 120,
      showLatestOnClose: true,
      proactive: {
        enabled: false,
        sessionStoragePrefix: "aethercore_assistant_prompt_shown",
        cooldownStoragePrefix: "aethercore_assistant_prompt_next",
        initialDelayMinMs: 30000,
        initialDelayMaxMs: 60000,
        cooldownMinMs: 60 * 60 * 1000,
        cooldownMaxMs: 4 * 60 * 60 * 1000,
        editingRetryMinMs: 15000,
        editingRetryMaxMs: 30000,
        messages: [
          "快来试试，我可以帮你操作平台。",
          "我可以帮你操作当前平台。",
          "有什么问题可以来问我。",
        ],
      },
    },
    width: 760,
    minWidth: 420,
    maxWidth: 1100,
    bottom: 44,
    right: 28,
    autoBind: true,
    autoOpen: false,
    credentials: "include",
    headers: {},
    labels: {
      openAriaLabel: "Open AetherCore",
      closeAriaLabel: "Close AetherCore",
      connect: "连接 Agent",
      connecting: "连接中...",
      retry: "重试连接",
    },
    theme: {
      bubbleGradient: "linear-gradient(135deg,#2563eb 0%,#14b8a6 100%)",
      bubbleShadow: "0 18px 42px rgba(37,99,235,.32)",
      bubbleHoverShadow: "0 22px 52px rgba(37,99,235,.38)",
      overlayBackground: "rgba(15,23,42,.28)",
      drawerBackground: "#f3f4f8",
      panelBackground: "#f3f4f8",
      toolbarBackground: "rgba(255,255,255,.7)",
      loadingBackground: "linear-gradient(180deg,#f8fafc 0%,#e8eef7 100%)",
    },
    hideBubbleWhenOpen: false,
    showToolbar: true,
    closeOnOverlayClick: true,
    showOverlay: true,
    embedTheme: null,
    embedThemeChangeable: true,
    getUserId: function () {
      return (
        window.__AETHERCORE_USER_ID__ ||
        window.__USER_IDENTIFIER__ ||
        window.__USER_ID__ ||
        "anonymous"
      );
    },
    getBindPayload: null,
    getBindRequest: null,
    onOpen: null,
    onClose: null,
    onBindStart: null,
    onBindSuccess: null,
    onBindError: null,
    onResize: null,
    onAssistantPreview: null,
    onError: function (error) {
      console.error("[AetherCore]", error);
    },
  };

  function mergeOptions(options) {
    const next = Object.assign({}, DEFAULTS, options || {});
    next.labels = Object.assign({}, DEFAULTS.labels, (options && options.labels) || {});
    next.theme = Object.assign({}, DEFAULTS.theme, (options && options.theme) || {});
    const previewOptions = (options && options.assistantPreview) || {};
    next.assistantPreview = Object.assign({}, DEFAULTS.assistantPreview, previewOptions);
    next.assistantPreview.proactive = Object.assign(
      {},
      DEFAULTS.assistantPreview.proactive,
      previewOptions.proactive || {}
    );
    return next;
  }

  function randomBetween(minimum, maximum) {
    const min = Math.max(0, Number(minimum) || 0);
    const max = Math.max(min, Number(maximum) || min);
    return min + Math.floor(Math.random() * (max - min + 1));
  }

  function normalizePreviewText(value, maxLength) {
    let text = String(value || "")
      .replace(/```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)```/g, "$1")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]\s|\d+[.)]\s)\s*/gm, "")
      .replace(/[*_~`]+/g, "")
      .replace(/\s+/g, " ")
      .trim();
    const limit = Math.max(1, Number(maxLength) || 500);
    const characters = Array.from(text);
    if (characters.length > limit) text = `${characters.slice(0, limit).join("")}…`;
    return text;
  }

  function isEditingElement(element) {
    return Boolean(
      element &&
        (element.tagName === "INPUT" ||
          element.tagName === "TEXTAREA" ||
          element.isContentEditable ||
          (typeof element.closest === "function" && element.closest("[contenteditable='true']")))
    );
  }

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (char) {
      const random = (Math.random() * 16) | 0;
      const value = char === "x" ? random : (random & 0x3) | 0x8;
      return value.toString(16);
    });
  }

  function parseBindResult(payload) {
    const data = payload && payload.data ? payload.data : payload;
    const token = data && (data.token || data.embed_token);
    const sessionId = data && (data.session_id || data.sessionId);
    const conversationId = data && (data.conversation_id || data.conversationId);
    const workbenchUrl = data && (data.workbench_url || data.workbenchUrl);
    if (!token || !sessionId) {
      throw new Error("AetherCore bind response must include token and session_id.");
    }
    return { token: token, sessionId: sessionId, conversationId: conversationId, workbenchUrl: workbenchUrl };
  }

  function injectStyles(config) {
    if (document.getElementById("aethercore-embed-styles")) return;
    const css = `
      .ac-embed-root{position:fixed;z-index:99999;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
      .ac-embed-bubble{position:fixed;right:${config.right}px;bottom:${config.bottom}px;width:62px;height:62px;padding:0;border:1px solid rgba(255,255,255,.42);border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;color:rgba(15,23,42,.82);background:linear-gradient(180deg,rgba(255,255,255,.28),rgba(255,255,255,.14));box-shadow:0 22px 48px rgba(15,23,42,.14),0 10px 22px rgba(15,23,42,.08),inset 0 1px 0 rgba(255,255,255,.46),inset 0 -10px 20px rgba(148,163,184,.08);backdrop-filter:blur(18px) saturate(180%);-webkit-backdrop-filter:blur(18px) saturate(180%);transition:transform .24s ease,box-shadow .24s ease,border-color .24s ease,background .24s ease,color .24s ease;isolation:isolate;overflow:visible}
      .ac-embed-bubble:before{content:"";position:absolute;inset:3px;border-radius:inherit;background:radial-gradient(circle at 30% 22%,rgba(255,255,255,.52),transparent 34%),radial-gradient(circle at 72% 78%,rgba(255,255,255,.16),transparent 28%),linear-gradient(160deg,rgba(255,255,255,.2),rgba(255,255,255,.04) 44%,rgba(148,163,184,.08) 100%);z-index:0;pointer-events:none}
      .ac-embed-bubble:after{content:"";position:absolute;top:-4px;right:-4px;width:15px;height:15px;border-radius:50%;background:linear-gradient(180deg,rgba(255,255,255,.96),rgba(226,232,240,.84));border:1px solid rgba(255,255,255,.86);box-shadow:0 6px 16px rgba(15,23,42,.12);z-index:3;pointer-events:none}
      .ac-embed-bubble:hover{transform:translateY(-3px) scale(1.045);border-color:rgba(255,255,255,.56);background:linear-gradient(180deg,rgba(255,255,255,.34),rgba(255,255,255,.18));box-shadow:0 28px 56px rgba(15,23,42,.16),0 12px 24px rgba(15,23,42,.08),inset 0 1px 0 rgba(255,255,255,.56),inset 0 -10px 20px rgba(148,163,184,.08)}
      .ac-embed-bubble:active{transform:translateY(-1px) scale(1.01)}
      .ac-embed-bubble:focus-visible{outline:none;border-color:rgba(255,255,255,.62);box-shadow:0 0 0 4px rgba(255,255,255,.18),0 0 0 8px rgba(148,163,184,.12),0 24px 52px rgba(15,23,42,.16),inset 0 1px 0 rgba(255,255,255,.56),inset 0 -10px 20px rgba(148,163,184,.08)}
      .ac-embed-bubble.is-open{transform:translateY(-2px);border-color:rgba(255,255,255,.58);background:linear-gradient(180deg,rgba(255,255,255,.36),rgba(255,255,255,.2));box-shadow:0 28px 60px rgba(15,23,42,.16),0 14px 30px rgba(15,23,42,.08),inset 0 1px 0 rgba(255,255,255,.58),inset 0 -10px 22px rgba(14,165,233,.09)}
      .ac-embed-bubble.is-open:after{background:linear-gradient(180deg,#f0fdf4 0%,#bbf7d0 100%);border-color:rgba(255,255,255,.92);box-shadow:0 6px 16px rgba(34,197,94,.16)}
      .ac-embed-bubble__icon-wrap{position:relative;z-index:1;display:flex;align-items:center;justify-content:center;width:42px;height:42px;border-radius:50%;background:linear-gradient(180deg,rgba(255,255,255,.3),rgba(255,255,255,.12));box-shadow:inset 0 1px 0 rgba(255,255,255,.34),inset 0 -8px 16px rgba(148,163,184,.08),0 6px 14px rgba(15,23,42,.08);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
      .ac-embed-bubble__icon{position:relative;z-index:2;display:block;filter:drop-shadow(0 2px 5px rgba(255,255,255,.3))}
      .ac-embed-bubble__icon path{stroke-width:2.1}
      .ac-embed-bubble.is-hidden{display:none}
      .ac-embed-preview{position:fixed;right:${config.right}px;bottom:${config.bottom + 76}px;display:none;width:min(360px,calc(100vw - 32px));min-height:68px;box-sizing:border-box;padding:13px 40px 13px 16px;color:#1f2937;background:rgba(255,255,255,.98);border:1px solid #d7dee8;border-radius:8px;box-shadow:0 8px 24px rgba(31,41,55,.14);animation:acEmbedPreviewEnter .2s ease-out}
      .ac-embed-preview.is-visible{display:block}
      .ac-embed-preview:after{position:absolute;right:24px;bottom:-7px;width:12px;height:12px;content:"";background:#fff;border-right:1px solid #d7dee8;border-bottom:1px solid #d7dee8;transform:rotate(45deg)}
      .ac-embed-preview__text{display:-webkit-box;width:100%;max-height:65.1px;padding:0;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:3;font:inherit;font-size:14px;line-height:1.55;letter-spacing:0;color:inherit;text-align:left;overflow-wrap:anywhere;cursor:pointer;background:transparent;border:0}
      .ac-embed-preview__text:hover{color:#166534}
      .ac-embed-preview__close{position:absolute;z-index:1;top:7px;right:7px;display:inline-flex;width:26px;height:26px;align-items:center;justify-content:center;padding:0;color:#6b7280;cursor:pointer;background:transparent;border:0;border-radius:50%;font-size:18px;line-height:1}
      .ac-embed-preview__close:hover{color:#111827;background:#f1f5f9}
      .ac-embed-modal{position:fixed;inset:0;display:none;background:${config.theme.overlayBackground};z-index:99998;backdrop-filter:blur(2px)}
      .ac-embed-modal.is-open{display:block}
      .ac-embed-modal.is-transparent{background:transparent;backdrop-filter:none}
      .ac-embed-drawer{position:fixed;top:0;right:0;bottom:0;display:none;width:${config.width}px;max-width:calc(100vw - 24px);background:${config.theme.drawerBackground};box-shadow:-22px 0 60px rgba(15,23,42,.18);z-index:99999;overflow:hidden}
      .ac-embed-drawer.is-open{display:flex}
      .ac-embed-drawer.is-resizing{transition:none!important}
      .ac-embed-resize{width:18px;flex:0 0 18px;cursor:ew-resize;position:relative;touch-action:none}
      .ac-embed-resize:before{content:"";position:absolute;left:8px;top:50%;width:2px;height:64px;border-radius:999px;background:rgba(100,116,139,.26);transform:translateY(-50%)}
      .ac-embed-panel{position:relative;flex:1;min-width:0;height:100%;display:flex;flex-direction:column;background:${config.theme.panelBackground}}
      .ac-embed-panel.is-toolbar-hidden .ac-embed-toolbar{display:none}
      .ac-embed-toolbar{height:58px;flex:0 0 58px;padding:0 18px 0 22px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(148,163,184,.22);background:${config.theme.toolbarBackground};backdrop-filter:blur(14px)}
      .ac-embed-title{display:flex;flex-direction:column;gap:2px;color:#0f172a}
      .ac-embed-title strong{font-size:15px;letter-spacing:.01em}
      .ac-embed-title span{font-size:12px;color:#64748b}
      .ac-embed-close{width:34px;height:34px;border:0;border-radius:999px;background:rgba(15,23,42,.06);color:#334155;cursor:pointer;font-size:22px;line-height:1}
      .ac-embed-close:hover{background:rgba(15,23,42,.1)}
      .ac-embed-body{position:relative;flex:1;min-height:0;overflow:hidden}
      .ac-embed-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:${config.theme.loadingBackground}}
      .ac-embed-card{min-width:250px;padding:22px 26px;border:1px solid rgba(148,163,184,.24);border-radius:20px;background:rgba(255,255,255,.92);box-shadow:0 18px 40px rgba(15,23,42,.10);color:#334155;font-size:14px;font-weight:700;cursor:pointer}
      .ac-embed-card[disabled]{cursor:wait;opacity:.72}
      .ac-embed-frame{width:100%;height:100%;border:0;display:block;background:#f3f4f8;opacity:0;transition:opacity .18s ease}
      .ac-embed-frame.is-loaded{opacity:1}
      .ac-embed-shield{position:absolute;inset:0;z-index:3;background:transparent;cursor:ew-resize}
      @keyframes acEmbedPreviewEnter{from{opacity:0;transform:translateY(6px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}
      @media (prefers-reduced-motion:no-preference){.ac-embed-bubble{animation:acEmbedBubbleFloat 3.8s ease-in-out infinite}.ac-embed-bubble:hover,.ac-embed-bubble:active,.ac-embed-bubble.is-open{animation-play-state:paused}}
      @keyframes acEmbedBubbleFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-2px)}}
      @media (prefers-reduced-motion:reduce){.ac-embed-preview{animation:none}}
      @media (max-width:640px){.ac-embed-drawer{left:12px;right:12px;width:auto!important;border-radius:20px 20px 0 0}.ac-embed-resize{display:none}.ac-embed-bubble{right:20px;bottom:28px;width:58px;height:58px}.ac-embed-bubble__icon-wrap{width:39px;height:39px}.ac-embed-preview{right:16px;bottom:98px;width:min(360px,calc(100vw - 32px))}}
    `;
    const style = document.createElement("style");
    style.id = "aethercore-embed-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }

  class AetherCoreEmbed {
    constructor(options) {
      this.config = mergeOptions(options);
      this.state = {
        open: false,
        binding: false,
        iframeLoaded: false,
        embedUrl: "",
        conversationKey: "",
      };
      this.width = this.config.width;
      this.cleanupResize = null;
      this.eventController = new AbortController();
      this.proactiveTimer = null;
      this.previewHideTimer = null;
      this.previewStreamTimer = null;
      this.pendingStreamPreview = null;
      this.latestAssistantPreview = null;
      this.assistantPreviewRevision = 0;
      this.presentedAssistantPreviewRevision = 0;
      this.handleWorkbenchMessage = this.handleWorkbenchMessage.bind(this);
      this.handleWindowResize = this.handleWindowResize.bind(this);
    }

    init() {
      this.state.conversationKey = this.getConversationKey();
      this.render();
      this.applyWidth(this.clampWidth(this.width));
      this.bindEvents();
      this.scheduleInitialProactivePreview();
      if (this.config.autoOpen) this.open();
      return this;
    }

    emitHook(name, payload) {
      const handler = this.config[name];
      if (typeof handler === "function") {
        try {
          handler(payload, this.state, this.config);
        } catch (error) {
          console.error("[AetherCore] hook failed:", name, error);
        }
      }
    }

    getConversationKey() {
      const userId = this.config.getUserId();
      const key = `${this.config.storagePrefix}_${this.config.platformKey}_${userId}`;
      const existing = window.localStorage && window.localStorage.getItem(key);
      if (existing) return existing;
      const next = `${this.config.platformKey}-${userId}-${uuid()}`;
      if (window.localStorage) window.localStorage.setItem(key, next);
      return next;
    }

    getPerUserStorageKey(prefix) {
      const userId = this.config.getUserId();
      return `${prefix}_${this.config.platformKey}_${userId}`;
    }

    getLastSessionId() {
      const key = this.getPerUserStorageKey(this.config.sessionStoragePrefix);
      return window.localStorage && window.localStorage.getItem(key);
    }

    setLastSessionId(sessionId) {
      const key = this.getPerUserStorageKey(this.config.sessionStoragePrefix);
      if (!window.localStorage) return;
      if (sessionId) window.localStorage.setItem(key, sessionId);
      else window.localStorage.removeItem(key);
    }

    getLastConversationId() {
      const key = this.getPerUserStorageKey(this.config.conversationIdStoragePrefix);
      return window.localStorage && window.localStorage.getItem(key);
    }

    setLastConversationId(conversationId) {
      const key = this.getPerUserStorageKey(this.config.conversationIdStoragePrefix);
      if (!window.localStorage) return;
      if (conversationId) window.localStorage.setItem(key, conversationId);
      else window.localStorage.removeItem(key);
    }

    clampWidth(width) {
      const upperBound = Math.min(
        this.config.maxWidth,
        Math.max(this.config.minWidth, window.innerWidth - 48)
      );
      return Math.min(Math.max(width, this.config.minWidth), upperBound);
    }

    render() {
      if (document.getElementById(this.config.rootId)) return;
      injectStyles(this.config);
      const root = document.createElement("div");
      const toolbarClass = this.config.showToolbar ? "ac-embed-panel" : "ac-embed-panel is-toolbar-hidden";
      const modalClass = this.config.showOverlay ? "ac-embed-modal" : "ac-embed-modal is-transparent";
      root.id = this.config.rootId;
      root.className = "ac-embed-root";
      root.innerHTML = `
        <aside class="ac-embed-preview" aria-live="polite" aria-atomic="true">
          <button type="button" class="ac-embed-preview__text"></button>
          <button type="button" class="ac-embed-preview__close" aria-label="关闭 Agent 提示" title="关闭">×</button>
        </aside>
        <button type="button" class="ac-embed-bubble" aria-label="${this.config.labels.openAriaLabel}">
          <span class="ac-embed-bubble__icon-wrap" aria-hidden="true">
            <svg class="ac-embed-bubble__icon" width="25" height="25" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
            </svg>
          </span>
        </button>
        <div class="${modalClass}"></div>
        <section class="ac-embed-drawer" aria-label="AetherCore Workbench">
          <div class="ac-embed-resize"></div>
          <div class="${toolbarClass}">
            <header class="ac-embed-toolbar">
              <div class="ac-embed-title"><strong>${this.config.title}</strong><span>${this.config.subtitle}</span></div>
              <button type="button" class="ac-embed-close" aria-label="${this.config.labels.closeAriaLabel}">×</button>
            </header>
            <main class="ac-embed-body">
              <div class="ac-embed-loading">
                <button type="button" class="ac-embed-card">${this.config.labels.connect}</button>
              </div>
              <iframe class="ac-embed-frame" title="AetherCore Workbench" allow="clipboard-write"></iframe>
            </main>
          </div>
        </section>
      `;
      document.body.appendChild(root);
    }

    bindEvents() {
      const root = document.getElementById(this.config.rootId);
      const signal = this.eventController.signal;
      root.querySelector(".ac-embed-bubble").addEventListener("click", () => this.toggle(), { signal });
      root.querySelector(".ac-embed-preview__text").addEventListener("click", () => this.open(), { signal });
      root.querySelector(".ac-embed-preview__close").addEventListener("click", () => this.hideAssistantPreview(), { signal });
      root.querySelector(".ac-embed-modal").addEventListener("click", () => {
        if (this.config.closeOnOverlayClick) this.close();
      }, { signal });
      root.querySelector(".ac-embed-close").addEventListener("click", () => this.close(), { signal });
      root.querySelector(".ac-embed-card").addEventListener("click", () => this.bindToAetherCore(), { signal });
      root.querySelector(".ac-embed-frame").addEventListener("load", () => this.handleFrameLoad(), { signal });
      root.querySelector(".ac-embed-resize").addEventListener("pointerdown", (event) => this.startResize(event), { signal });
      window.addEventListener("resize", this.handleWindowResize, { signal });
      window.addEventListener("message", this.handleWorkbenchMessage, { signal });
    }

    handleWindowResize() {
      this.applyWidth(this.clampWidth(this.width));
    }

    isWorkbenchMessage(event) {
      const root = document.getElementById(this.config.rootId);
      const frame = root && root.querySelector(".ac-embed-frame");
      if (!frame || event.source !== frame.contentWindow) return false;
      try {
        const expectedOrigin = new URL(frame.src, window.location.href).origin;
        return !event.origin || event.origin === expectedOrigin;
      } catch (_error) {
        return false;
      }
    }

    handleWorkbenchMessage(event) {
      const data = event && event.data;
      if (!this.isWorkbenchMessage(event) || !data || data.source !== "aethercore-workbench") {
        return;
      }
      const payload = data.payload || {};
      if (data.type === "aethercore:session-changed") {
        if (payload.session_id) {
          this.setLastSessionId(String(payload.session_id));
        }
        if (payload.conversation_id) {
          this.setLastConversationId(String(payload.conversation_id));
        }
        return;
      }

      if (data.type !== "aethercore:assistant-preview" || !this.config.assistantPreview.enabled) {
        return;
      }
      const preview = {
        sessionId: String(payload.session_id || ""),
        messageId: String(payload.message_id || ""),
        contentId: String(payload.content_id || ""),
        content: String(payload.content || ""),
        status: payload.status === "completed" ? "completed" : "streaming",
      };
      if (!preview.content || !preview.messageId || !preview.contentId) return;
      this.queueAssistantPreview(preview);
    }

    queueAssistantPreview(preview) {
      if (preview.status === "completed") {
        if (this.previewStreamTimer) window.clearTimeout(this.previewStreamTimer);
        this.previewStreamTimer = null;
        this.pendingStreamPreview = null;
        this.showAssistantPreview(preview.content, { kind: "assistant", preview });
        return;
      }
      this.pendingStreamPreview = preview;
      if (this.previewStreamTimer) return;
      this.previewStreamTimer = window.setTimeout(() => {
        const pending = this.pendingStreamPreview;
        this.previewStreamTimer = null;
        this.pendingStreamPreview = null;
        if (pending) this.showAssistantPreview(pending.content, { kind: "assistant", preview: pending });
      }, Math.max(0, Number(this.config.assistantPreview.streamThrottleMs) || 0));
    }

    getProactiveStorageKey(prefix) {
      return this.getPerUserStorageKey(prefix);
    }

    isProactiveSuppressed() {
      const prefix = this.config.assistantPreview.proactive.sessionStoragePrefix;
      try {
        return window.sessionStorage.getItem(this.getProactiveStorageKey(prefix)) === "1";
      } catch (_error) {
        return false;
      }
    }

    suppressProactiveForSession() {
      const prefix = this.config.assistantPreview.proactive.sessionStoragePrefix;
      try {
        window.sessionStorage.setItem(this.getProactiveStorageKey(prefix), "1");
      } catch (_error) {
        // Storage can be unavailable in privacy-restricted embeds.
      }
      if (this.proactiveTimer) window.clearTimeout(this.proactiveTimer);
      this.proactiveTimer = null;
    }

    getNextProactiveAt() {
      const prefix = this.config.assistantPreview.proactive.cooldownStoragePrefix;
      try {
        return Number(window.localStorage.getItem(this.getProactiveStorageKey(prefix))) || 0;
      } catch (_error) {
        return 0;
      }
    }

    setNextProactiveAt(timestamp) {
      const prefix = this.config.assistantPreview.proactive.cooldownStoragePrefix;
      try {
        window.localStorage.setItem(this.getProactiveStorageKey(prefix), String(timestamp));
      } catch (_error) {
        // The in-memory session guard still prevents repeated prompts in this tab.
      }
    }

    scheduleInitialProactivePreview() {
      const preview = this.config.assistantPreview;
      const proactive = preview.proactive;
      if (!preview.enabled || !proactive.enabled || this.isProactiveSuppressed()) return;
      const initialDelay = randomBetween(proactive.initialDelayMinMs, proactive.initialDelayMaxMs);
      const cooldownDelay = Math.max(0, this.getNextProactiveAt() - Date.now());
      this.scheduleProactivePreview(Math.max(initialDelay, cooldownDelay));
    }

    scheduleProactivePreview(delay) {
      if (this.proactiveTimer) window.clearTimeout(this.proactiveTimer);
      this.proactiveTimer = window.setTimeout(() => {
        this.proactiveTimer = null;
        this.tryShowProactivePreview();
      }, Math.max(0, delay));
    }

    resolveProactiveMessage() {
      const configured = this.config.assistantPreview.proactive.messages;
      let messages = configured;
      if (typeof configured === "function") {
        try {
          messages = configured(this.state, this.config);
        } catch (error) {
          this.emitHook("onError", error);
          return "";
        }
      }
      if (typeof messages === "string") return messages;
      if (!Array.isArray(messages) || messages.length === 0) return "";
      return String(messages[Math.floor(Math.random() * messages.length)] || "");
    }

    tryShowProactivePreview() {
      const proactive = this.config.assistantPreview.proactive;
      if (this.isProactiveSuppressed()) return;
      if (this.state.open || isEditingElement(document.activeElement)) {
        this.scheduleProactivePreview(
          randomBetween(proactive.editingRetryMinMs, proactive.editingRetryMaxMs)
        );
        return;
      }
      const message = this.resolveProactiveMessage();
      if (!message) return;
      this.suppressProactiveForSession();
      this.setNextProactiveAt(
        Date.now() + randomBetween(proactive.cooldownMinMs, proactive.cooldownMaxMs)
      );
      this.showAssistantPreview(message, { kind: "proactive" });
    }

    showAssistantPreview(text, options) {
      if (!this.config.assistantPreview.enabled) return false;
      const content = normalizePreviewText(text, this.config.assistantPreview.maxLength);
      if (!content) return false;
      const previewOptions = options || {};
      const kind = previewOptions.kind || "manual";
      if (kind === "assistant" && previewOptions.remember !== false) {
        this.assistantPreviewRevision += 1;
        this.latestAssistantPreview = {
          content,
          options: Object.assign({}, previewOptions, { remember: false }),
        };
      }
      if (this.state.open) return false;
      const root = document.getElementById(this.config.rootId);
      if (!root) return false;
      const preview = root.querySelector(".ac-embed-preview");
      const textElement = root.querySelector(".ac-embed-preview__text");
      textElement.textContent = content;
      preview.classList.add("is-visible");
      if (this.previewHideTimer) window.clearTimeout(this.previewHideTimer);
      const autoHideMs = Math.max(0, Number(this.config.assistantPreview.autoHideMs) || 0);
      if (autoHideMs > 0) {
        this.previewHideTimer = window.setTimeout(() => this.hideAssistantPreview(), autoHideMs);
      }
      this.emitHook("onAssistantPreview", {
        content,
        kind,
        preview: previewOptions.preview || null,
      });
      if (kind === "assistant") {
        this.presentedAssistantPreviewRevision = this.assistantPreviewRevision;
      }
      return true;
    }

    hideAssistantPreview() {
      const root = document.getElementById(this.config.rootId);
      const preview = root && root.querySelector(".ac-embed-preview");
      if (preview) preview.classList.remove("is-visible");
      if (this.previewHideTimer) window.clearTimeout(this.previewHideTimer);
      this.previewHideTimer = null;
    }

    open() {
      const root = document.getElementById(this.config.rootId);
      if (!root) return;
      this.hideAssistantPreview();
      this.state.open = true;
      root.querySelector(".ac-embed-drawer").classList.add("is-open");
      root.querySelector(".ac-embed-modal").classList.add("is-open");
      root.querySelector(".ac-embed-bubble").classList.add("is-open");
      if (this.config.hideBubbleWhenOpen) {
        root.querySelector(".ac-embed-bubble").classList.add("is-hidden");
      }
      this.emitHook("onOpen");
      if (this.config.autoBind && !this.state.embedUrl) this.bindToAetherCore();
    }

    close() {
      const root = document.getElementById(this.config.rootId);
      if (!root) return;
      this.state.open = false;
      root.querySelector(".ac-embed-drawer").classList.remove("is-open");
      root.querySelector(".ac-embed-modal").classList.remove("is-open");
      root.querySelector(".ac-embed-bubble").classList.remove("is-hidden");
      root.querySelector(".ac-embed-bubble").classList.remove("is-open");
      if (
        this.config.assistantPreview.showLatestOnClose &&
        this.latestAssistantPreview &&
        this.assistantPreviewRevision > this.presentedAssistantPreviewRevision
      ) {
        this.showAssistantPreview(
          this.latestAssistantPreview.content,
          this.latestAssistantPreview.options
        );
      }
      this.emitHook("onClose");
    }

    toggle() {
      if (this.state.open) this.close();
      else this.open();
    }

    async buildBindRequest() {
      const defaultBody = {
        conversation_key: this.state.conversationKey,
        session_id: this.getLastSessionId() || null,
        conversation_id: this.getLastConversationId() || null,
      };

      if (typeof this.config.getBindRequest === "function") {
        const requestConfig = await this.config.getBindRequest(this.state, this.config);
        return Object.assign(
          {
            url: this.config.bindUrl,
            method: "POST",
            credentials: this.config.credentials,
            headers: {},
            body: defaultBody,
          },
          requestConfig || {},
          {
            body: Object.assign({}, defaultBody, (requestConfig && requestConfig.body) || {}),
          }
        );
      }

      const payload =
        typeof this.config.getBindPayload === "function"
          ? await this.config.getBindPayload(this.state)
          : defaultBody;

      return {
        url: this.config.bindUrl,
        method: "POST",
        credentials: this.config.credentials,
        headers: Object.assign({}, this.config.headers),
        body: payload || {},
      };
    }

    async bindToAetherCore() {
      if (this.state.binding) return;
      const root = document.getElementById(this.config.rootId);
      const button = root.querySelector(".ac-embed-card");
      this.state.binding = true;
      button.textContent = this.config.labels.connecting;
      button.disabled = true;
      this.emitHook("onBindStart");

      try {
        const requestConfig = await this.buildBindRequest();
        const response = await fetch(requestConfig.url, {
          method: requestConfig.method || "POST",
          credentials:
            Object.prototype.hasOwnProperty.call(requestConfig, "credentials")
              ? requestConfig.credentials
              : this.config.credentials,
          headers: Object.assign(
            { "Content-Type": "application/json" },
            this.config.headers,
            requestConfig.headers || {}
          ),
          body: JSON.stringify(requestConfig.body || {}),
        });
        if (!response.ok) {
          throw new Error(`AetherCore bind failed: ${response.status} ${await response.text()}`);
        }
        const result = parseBindResult(await response.json());
        this.setLastSessionId(result.sessionId);
        this.setLastConversationId(result.conversationId || null);
        this.state.embedUrl =
          result.workbenchUrl ||
          `${this.config.workbenchUrl}?embed_token=${encodeURIComponent(result.token)}&session_id=${encodeURIComponent(result.sessionId)}`;
        if (this.config.embedTheme) {
          this.state.embedUrl += `&embed_theme=${encodeURIComponent(this.config.embedTheme)}`;
        }
        if (this.config.embedThemeChangeable === false) {
          this.state.embedUrl += `&embed_theme_changeable=false`;
        }
        this.state.iframeLoaded = false;
        root.querySelector(".ac-embed-frame").classList.remove("is-loaded");
        root.querySelector(".ac-embed-frame").src = this.state.embedUrl;
        this.emitHook("onBindSuccess", result);
      } catch (error) {
        button.textContent = this.config.labels.retry;
        button.disabled = false;
        this.emitHook("onBindError", error);
        this.config.onError(error);
      } finally {
        this.state.binding = false;
      }
    }

    handleFrameLoad() {
      const root = document.getElementById(this.config.rootId);
      this.state.iframeLoaded = true;
      root.querySelector(".ac-embed-loading").style.display = "none";
      root.querySelector(".ac-embed-frame").classList.add("is-loaded");
    }

    applyWidth(width) {
      this.width = width;
      const drawer = document.getElementById(this.config.rootId).querySelector(".ac-embed-drawer");
      drawer.style.width = `${width}px`;
      this.emitHook("onResize", width);
    }

    startResize(event) {
      event.preventDefault();
      const root = document.getElementById(this.config.rootId);
      const drawer = root.querySelector(".ac-embed-drawer");
      const body = root.querySelector(".ac-embed-body");
      const shield = document.createElement("div");
      const startX = event.clientX;
      const startWidth = this.width;

      shield.className = "ac-embed-shield";
      body.appendChild(shield);
      drawer.classList.add("is-resizing");

      const handleMove = (moveEvent) => {
        this.applyWidth(this.clampWidth(startWidth + startX - moveEvent.clientX));
      };
      const handleUp = () => {
        window.removeEventListener("pointermove", handleMove);
        window.removeEventListener("pointerup", handleUp);
        drawer.classList.remove("is-resizing");
        shield.remove();
        this.cleanupResize = null;
      };

      if (this.cleanupResize) this.cleanupResize();
      window.addEventListener("pointermove", handleMove);
      window.addEventListener("pointerup", handleUp);
      this.cleanupResize = handleUp;
    }

    destroy() {
      if (this.cleanupResize) this.cleanupResize();
      if (this.proactiveTimer) window.clearTimeout(this.proactiveTimer);
      if (this.previewHideTimer) window.clearTimeout(this.previewHideTimer);
      if (this.previewStreamTimer) window.clearTimeout(this.previewStreamTimer);
      this.latestAssistantPreview = null;
      this.eventController.abort();
      document.getElementById(this.config.rootId)?.remove();
    }

    setTheme(theme) {
      var frame = this.root && this.root.querySelector(".ac-embed-frame");
      if (!frame || !frame.contentWindow) return;
      var origin = "*";
      try { origin = new URL(this.state.embedUrl || this.config.workbenchUrl).origin; } catch (e) {}
      frame.contentWindow.postMessage({
        source: "aethercore-host",
        type: "aethercore:theme",
        payload: { theme: theme },
      }, origin);
    }
  }

  window.AetherCoreEmbed = AetherCoreEmbed;
  window.mountAetherCore = function (options) {
    return new AetherCoreEmbed(options).init();
  };
})();
