// host-adapters/dash/src/workbench-launcher.js
(function () {
  /**
   * 在 Dash 页面中挂载 AetherCore 工作台。
   * 当前使用 iframe 作为最小接入方式，后续可以演进到 web component 或 SDK 模式。
   */
  class AetherCoreWorkbenchLauncher {
    constructor(options = {}) {
      this.options = options;
      this.workbenchUrl = options.workbenchUrl || "http://127.0.0.1:5178";
      this.containerId = options.containerId || "aethercore-workbench-root";
    }

    ensureContainer() {
      let container = document.getElementById(this.containerId);
      if (container) return container;

      container = document.createElement("div");
      container.id = this.containerId;
      container.style.position = "fixed";
      container.style.inset = "24px";
      container.style.zIndex = "9999";
      container.style.background = "rgba(15, 23, 42, 0.12)";
      container.style.backdropFilter = "blur(8px)";
      container.style.borderRadius = "24px";
      container.style.overflow = "hidden";
      container.style.display = "none";
      document.body.appendChild(container);
      return container;
    }

    mount() {
      const container = this.ensureContainer();
      if (container.querySelector("iframe")) return container;

      const iframe = document.createElement("iframe");
      iframe.src = this.workbenchUrl;
      iframe.style.width = "100%";
      iframe.style.height = "100%";
      iframe.style.border = "none";
      iframe.title = "AetherCore Workbench";
      container.appendChild(iframe);
      return container;
    }

    open() {
      const container = this.mount();
      container.style.display = "block";
    }

    close() {
      const container = document.getElementById(this.containerId);
      if (container) {
        container.style.display = "none";
      }
    }
  }

  window.AetherCoreWorkbenchLauncher = AetherCoreWorkbenchLauncher;
})();
