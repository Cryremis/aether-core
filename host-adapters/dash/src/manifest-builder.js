// host-adapters/dash/src/manifest-builder.js
(function () {
  /**
   * 根据 Dash 当前页面与全局对象构建宿主注入清单。
   * 当前先保留空能力列表，后续逐步补充工具、技能与 API。
   */
  function buildDashHostManifest() {
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
        extras: {},
      },
      tools: [],
      skills: [],
      apis: [],
    };
  }

  window.buildDashHostManifest = buildDashHostManifest;
})();
