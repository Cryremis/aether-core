import { useParams } from "react-router-dom";

import { PlatformLlmPanel } from "../../components/admin/PlatformLlmPanel";
import { useAppPreferences } from "../../i18n";
import { TabPageShell } from "./TabPageShell";
import { usePlatformLlmConfig } from "./hooks";

export default function PlatformLlmPage() {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const llm = usePlatformLlmConfig(Number.isFinite(platformId) ? platformId : null);

  return (
    <TabPageShell>
      {!llm.loaded ? <div className="platform-settings-loading">{t("common.loading")}</div> : null}
      {llm.loaded ? (
        <PlatformLlmPanel
          platformLlmForm={llm.form}
          platformLlmError={llm.error}
          platformLlmBusy={llm.busy}
          showPlatformLlmAdvanced={llm.showAdvanced}
          onToggleAdvanced={llm.setShowAdvanced}
          onChange={llm.setForm}
          onSave={() => void llm.save()}
          onReset={() => void llm.reset()}
        />
      ) : null}
    </TabPageShell>
  );
}
