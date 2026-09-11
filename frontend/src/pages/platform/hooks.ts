import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

import {
  createPlatformBaselineDirectory, deletePlatformBaselineFile, downloadPlatformBaselineFile,
  getPlatformBaseline, getPlatformBaselineFileContent, getPlatformLlmConfig, getPlatformPromptConfig,
  getPlatformRuntimeImage, getPlatformRuntimeImageGuide, getPlatformSandboxProxyConfig,
  importPlatformBaselineFileTree, movePlatformBaselinePath, savePlatformBaselineTextFile,
  deletePlatformLlmConfig, deletePlatformPromptConfig, deletePlatformRuntimeImage,
  deletePlatformSandboxProxyConfig, updatePlatformLlmConfig, updatePlatformPromptConfig,
  updatePlatformRuntimeImage, updatePlatformSandboxProxyConfig, uploadPlatformBaselineFile,
  uploadPlatformBaselineSkill, uploadPlatformRuntimeImage,
} from "../../api/client";
import type {
  DirectoryCapableFile, LlmConfigFormState, PlatformBaselineEntryItem,
  PlatformRuntimeImageFormState, PlatformSandboxProxyFormState, PromptConfigFormState,
} from "../../components/admin/types";

/* ============ 平台 LLM 配置 ============ */

const EMPTY_LLM_FORM: LlmConfigFormState = {
  enabled: true, base_url: "", model: "", api_key: "", extra_headers_text: "", extra_body_text: "",
  has_api_key: false, network_enabled: true, allowed_domains_text: "", blocked_domains_text: "",
  max_search_results: 8, fetch_timeout_seconds: 30, sampling_temperature: "", sampling_frequency_penalty: "",
  sampling_presence_penalty: "", sampling_top_p: "", sampling_repetition_penalty: "",
};

type PlatformLlmConfigData = {
  enabled: boolean; base_url: string; model: string; has_api_key: boolean;
  extra_headers?: Record<string, string>; extra_body?: Record<string, unknown>;
  network?: { enabled?: boolean; allowed_domains?: string[]; blocked_domains?: string[]; max_search_results?: number; fetch_timeout_seconds?: number };
  sampling?: { temperature?: number | null; frequency_penalty?: number | null; presence_penalty?: number | null; top_p?: number | null; repetition_penalty?: number | null } | null;
};

function llmFormFromData(data: PlatformLlmConfigData | null): LlmConfigFormState {
  const s = data?.sampling;
  return {
    enabled: data?.enabled ?? true,
    base_url: data?.base_url ?? "",
    model: data?.model ?? "",
    api_key: "",
    extra_headers_text: data?.extra_headers && Object.keys(data.extra_headers).length > 0 ? JSON.stringify(data.extra_headers, null, 2) : "",
    extra_body_text: data?.extra_body && Object.keys(data.extra_body).length > 0 ? JSON.stringify(data.extra_body, null, 2) : "",
    has_api_key: Boolean(data?.has_api_key),
    network_enabled: data?.network?.enabled ?? true,
    allowed_domains_text: (data?.network?.allowed_domains ?? []).join("\n"),
    blocked_domains_text: (data?.network?.blocked_domains ?? []).join("\n"),
    max_search_results: data?.network?.max_search_results ?? 8,
    fetch_timeout_seconds: data?.network?.fetch_timeout_seconds ?? 30,
    sampling_temperature: s?.temperature != null ? String(s.temperature) : "",
    sampling_frequency_penalty: s?.frequency_penalty != null ? String(s.frequency_penalty) : "",
    sampling_presence_penalty: s?.presence_penalty != null ? String(s.presence_penalty) : "",
    sampling_top_p: s?.top_p != null ? String(s.top_p) : "",
    sampling_repetition_penalty: s?.repetition_penalty != null ? String(s.repetition_penalty) : "",
  };
}

function parseJsonObject(raw: string, label: string) {
  const trimmed = raw.trim();
  if (!trimmed) return {};
  try {
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 对象`);
    return parsed as Record<string, unknown>;
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : `${label}解析失败`);
  }
}

const parseLineList = (raw: string) => raw.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);

export function usePlatformLlmConfig(platformId: number | null, enabled = true) {
  const [form, setForm] = useState<LlmConfigFormState>(EMPTY_LLM_FORM);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setForm(EMPTY_LLM_FORM);
    setShowAdvanced(false);
    setError("");
    setLoaded(false);
  }, [platformId]);

  useEffect(() => {
    if (!platformId || !enabled || loaded) return;
    void (async () => {
      try {
        setError("");
        const result = await getPlatformLlmConfig(platformId);
        const data = (result.data ?? null) as PlatformLlmConfigData | null;
        setForm(llmFormFromData(data));
        setShowAdvanced(Boolean((data?.extra_headers && Object.keys(data.extra_headers).length > 0) || (data?.extra_body && Object.keys(data.extra_body).length > 0)));
        setLoaded(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载平台 LLM 配置失败");
      }
    })();
  }, [platformId, enabled, loaded]);

  const save = async () => {
    if (!platformId) return;
    try {
      setBusy(true);
      setError("");
      await updatePlatformLlmConfig(platformId, {
        enabled: form.enabled,
        base_url: form.base_url.trim(),
        model: form.model.trim(),
        api_key: form.api_key.trim() || undefined,
        extra_headers: parseJsonObject(form.extra_headers_text, "扩展请求头") as Record<string, string>,
        extra_body: parseJsonObject(form.extra_body_text, "扩展请求体"),
        network: {
          enabled: form.network_enabled,
          allowed_domains: parseLineList(form.allowed_domains_text),
          blocked_domains: parseLineList(form.blocked_domains_text),
          max_search_results: form.max_search_results,
          fetch_timeout_seconds: form.fetch_timeout_seconds,
        },
        sampling: {
          temperature: form.sampling_temperature.trim() ? Number(form.sampling_temperature) : null,
          frequency_penalty: form.sampling_frequency_penalty.trim() ? Number(form.sampling_frequency_penalty) : null,
          presence_penalty: form.sampling_presence_penalty.trim() ? Number(form.sampling_presence_penalty) : null,
          top_p: form.sampling_top_p.trim() ? Number(form.sampling_top_p) : null,
          repetition_penalty: form.sampling_repetition_penalty.trim() ? Number(form.sampling_repetition_penalty) : null,
        },
      });
      const latest = await getPlatformLlmConfig(platformId);
      setForm(llmFormFromData((latest.data ?? null) as PlatformLlmConfigData | null));
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存平台 LLM 配置失败");
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!platformId) return;
    if (!window.confirm("确定删除该平台的专属 LLM 配置并回退到全局默认吗？")) return;
    try {
      setBusy(true);
      setError("");
      await deletePlatformLlmConfig(platformId);
      setForm(EMPTY_LLM_FORM);
      setShowAdvanced(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除平台 LLM 配置失败");
    } finally {
      setBusy(false);
    }
  };

  return { form, setForm, error, busy, showAdvanced, setShowAdvanced, loaded, save, reset };
}

/* ============ 平台系统提示词 ============ */

const EMPTY_PROMPT_FORM: PromptConfigFormState = { enabled: true, system_prompt: "" };

export function usePlatformPromptConfig(platformId: number | null, enabled = true) {
  const [form, setForm] = useState<PromptConfigFormState>(EMPTY_PROMPT_FORM);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setForm(EMPTY_PROMPT_FORM);
    setError("");
    setLoaded(false);
  }, [platformId]);

  useEffect(() => {
    if (!platformId || !enabled || loaded) return;
    void (async () => {
      try {
        setError("");
        const result = await getPlatformPromptConfig(platformId);
        const data = (result.data ?? null) as { enabled: boolean; system_prompt: string } | null;
        setForm({ enabled: data?.enabled ?? true, system_prompt: data?.system_prompt ?? "" });
        setLoaded(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载平台提示词配置失败");
      }
    })();
  }, [platformId, enabled, loaded]);

  const save = async () => {
    if (!platformId) return;
    try {
      setBusy(true);
      setError("");
      await updatePlatformPromptConfig(platformId, { enabled: form.enabled, system_prompt: form.system_prompt });
      const latest = await getPlatformPromptConfig(platformId);
      const data = (latest.data ?? null) as { enabled: boolean; system_prompt: string } | null;
      setForm({ enabled: data?.enabled ?? true, system_prompt: data?.system_prompt ?? "" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存平台提示词配置失败");
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!platformId) return;
    if (!window.confirm("确定删除该平台的专属系统提示词配置吗？")) return;
    try {
      setBusy(true);
      setError("");
      await deletePlatformPromptConfig(platformId);
      setForm(EMPTY_PROMPT_FORM);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除平台提示词配置失败");
    } finally {
      setBusy(false);
    }
  };

  return { form, setForm, error, busy, loaded, save, reset };
}

/* ============ 平台运行镜像 ============ */

const EMPTY_IMAGE_FORM: PlatformRuntimeImageFormState = { image: "", resolvedImage: "", recycledRuntimeCount: null, guide: null };

type RuntimeImageData = { custom_image?: string | null; resolved_image?: string; recycled_runtime_count?: number };

export function usePlatformRuntimeImage(platformId: number | null, enabled = true, onMutated?: () => Promise<void> | void) {
  const [form, setForm] = useState<PlatformRuntimeImageFormState>(EMPTY_IMAGE_FORM);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const onMutatedRef = useRef(onMutated);
  onMutatedRef.current = onMutated;

  useEffect(() => {
    setForm(EMPTY_IMAGE_FORM);
    setError("");
    setLoaded(false);
  }, [platformId]);

  useEffect(() => {
    if (!platformId || !enabled || loaded) return;
    void (async () => {
      try {
        setError("");
        const [runtimeResult, guideResult] = await Promise.all([getPlatformRuntimeImage(platformId), getPlatformRuntimeImageGuide(platformId)]);
        const runtimeData = (runtimeResult.data ?? null) as RuntimeImageData | null;
        setForm({
          image: runtimeData?.custom_image ?? "",
          resolvedImage: runtimeData?.resolved_image ?? "",
          recycledRuntimeCount: runtimeData?.recycled_runtime_count ?? null,
          guide: (guideResult.data ?? null) as PlatformRuntimeImageFormState["guide"],
        });
        setLoaded(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载平台运行镜像失败");
      }
    })();
  }, [platformId, enabled, loaded]);

  const save = async () => {
    if (!platformId) return;
    try {
      setBusy(true);
      setError("");
      const result = await updatePlatformRuntimeImage(platformId, { image: form.image.trim() });
      const data = (result.data ?? {}) as RuntimeImageData;
      setForm({ image: data.custom_image ?? "", resolvedImage: data.resolved_image ?? "", recycledRuntimeCount: data.recycled_runtime_count ?? null, guide: form.guide });
      await onMutatedRef.current?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存平台运行镜像失败");
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!platformId) return;
    if (!window.confirm("确定清除该平台的专属运行镜像并回退到全局默认吗？")) return;
    try {
      setBusy(true);
      setError("");
      const result = await deletePlatformRuntimeImage(platformId);
      const data = (result.data ?? {}) as RuntimeImageData;
      setForm({ image: data.custom_image ?? "", resolvedImage: data.resolved_image ?? "", recycledRuntimeCount: data.recycled_runtime_count ?? null, guide: form.guide });
      await onMutatedRef.current?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "清除平台运行镜像失败");
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File | null) => {
    if (!platformId || !file) return;
    try {
      setBusy(true);
      setError("");
      const result = await uploadPlatformRuntimeImage(platformId, file);
      const data = (result.data ?? {}) as RuntimeImageData;
      setForm((current) => ({ ...current, image: data.custom_image ?? "", resolvedImage: data.resolved_image ?? "", recycledRuntimeCount: data.recycled_runtime_count ?? null }));
      await onMutatedRef.current?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传平台运行镜像失败");
    } finally {
      setBusy(false);
    }
  };

  return { form, setForm, error, busy, loaded, save, reset, upload };
}

/* ============ 平台 Sandbox 代理 ============ */

const EMPTY_PROXY_FORM: PlatformSandboxProxyFormState = {
  enabled: false, http_proxy: "", https_proxy: "", all_proxy: "", no_proxy: "", inherit_host_proxy: true, recycledRuntimeCount: null,
};

type ProxyData = { enabled?: boolean; http_proxy?: string; https_proxy?: string; all_proxy?: string; no_proxy?: string; inherit_host_proxy?: boolean; recycled_runtime_count?: number };

const proxyFormFromData = (data: ProxyData | null): PlatformSandboxProxyFormState => ({
  enabled: data?.enabled ?? false,
  http_proxy: data?.http_proxy ?? "",
  https_proxy: data?.https_proxy ?? "",
  all_proxy: data?.all_proxy ?? "",
  no_proxy: data?.no_proxy ?? "",
  inherit_host_proxy: data?.inherit_host_proxy ?? true,
  recycledRuntimeCount: data?.recycled_runtime_count ?? null,
});

export function usePlatformSandboxProxy(platformId: number | null, enabled = true, onMutated?: () => Promise<void> | void) {
  const [form, setForm] = useState<PlatformSandboxProxyFormState>(EMPTY_PROXY_FORM);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const onMutatedRef = useRef(onMutated);
  onMutatedRef.current = onMutated;

  useEffect(() => {
    setForm(EMPTY_PROXY_FORM);
    setError("");
    setLoaded(false);
  }, [platformId]);

  useEffect(() => {
    if (!platformId || !enabled || loaded) return;
    void (async () => {
      try {
        setError("");
        const result = await getPlatformSandboxProxyConfig(platformId);
        setForm(proxyFormFromData((result.data ?? null) as ProxyData | null));
        setLoaded(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载平台 sandbox 代理配置失败");
      }
    })();
  }, [platformId, enabled, loaded]);

  const save = async () => {
    if (!platformId) return;
    try {
      setBusy(true);
      setError("");
      const result = await updatePlatformSandboxProxyConfig(platformId, {
        enabled: form.enabled,
        http_proxy: form.http_proxy.trim(),
        https_proxy: form.https_proxy.trim(),
        all_proxy: form.all_proxy.trim(),
        no_proxy: form.no_proxy.trim(),
        inherit_host_proxy: form.inherit_host_proxy,
      });
      setForm(proxyFormFromData((result.data ?? null) as ProxyData | null));
      await onMutatedRef.current?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存平台 sandbox 代理配置失败");
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!platformId) return;
    if (!window.confirm("确定删除该平台的专属 sandbox 代理配置并回退到全局默认吗？")) return;
    try {
      setBusy(true);
      setError("");
      const result = await deletePlatformSandboxProxyConfig(platformId);
      setForm(proxyFormFromData((result.data ?? null) as ProxyData | null));
      await onMutatedRef.current?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "清除平台 sandbox 代理配置失败");
    } finally {
      setBusy(false);
    }
  };

  return { form, setForm, error, busy, loaded, save, reset };
}

/* ============ 平台基线资源（文件管理） ============ */

export type BaselineMoveState = { mode: "move" | "rename"; sourcePath: string; targetPath: string };

export function usePlatformBaseline(platformId: number | null) {
  const [entries, setEntries] = useState<PlatformBaselineEntryItem[]>([]);
  const [error, setError] = useState("");
  const [currentDirectory, setCurrentDirectory] = useState("");
  const [selectedPath, setSelectedPath] = useState("");
  const [selectedContent, setSelectedContent] = useState("");
  const [selectedMediaType, setSelectedMediaType] = useState("");
  const [selectedTruncated, setSelectedTruncated] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [skillUploadVisible, setSkillUploadVisible] = useState(false);
  const [skillUploadBusy, setSkillUploadBusy] = useState(false);
  const [skillUploadError, setSkillUploadError] = useState("");
  const [moveState, setMoveState] = useState<BaselineMoveState>({ mode: "move", sourcePath: "", targetPath: "" });
  const [moveBusy, setMoveBusy] = useState(false);
  const [moveError, setMoveError] = useState("");
  const [contextMenu, setContextMenu] = useState<{ visible: boolean; x: number; y: number; item: PlatformBaselineEntryItem | null }>({ visible: false, x: 0, y: 0, item: null });
  const fileManagerRef = useRef<HTMLDivElement>(null);
  const currentDirRef = useRef("");
  currentDirRef.current = currentDirectory;

  useEffect(() => {
    const handleClick = () => setContextMenu((prev) => ({ ...prev, visible: false }));
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, []);

  const load = useCallback(async (id: number) => {
    setError("");
    try {
      const result = await getPlatformBaseline(id);
      const data = (result.data ?? {}) as { entries?: PlatformBaselineEntryItem[] };
      setEntries(data.entries ?? []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载平台基线环境失败");
    }
  }, []);

  useEffect(() => {
    if (!platformId) { setEntries([]); return; }
    setCurrentDirectory("");
    setSelectedPath(""); setSelectedContent(""); setSelectedMediaType(""); setSelectedTruncated(false); setDirty(false);
    void load(platformId);
  }, [platformId, load]);

  // 已选文件被删除后自动关闭预览
  useEffect(() => {
    if (!selectedPath) return;
    if (!entries.some((item) => item.relative_path === selectedPath && item.kind === "file")) {
      setSelectedPath(""); setSelectedContent(""); setSelectedMediaType(""); setSelectedTruncated(false); setDirty(false);
    }
  }, [entries, selectedPath]);

  const getTargetUploadDir = () => currentDirRef.current || "work";

  const handleUploadFile = async (file?: File | null) => {
    if (!file || !platformId) return;
    try {
      setError("");
      await uploadPlatformBaselineFile(platformId, getTargetUploadDir(), file);
      await load(platformId);
    } catch (submitError) { setError(submitError instanceof Error ? submitError.message : "上传平台基线文件失败"); }
  };

  const handleUploadFolder = async (files: FileList | null) => {
    if (!files?.length || !platformId) return;
    const uploadItems = Array.from(files as unknown as DirectoryCapableFile[])
      .map((file) => {
        const relativePath = file.webkitRelativePath || file.name;
        return relativePath ? { file, relativePath } : null;
      })
      .filter((item): item is { file: File; relativePath: string } => item !== null);
    if (!uploadItems.length) { setError("未能从所选文件夹中解析出有效文件。"); return; }
    try {
      setError("");
      const result = await importPlatformBaselineFileTree(platformId, getTargetUploadDir(), uploadItems);
      const data = (result.data ?? null) as { imported_count?: number } | null;
      await load(platformId);
      if (data?.imported_count) setError(`已导入 ${data.imported_count} 个文件。`);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "导入平台基线文件夹失败");
    }
  };

  const handleSkillUpload = async (file: File) => {
    if (!platformId) return;
    try {
      setSkillUploadBusy(true);
      setSkillUploadError("");
      await uploadPlatformBaselineSkill(platformId, file);
      await load(platformId);
    } catch (submitError) {
      setSkillUploadError(submitError instanceof Error ? submitError.message : "上传技能失败");
    } finally {
      setSkillUploadBusy(false);
    }
  };

  const buildSkillArchiveFromFolder = async (files: FileList | null) => {
    if (!files?.length) return null;
    const entriesToZip = Array.from(files as unknown as DirectoryCapableFile[])
      .map((file) => {
        const relativePath = file.webkitRelativePath || file.name;
        return relativePath ? { file, relativePath } : null;
      })
      .filter((item): item is { file: File; relativePath: string } => item !== null);
    if (!entriesToZip.length) throw new Error("未能从所选技能文件夹中解析出文件。");
    const JSZipModule = await import("jszip");
    const zip = new JSZipModule.default();
    entriesToZip.forEach(({ file, relativePath }) => zip.file(relativePath, file));
    const rootName = entriesToZip[0]?.relativePath.split("/")[0] || "skill-package";
    const blob = await zip.generateAsync({ type: "blob", compression: "DEFLATE", compressionOptions: { level: 6 } });
    return new File([blob], `${rootName}.zip`, { type: "application/zip" });
  };

  const handleSkillFolderUpload = async (files: FileList | null) => {
    if (!platformId) return;
    try {
      setSkillUploadBusy(true);
      setSkillUploadError("");
      const archive = await buildSkillArchiveFromFolder(files);
      if (!archive) return;
      await uploadPlatformBaselineSkill(platformId, archive);
      await load(platformId);
    } catch (submitError) {
      setSkillUploadError(submitError instanceof Error ? submitError.message : "上传技能文件夹失败");
    } finally {
      setSkillUploadBusy(false);
    }
  };

  const handleDownload = async (fileRelativePath: string, fileName: string) => {
    if (!platformId) return;
    try {
      setError("");
      const blob = await downloadPlatformBaselineFile(platformId, fileRelativePath);
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = fileName;
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); window.URL.revokeObjectURL(url);
    } catch (err) { setError(err instanceof Error ? err.message : "下载失败"); }
  };

  const handleDelete = async (relativePath: string) => {
    if (!platformId) return;
    if (["skills", "work", "logs"].includes(relativePath)) { setError("根目录不允许删除。"); return; }
    if (!window.confirm(`确定要删除 ${relativePath} 吗？此操作不可恢复。`)) return;
    try {
      setError("");
      await deletePlatformBaselineFile(platformId, relativePath);
      if (selectedPath === relativePath || selectedPath.startsWith(`${relativePath}/`)) {
        setSelectedPath(""); setSelectedContent(""); setDirty(false);
      }
      await load(platformId);
    } catch (err) { setError(err instanceof Error ? err.message : "删除失败"); }
  };

  const buildRenameTargetPath = (sourcePath: string) => {
    const normalized = sourcePath.replace(/\\/g, "/");
    const segments = normalized.split("/");
    const currentName = segments.pop() ?? normalized;
    const parentPath = segments.join("/");
    return { currentName, parentPath, targetPath: parentPath ? `${parentPath}/${currentName}` : currentName };
  };

  const openMoveModal = (sourcePath: string, mode: "move" | "rename") => {
    if (["skills", "work", "logs"].includes(sourcePath)) {
      setError(mode === "rename" ? "根目录不允许重命名。" : "根目录不允许移动。");
      return;
    }
    const targetPath = mode === "rename" ? buildRenameTargetPath(sourcePath).targetPath : sourcePath;
    setMoveState({ mode, sourcePath, targetPath });
    setMoveError("");
  };

  const closeMoveModal = () => {
    setMoveState({ mode: "move", sourcePath: "", targetPath: "" });
    setMoveError("");
    setMoveBusy(false);
  };

  const handleMove = async () => {
    if (!platformId || !moveState.sourcePath) return;
    const targetPath = moveState.targetPath.trim();
    if (!targetPath || targetPath === moveState.sourcePath) { setMoveError("请输入新的目标路径。"); return; }
    try {
      setMoveBusy(true);
      setMoveError("");
      await movePlatformBaselinePath(platformId, moveState.sourcePath, targetPath);
      if (selectedPath === moveState.sourcePath) {
        setSelectedPath(targetPath);
      } else if (selectedPath.startsWith(`${moveState.sourcePath}/`)) {
        setSelectedPath(selectedPath.replace(moveState.sourcePath, targetPath));
      }
      await load(platformId);
      closeMoveModal();
    } catch (err) {
      setMoveError(err instanceof Error ? err.message : moveState.mode === "rename" ? "重命名失败" : "移动失败");
      setMoveBusy(false);
    }
  };

  const handleSaveText = async () => {
    if (!platformId || !selectedPath) return;
    try {
      setError("");
      await savePlatformBaselineTextFile(platformId, selectedPath, selectedContent);
      setDirty(false);
      await load(platformId);
    } catch (err) { setError(err instanceof Error ? err.message : "保存失败"); }
  };

  const handleCreateFile = async () => {
    if (!platformId) return;
    const targetDir = getTargetUploadDir();
    const filename = window.prompt(`输入新文件名 (当前目录：${targetDir})`, `new-file.txt`);
    if (!filename?.trim()) return;
    const fullPath = targetDir ? `${targetDir}/${filename.trim()}` : filename.trim();
    try {
      setError("");
      await savePlatformBaselineTextFile(platformId, fullPath, "");
      await load(platformId);
      void handleSelectFile({ name: filename.trim(), relative_path: fullPath, section: "work", kind: "file", size: 0, media_type: "text/plain" });
    } catch (err) { setError(err instanceof Error ? err.message : "创建文件失败"); }
  };

  const handleCreateDirectory = async () => {
    if (!platformId) return;
    const targetDir = getTargetUploadDir();
    const directoryName = window.prompt(`输入新目录名 (当前目录：${targetDir})`, `new-folder`);
    if (!directoryName?.trim()) return;
    const fullPath = targetDir ? `${targetDir}/${directoryName.trim()}` : directoryName.trim();
    try {
      setError("");
      await createPlatformBaselineDirectory(platformId, fullPath);
      await load(platformId);
    } catch (err) { setError(err instanceof Error ? err.message : "创建目录失败"); }
  };

  const handleSelectFile = async (item: PlatformBaselineEntryItem) => {
    if (!platformId || item.kind === "directory") return;
    try {
      setError("");
      const result = await getPlatformBaselineFileContent(platformId, item.relative_path);
      const data = (result.data ?? {}) as { content?: string; media_type?: string; truncated?: boolean };
      setSelectedPath(item.relative_path);
      setSelectedContent(data.content ?? "");
      setSelectedMediaType(data.media_type ?? item.media_type);
      setSelectedTruncated(Boolean(data.truncated));
      setDirty(false);
    } catch (err) { setError(err instanceof Error ? err.message : "读取文件失败"); }
  };

  const handleDoubleClickItem = (item: PlatformBaselineEntryItem) => {
    if (item.kind === "directory") {
      setCurrentDirectory(item.relative_path);
      setSelectedPath("");
      setDirty(false);
    } else {
      void handleSelectFile(item);
    }
  };

  const handleContextMenu = (event: ReactMouseEvent, item: PlatformBaselineEntryItem) => {
    event.preventDefault();
    event.stopPropagation();
    setContextMenu({ visible: true, x: event.clientX, y: event.clientY, item });
  };

  const handleContentChange = (value: string) => {
    setSelectedContent(value);
    setDirty(true);
  };

  return {
    entries, error, currentDirectory, selectedPath, selectedContent, selectedMediaType, selectedTruncated, dirty,
    fileManagerRef, skillUploadVisible, skillUploadBusy, skillUploadError, moveState, moveBusy, moveError, contextMenu,
    setCurrentDirectory, setSelectedPath, setDirty,
    setSkillUploadVisible, setSkillUploadError, setMoveState,
    handleUploadFile, handleUploadFolder, handleSkillUpload, handleSkillFolderUpload, handleDownload, handleDelete,
    openMoveModal, closeMoveModal, handleMove, handleSaveText, handleCreateFile, handleCreateDirectory,
    handleSelectFile, handleDoubleClickItem, handleContextMenu, handleContentChange,
  };
}
