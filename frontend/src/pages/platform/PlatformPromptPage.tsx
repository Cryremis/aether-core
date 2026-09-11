import { useParams } from "react-router-dom";

import { PlatformPromptPanel } from "../../components/admin/PlatformPromptPanel";
import { useAppPreferences } from "../../i18n";
import { TabPageShell } from "./TabPageShell";
import { usePlatformPromptConfig } from "./hooks";

export default function PlatformPromptPage() {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const prompt = usePlatformPromptConfig(Number.isFinite(platformId) ? platformId : null);

  return (
    <TabPageShell>
      {!prompt.loaded ? <div className="platform-settings-loading">{t("common.loading")}</div> : null}
      {prompt.loaded ? (
        <PlatformPromptPanel
          promptForm={prompt.form}
          promptError={prompt.error}
          promptBusy={prompt.busy}
          onChange={prompt.setForm}
          onSave={() => void prompt.save()}
          onReset={() => void prompt.reset()}
        />
      ) : null}
    </TabPageShell>
  );
}
