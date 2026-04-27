import type { PlatformIntegrationGuide } from "../../api/client";

export type PlatformItem = {
  platform_id: number;
  platform_key: string;
  display_name: string;
  host_type: string;
  description: string;
  owner_name: string;
  host_secret: string;
};

export type PlatformBaselineFileItem = {
  name: string;
  relative_path: string;
  section: "input" | "skills" | "work" | "output" | "logs";
  size: number;
  media_type: string;
};

export type PlatformBaselineEntryItem = {
  name: string;
  relative_path: string;
  section: "input" | "skills" | "work" | "output" | "logs";
  kind: "file" | "directory";
  size: number;
  media_type: string;
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

export type IntegrationGuideState = {
  integrationGuide: PlatformIntegrationGuide | null;
  integrationGuideBusy: boolean;
  integrationGuideError: string;
  integrationGuidePlatformName: string;
};
