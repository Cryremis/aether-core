// host-adapters/dash/src/index.js
(function () {
  /**
   * Dash 宿主统一入口。
   * 调用后会准备宿主桥接器与工作台挂载器，方便后续直接在页面中接入。
   */
  function createAetherCoreDashIntegration(options = {}) {
    if (!window.AetherCoreDashHost) {
      throw new Error("AetherCoreDashHost 未加载");
    }
    if (!window.AetherCoreWorkbenchLauncher) {
      throw new Error("AetherCoreWorkbenchLauncher 未加载");
    }

    const host = new window.AetherCoreDashHost(options);
    const launcher = new window.AetherCoreWorkbenchLauncher(options);

    return {
      host,
      launcher,
      async initialize() {
        return host.bind();
      },
      openWorkbench() {
        launcher.open();
      },
      closeWorkbench() {
        launcher.close();
      },
    };
  }

  window.createAetherCoreDashIntegration = createAetherCoreDashIntegration;
})();
