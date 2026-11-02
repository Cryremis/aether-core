import { useParams } from "react-router-dom";

import { AuditBrowser } from "../../components/admin/AuditBrowser";
import { useAppPreferences } from "../../i18n";
import { TabPageShell } from "./TabPageShell";

export default function PlatformAuditPage() {
  const { t } = useAppPreferences();
  const { platformId: value } = useParams();
  const platformId = Number(value);
  if (!Number.isFinite(platformId) || platformId <= 0) return null;
  return (
    <TabPageShell title={t("platformDetail.audit")} description={t("platformDetail.auditHint")}>
      <AuditBrowser key={platformId} platformId={platformId} />
    </TabPageShell>
  );
}
