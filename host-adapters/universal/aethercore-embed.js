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
    onError: function (error) {
      console.error("[AetherCore]", error);
    },
  };

  function mergeOptions(options) {
    const next = Object.assign({}, DEFAULTS, options || {});
    next.labels = Object.assign({}, DEFAULTS.labels, (options && options.labels) || {});
    next.theme = Object.assign({}, DEFAULTS.theme, (options && options.theme) || {});
    return next;
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
    const workbenchUrl = data && (data.workbench_url || data.workbenchUrl);
    if (!token || !sessionId) {
      throw new Error("AetherCore bind response must include token and session_id.");
    }
    return { token: token, sessionId: sessionId, workbenchUrl: workbenchUrl };
  }

  function injectStyles(config) {
    if (document.getElementById("aethercore-embed-styles")) return;
    const css = `
      .ac-embed-root{position:fixed;z-index:99999;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
      .ac-embed-bubble{position:fixed;right:${config.right}px;bottom:${config.bottom}px;width:56px;height:56px;border:0;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;color:#fff;background:${config.theme.bubbleGradient};box-shadow:${config.theme.bubbleShadow};transition:transform .24s ease,box-shadow .24s ease}
      .ac-embed-bubble:hover{transform:translateY(-2px) scale(1.04);box-shadow:${config.theme.bubbleHoverShadow}}
      .ac-embed-bubble.is-hidden{display:none}
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
      @media (max-width:640px){.ac-embed-drawer{left:12px;right:12px;width:auto!important;border-radius:20px 20px 0 0}.ac-embed-resize{display:none}.ac-embed-bubble{right:20px;bottom:28px}}
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
    }

    init() {
      this.state.conversationKey = this.getConversationKey();
      this.render();
      this.applyWidth(this.clampWidth(this.width));
      this.bindEvents();
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
        <button type="button" class="ac-embed-bubble" aria-label="${this.config.labels.openAriaLabel}">
          <svg width="26" height="26" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
          </svg>
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
      root.querySelector(".ac-embed-bubble").addEventListener("click", () => this.toggle());
      root.querySelector(".ac-embed-modal").addEventListener("click", () => {
        if (this.config.closeOnOverlayClick) this.close();
      });
      root.querySelector(".ac-embed-close").addEventListener("click", () => this.close());
      root.querySelector(".ac-embed-card").addEventListener("click", () => this.bindToAetherCore());
      root.querySelector(".ac-embed-frame").addEventListener("load", () => this.handleFrameLoad());
      root.querySelector(".ac-embed-resize").addEventListener("pointerdown", (event) => this.startResize(event));
      window.addEventListener("resize", () => this.applyWidth(this.clampWidth(this.width)));
    }

    open() {
      const root = document.getElementById(this.config.rootId);
      this.state.open = true;
      root.querySelector(".ac-embed-drawer").classList.add("is-open");
      root.querySelector(".ac-embed-modal").classList.add("is-open");
      if (this.config.hideBubbleWhenOpen) {
        root.querySelector(".ac-embed-bubble").classList.add("is-hidden");
      }
      this.emitHook("onOpen");
      if (this.config.autoBind && !this.state.embedUrl) this.bindToAetherCore();
    }

    close() {
      const root = document.getElementById(this.config.rootId);
      this.state.open = false;
      root.querySelector(".ac-embed-drawer").classList.remove("is-open");
      root.querySelector(".ac-embed-modal").classList.remove("is-open");
      root.querySelector(".ac-embed-bubble").classList.remove("is-hidden");
      this.emitHook("onClose");
    }

    toggle() {
      if (this.state.open) this.close();
      else this.open();
    }

    async buildBindRequest() {
      if (typeof this.config.getBindRequest === "function") {
        const requestConfig = await this.config.getBindRequest(this.state, this.config);
        return Object.assign(
          {
            url: this.config.bindUrl,
            method: "POST",
            credentials: this.config.credentials,
            headers: {},
            body: { conversation_key: this.state.conversationKey },
          },
          requestConfig || {}
        );
      }

      const payload =
        typeof this.config.getBindPayload === "function"
          ? await this.config.getBindPayload(this.state)
          : { conversation_key: this.state.conversationKey };

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
        this.state.embedUrl =
          result.workbenchUrl ||
          `${this.config.workbenchUrl}?embed_token=${encodeURIComponent(result.token)}&session_id=${encodeURIComponent(result.sessionId)}`;
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
  }

  window.AetherCoreEmbed = AetherCoreEmbed;
  window.mountAetherCore = function (options) {
    return new AetherCoreEmbed(options).init();
  };
})();
