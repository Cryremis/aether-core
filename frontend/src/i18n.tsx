import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type AppLanguage = "zh-CN" | "en-US";
export type AppTheme = "light" | "dark" | "system";

const LANGUAGE_KEY = "aethercore_language";
const THEME_KEY = "aethercore_theme";

const messages = {
  "zh-CN": {
    "brand.name": "AetherCore",
    "nav.home": "首页",
    "nav.chat": "聊天",
    "nav.platforms": "平台",
    "nav.system": "系统管理",
    "nav.signIn": "登录",
    "nav.signOut": "退出",
    "nav.openChat": "进入聊天",
    "nav.openPlatforms": "管理平台",
    "theme.light": "亮色",
    "theme.dark": "暗色",
    "theme.system": "跟随系统",
    "language.zh": "中文",
    "language.en": "English",
    "auth.title": "登录 AetherCore",
    "auth.description": "登录后继续进入聊天工作台、平台治理和系统管理。",
    "home.hero.kicker": "把 AI Agent 带进真实业务系统",
    "home.hero.title": "AetherCore",
    "home.hero.subtitle": "一套面向企业级 AI 应用的运行、接入与治理平台：安全沙箱执行、平台级策略、资源基线、会话审计和多系统接入一次配齐。",
    "home.hero.primary": "体验 AI 工作台",
    "home.hero.secondary": "管理接入平台",
    "home.hero.metric1": "隔离沙箱",
    "home.hero.metric2": "统一接入",
    "home.hero.metric3": "可审计执行",
    "home.hero.signal": "实时治理看板",
    "home.hero.signalText": "每一次模型调用、工具执行、文件变更和 runtime 状态都能被追踪。",
    "home.section.platform.title": "把 Agent 嵌进任何业务平台",
    "home.section.platform.copy": "从内部系统、SaaS 后台到自研工具，AetherCore 提供统一接入脚本、后端绑定示例和平台级密钥，让业务系统快速拥有 AI 工作台。",
    "home.section.chat.title": "不仅会聊天，还能真实执行",
    "home.section.chat.copy": "每个会话都有独立 runtime、文件区、技能包和工作板。用户交代任务后，Agent 可以读写文件、运行命令、沉淀结果。",
    "home.section.access.title": "企业需要的控制力都在里面",
    "home.section.access.copy": "系统提示词、默认模型、联网策略、运行镜像、资源基线、负责人权限、注册审批和审计回放全部集中治理。",
    "home.capability.runtime": "安全运行时",
    "home.capability.runtimeDesc": "按会话创建隔离 runtime，支持镜像定制、生命周期追踪和一键回收。",
    "home.capability.audit": "审计回放",
    "home.capability.auditDesc": "完整还原用户消息、模型回复、工具调用和 runtime 状态。",
    "home.capability.baseline": "平台资源基线",
    "home.capability.baselineDesc": "为每个平台预置 skills、文件模板、工作目录和运行素材。",
    "home.capability.policy": "模型策略中心",
    "home.capability.policyDesc": "按平台配置默认模型、系统提示词、联网白名单和请求扩展参数。",
    "home.flow.1": "业务平台接入",
    "home.flow.2": "用户发起任务",
    "home.flow.3": "沙箱执行处理",
    "home.flow.4": "全程审计治理",
    "platforms.title": "平台",
    "platforms.subtitle": "选择一个平台进入治理视图。每个平台都拥有独立的接入、运行、资源和审计工作区。",
    "platforms.empty": "当前没有可管理的平台。",
    "platforms.open": "进入治理",
    "platforms.integration": "接入",
    "platforms.runtimeImage": "运行镜像",
    "platforms.owner": "负责人",
    "platforms.systemEntry": "系统管理",
    "platformDetail.back": "返回平台",
    "platformDetail.integration": "接入",
    "platformDetail.governance": "配置与资源",
    "platformDetail.runtime": "Runtime",
    "platformDetail.audit": "审计",
    "platformDetail.refresh": "刷新",
    "platformDetail.showHistory": "显示已关闭 runtime",
    "platformDetail.noRuntime": "当前平台没有 runtime 记录。",
    "platformDetail.noAudit": "当前平台还没有可审计会话。",
    "platformDetail.copy": "复制",
    "platformDetail.copied": "已复制",
    "system.title": "系统管理",
    "system.subtitle": "处理平台注册审批、用户授权和平台负责人治理。",
    "common.loading": "正在加载...",
    "common.retry": "重试",
  },
  "en-US": {
    "brand.name": "AetherCore",
    "nav.home": "Home",
    "nav.chat": "Chat",
    "nav.platforms": "Platforms",
    "nav.system": "System",
    "nav.signIn": "Sign in",
    "nav.signOut": "Sign out",
    "nav.openChat": "Open chat",
    "nav.openPlatforms": "Manage platforms",
    "theme.light": "Light",
    "theme.dark": "Dark",
    "theme.system": "System",
    "language.zh": "中文",
    "language.en": "English",
    "auth.title": "Sign in to AetherCore",
    "auth.description": "Continue to chat, platform governance, and system administration.",
    "home.hero.kicker": "Bring AI agents into real business systems",
    "home.hero.title": "AetherCore",
    "home.hero.subtitle": "An enterprise AI operations platform for sandboxed execution, platform integration, resource baselines, model policy, and auditable agent workflows.",
    "home.hero.primary": "Open AI workbench",
    "home.hero.secondary": "Manage platforms",
    "home.hero.metric1": "Isolated sandbox",
    "home.hero.metric2": "Unified integration",
    "home.hero.metric3": "Auditable execution",
    "home.hero.signal": "Live governance board",
    "home.hero.signalText": "Every model call, tool run, file change, and runtime state remains traceable.",
    "home.section.platform.title": "Embed agents into any business platform",
    "home.section.platform.copy": "AetherCore gives internal tools, SaaS dashboards, and custom systems a governed AI workbench with scripts, backend binding examples, and platform secrets.",
    "home.section.chat.title": "More than chat: real execution",
    "home.section.chat.copy": "Each conversation gets its own runtime, files, skills, and workboard so agents can run commands, edit artifacts, and deliver outcomes.",
    "home.section.access.title": "Enterprise control, built in",
    "home.section.access.copy": "Prompts, default models, network policy, runtime images, baselines, owners, approvals, and audit replay live in one control plane.",
    "home.capability.runtime": "Secure runtime",
    "home.capability.runtimeDesc": "Create isolated per-session runtimes with custom images, lifecycle tracking, and collection controls.",
    "home.capability.audit": "Audit replay",
    "home.capability.auditDesc": "Replay user messages, model responses, tool calls, and runtime state.",
    "home.capability.baseline": "Platform baseline",
    "home.capability.baselineDesc": "Preload skills, file templates, workspaces, and execution assets per platform.",
    "home.capability.policy": "Model policy center",
    "home.capability.policyDesc": "Configure default models, system prompts, network allowlists, and request extensions.",
    "home.flow.1": "Connect platform",
    "home.flow.2": "User starts task",
    "home.flow.3": "Sandbox executes",
    "home.flow.4": "Audit everything",
    "platforms.title": "Platforms",
    "platforms.subtitle": "Choose a platform to enter its governance workspace. Each platform has dedicated integration, runtime, resource, and audit views.",
    "platforms.empty": "No manageable platforms yet.",
    "platforms.open": "Open governance",
    "platforms.integration": "Integration",
    "platforms.runtimeImage": "Runtime image",
    "platforms.owner": "Owner",
    "platforms.systemEntry": "System administration",
    "platformDetail.back": "Back to platforms",
    "platformDetail.integration": "Integration",
    "platformDetail.governance": "Config and resources",
    "platformDetail.runtime": "Runtime",
    "platformDetail.audit": "Audit",
    "platformDetail.refresh": "Refresh",
    "platformDetail.showHistory": "Show closed runtimes",
    "platformDetail.noRuntime": "This platform has no runtime records.",
    "platformDetail.noAudit": "This platform has no auditable conversations yet.",
    "platformDetail.copy": "Copy",
    "platformDetail.copied": "Copied",
    "system.title": "System administration",
    "system.subtitle": "Review platform registrations, user access, and platform owner governance.",
    "common.loading": "Loading...",
    "common.retry": "Retry",
  },
} as const;

type MessageKey = keyof typeof messages["zh-CN"];

type PreferencesContextValue = {
  language: AppLanguage;
  setLanguage: (language: AppLanguage) => void;
  theme: AppTheme;
  setTheme: (theme: AppTheme) => void;
  resolvedTheme: "light" | "dark";
  t: (key: MessageKey) => string;
};

const PreferencesContext = createContext<PreferencesContextValue | null>(null);

function readLanguage(): AppLanguage {
  const stored = window.localStorage.getItem(LANGUAGE_KEY);
  if (stored === "zh-CN" || stored === "en-US") return stored;
  return navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
}

function readTheme(): AppTheme {
  const stored = window.localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "dark" || stored === "system") return stored;
  return "system";
}

function getSystemTheme(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function AppPreferencesProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<AppLanguage>(() => readLanguage());
  const [theme, setThemeState] = useState<AppTheme>(() => readTheme());
  const [systemTheme, setSystemTheme] = useState<"light" | "dark">(() => getSystemTheme());
  const resolvedTheme = theme === "system" ? systemTheme : theme;

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => setSystemTheme(media.matches ? "dark" : "light");
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.lang = language;
  }, [language, resolvedTheme]);

  const value = useMemo<PreferencesContextValue>(
    () => ({
      language,
      setLanguage: (nextLanguage) => {
        window.localStorage.setItem(LANGUAGE_KEY, nextLanguage);
        setLanguageState(nextLanguage);
      },
      theme,
      setTheme: (nextTheme) => {
        window.localStorage.setItem(THEME_KEY, nextTheme);
        setThemeState(nextTheme);
      },
      resolvedTheme,
      t: (key) => messages[language][key] ?? messages["zh-CN"][key] ?? key,
    }),
    [language, resolvedTheme, theme],
  );

  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function useAppPreferences() {
  const context = useContext(PreferencesContext);
  if (!context) {
    throw new Error("useAppPreferences must be used inside AppPreferencesProvider");
  }
  return context;
}
