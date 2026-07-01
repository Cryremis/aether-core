import type { PlatformIntegrationGuide, PlatformRuntimeImageGuide } from "../../api/client";

export type PlatformItem = {
  platform_id: number;
  platform_key: string;
  display_name: string;
  host_type: string;
  description: string;
  owner_name: string;
  host_secret: string;
  sandbox_image?: string | null;
  resolved_sandbox_image: string;
  sandbox_image_updated_at?: string | null;
  sandbox_proxy_enabled: boolean;
  sandbox_proxy_http: string;
  sandbox_proxy_https: string;
  sandbox_proxy_all: string;
  sandbox_proxy_no_proxy: string;
  sandbox_proxy_inherit_host_proxy: boolean;
  sandbox_proxy_updated_at?: string | null;
};

export type PlatformBaselineFileItem = {
  name: string;
  relative_path: string;
  section: "skills" | "work" | "logs";
  size: number;
  media_type: string;
};

export type PlatformBaselineEntryItem = {
  name: string;
  relative_path: string;
  section: "skills" | "work" | "logs";
  kind: "file" | "directory";
  size: number;
  media_type: string;
};

export type DirectoryCapableFile = File & {
  webkitRelativePath?: string;
};

export type LlmConfigFormState = {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key: string;
  extra_headers_text: string;
  extra_body_text: string;
  has_api_key: boolean;
  network_enabled: boolean;
  allowed_domains_text: string;
  blocked_domains_text: string;
  max_search_results: number;
  fetch_timeout_seconds: number;
};

export type PromptConfigFormState = {
  enabled: boolean;
  system_prompt: string;
};

export type PlatformRuntimeImageFormState = {
  image: string;
  resolvedImage: string;
  recycledRuntimeCount: number | null;
  guide: PlatformRuntimeImageGuide | null;
};

export type PlatformSandboxProxyFormState = {
  enabled: boolean;
  http_proxy: string;
  https_proxy: string;
  all_proxy: string;
  no_proxy: string;
  inherit_host_proxy: boolean;
  recycledRuntimeCount: number | null;
};

export type IntegrationGuideState = {
  integrationGuide: PlatformIntegrationGuide | null;
  integrationGuideBusy: boolean;
  integrationGuideError: string;
  integrationGuidePlatformName: string;
};
