// host-adapters/dash/src/aethercore-host.js
(function () {
  /**
   * Dash 侧的宿主桥接器。
   * 当前先提供最小骨架，后续逐步接入页面上下文、宿主工具与工作台挂载。
   */
  class AetherCoreDashHost {
    constructor(options = {}) {
      this.options = options;
      this.baseUrl = options.baseUrl || "http://127.0.0.1:8100";
    }

    buildPayload() {
      return {
        host_name: "ascend-compete-dash",
        host_type: "dash",
        context: {
          user: {
            display_name: window.__USER_DISPLAY_NAME__ || "Dash 用户",
          },
          page: {
            title: document.title,
            pathname: window.location.pathname,
          },
        },
        tools: [],
        skills: [],
        apis: [],
      };
    }

    async bind() {
      const response = await fetch(`${this.baseUrl}/api/v1/host/bind`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(this.buildPayload()),
      });
      if (!response.ok) {
        throw new Error(`AetherCore 宿主绑定失败：${response.status}`);
      }
      return response.json();
    }
  }

  window.AetherCoreDashHost = AetherCoreDashHost;
})();
