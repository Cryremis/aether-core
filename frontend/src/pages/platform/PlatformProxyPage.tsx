import { useParams } from "react-router-dom";

import { PlatformSandboxProxyPanel } from "../../components/admin/PlatformSandboxProxyPanel";
import { useAppPreferences } from "../../i18n";
import { TabPageShell } from "./TabPageShell";
import { usePlatformSandboxProxy } from "./hooks";

export default function PlatformProxyPage() {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const proxy = usePlatformSandboxProxy(Number.isFinite(platformId) ? platformId : null);

  return (
    <TabPageShell>
      {!proxy.loaded ? <div className="platform-settings-loading">{t("common.loading")}</div> : null}
      {proxy.loaded ? (
        <PlatformSandboxProxyPanel
          sandboxProxyForm={proxy.form}
          sandboxProxyError={proxy.error}
          sandboxProxyBusy={proxy.busy}
          onChange={proxy.setForm}
          onSave={() => void proxy.save()}
          onReset={() => void proxy.reset()}
        />
      ) : null}
    </TabPageShell>
  );
}
