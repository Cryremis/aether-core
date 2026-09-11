import type { PlatformItem } from "../../components/admin/types";

/* 平台详情布局通过 <Outlet context> 注入给各 tab 页 */
export type PlatformDetailContext = {
  platform: PlatformItem | null;
  loading: boolean;
};
