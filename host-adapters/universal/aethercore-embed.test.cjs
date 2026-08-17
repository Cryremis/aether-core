const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

function createStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

function createFixture() {
  const visibleClasses = new Set();
  const preview = {
    classList: {
      add(value) {
        visibleClasses.add(value);
      },
      remove(value) {
        visibleClasses.delete(value);
      },
    },
  };
  const textElement = { textContent: "" };
  const frameWindow = {};
  const frame = { src: "https://agent.example/workbench", contentWindow: frameWindow };
  const elements = {
    ".ac-embed-preview": preview,
    ".ac-embed-preview__text": textElement,
    ".ac-embed-frame": frame,
  };
  const root = { querySelector: (selector) => elements[selector] || null, remove() {} };

  global.window = {
    crypto: global.crypto,
    localStorage: createStorage(),
    sessionStorage: createStorage(),
    location: { href: "https://host.example/page" },
    setTimeout,
    clearTimeout,
  };
  global.document = {
    activeElement: null,
    getElementById: () => root,
  };

  const adapterPath = path.join(__dirname, "aethercore-embed.js");
  delete require.cache[require.resolve(adapterPath)];
  require(adapterPath);
  return {
    AetherCoreEmbed: window.AetherCoreEmbed,
    frameWindow,
    textElement,
    visibleClasses,
  };
}

test("normalizes markdown and displays at most one current preview", () => {
  const fixture = createFixture();
  const instance = new fixture.AetherCoreEmbed({
    assistantPreview: { enabled: true, autoHideMs: 0 },
  });

  assert.equal(instance.showAssistantPreview("## 开始\n[查看](https://example.com) **任务**"), true);
  assert.equal(fixture.textElement.textContent, "开始 查看 任务");
  assert.equal(fixture.visibleClasses.has("is-visible"), true);

  instance.showAssistantPreview("最新正文");
  assert.equal(fixture.textElement.textContent, "最新正文");
  instance.destroy();
});

test("accepts preview events only from the mounted workbench frame", () => {
  const fixture = createFixture();
  const instance = new fixture.AetherCoreEmbed({ assistantPreview: { enabled: true } });

  assert.equal(
    instance.isWorkbenchMessage({ source: fixture.frameWindow, origin: "https://agent.example" }),
    true,
  );
  assert.equal(instance.isWorkbenchMessage({ source: {}, origin: "https://agent.example" }), false);
  assert.equal(
    instance.isWorkbenchMessage({ source: fixture.frameWindow, origin: "https://other.example" }),
    false,
  );
  instance.destroy();
});

test("ignores reasoning and tool events and accepts assistant正文 events", () => {
  const fixture = createFixture();
  const instance = new fixture.AetherCoreEmbed({ assistantPreview: { enabled: true } });
  const received = [];
  instance.isWorkbenchMessage = () => true;
  instance.queueAssistantPreview = (payload) => received.push(payload);

  instance.handleWorkbenchMessage({ data: { source: "aethercore-workbench", type: "reasoning_delta" } });
  instance.handleWorkbenchMessage({ data: { source: "aethercore-workbench", type: "tool_started" } });
  assert.deepEqual(received, []);

  instance.handleWorkbenchMessage({
    data: {
      source: "aethercore-workbench",
      type: "aethercore:assistant-preview",
      payload: {
        session_id: "session-1",
        message_id: "message-1",
        content_id: "content-1",
        content: "正在处理",
        status: "streaming",
      },
    },
  });
  assert.equal(received.length, 1);
  assert.equal(received[0].content, "正在处理");
  assert.equal(received[0].status, "streaming");
  instance.destroy();
});

test("uses the configured first-delay range and defers while editing", () => {
  const fixture = createFixture();
  const instance = new fixture.AetherCoreEmbed({
    assistantPreview: { enabled: true, proactive: { enabled: true } },
  });
  const delays = [];
  instance.scheduleProactivePreview = (delay) => delays.push(delay);

  const originalRandom = Math.random;
  Math.random = () => 0;
  try {
    instance.scheduleInitialProactivePreview();
    assert.equal(delays.shift(), 30000);

    document.activeElement = { tagName: "INPUT", isContentEditable: false };
    instance.tryShowProactivePreview();
    assert.equal(delays.shift(), 15000);
    assert.equal(instance.isProactiveSuppressed(), false);
  } finally {
    Math.random = originalRandom;
    instance.destroy();
  }
});

test("records one-to-four-hour cooldown after a proactive prompt", () => {
  const fixture = createFixture();
  const instance = new fixture.AetherCoreEmbed({
    assistantPreview: {
      enabled: true,
      autoHideMs: 0,
      proactive: { enabled: true, messages: ["来问我"] },
    },
  });
  const before = Date.now();
  const originalRandom = Math.random;
  Math.random = () => 0;
  try {
    instance.tryShowProactivePreview();
    assert.equal(instance.isProactiveSuppressed(), true);
    const nextAt = instance.getNextProactiveAt();
    assert.ok(nextAt >= before + 60 * 60 * 1000);
    assert.ok(nextAt <= Date.now() + 60 * 60 * 1000);
  } finally {
    Math.random = originalRandom;
    instance.destroy();
  }
});
