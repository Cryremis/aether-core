import { useParams } from "react-router-dom";

import { PlatformRuntimeImagePanel } from "../../components/admin/PlatformRuntimeImagePanel";
import { useAppPreferences } from "../../i18n";
import { TabPageShell, usePlatformDetail } from "./TabPageShell";
import { usePlatformRuntimeImage } from "./hooks";

export default function PlatformImagePage() {
  const { t } = useAppPreferences();
  const params = useParams();
  const platformId = Number(params.platformId);
  const { platform } = usePlatformDetail();
  const image = usePlatformRuntimeImage(Number.isFinite(platformId) ? platformId : null);

  return (
    <TabPageShell>
      {!image.loaded ? <div className="platform-settings-loading">{t("common.loading")}</div> : null}
      {image.loaded ? (
        <PlatformRuntimeImagePanel
          platformName={platform?.display_name ?? ""}
          runtimeImageForm={image.form}
          runtimeImageError={image.error}
          runtimeImageBusy={image.busy}
          onChange={image.setForm}
          onSave={() => void image.save()}
          onReset={() => void image.reset()}
          onUpload={(file) => void image.upload(file)}
        />
      ) : null}
    </TabPageShell>
  );
}
