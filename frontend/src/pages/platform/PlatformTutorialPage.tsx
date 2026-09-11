import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { useAppPreferences } from "../../i18n";

import { getPlatformIntegrationGuide, PlatformIntegrationGuide } from "../../api/client";
import { IntegrationGuidePanel } from "../../components/admin/IntegrationGuideModal";
import { TabPageShell, usePlatformDetail } from "./TabPageShell";

export default function PlatformTutorialPage() {
  const { t } = useAppPreferences();
  const params = useParams();
  const { platform } = usePlatformDetail();
  const platformId = Number(params.platformId);
  const [guide, setGuide] = useState<PlatformIntegrationGuide | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!Number.isFinite(platformId) || platformId <= 0) return;
    void (async () => {
      try {
        setBusy(true);
        setError("");
        const result = await getPlatformIntegrationGuide(platformId);
        setGuide((result.data ?? null) as PlatformIntegrationGuide | null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载接入教程失败");
      } finally {
        setBusy(false);
      }
    })();
  }, [platformId]);

  const copyText = async (value: string) => {
    await navigator.clipboard.writeText(value);
  };

  const renderHighlightedSnippet = (snippet: string | undefined) => {
    if (!snippet) return null;
    const parts = snippet.split(/(\{\{[A-Z0-9_]+\}\})/g);
    return parts.map((part, index) =>
      /^\{\{[A-Z0-9_]+\}\}$/.test(part) ? (
        <span key={`placeholder-${index}`} className="guide-placeholder">{part}</span>
      ) : part,
    );
  };

  return (
    <TabPageShell title={t("platformDetail.tutorial")} description={t("platformDetail.tutorialHint")}>
      <IntegrationGuidePanel
        integrationGuide={guide}
        integrationGuideBusy={busy}
        integrationGuideError={error}
        integrationGuidePlatformName={platform?.display_name ?? ""}
        renderHighlightedSnippet={renderHighlightedSnippet}
        onCopy={(value) => void copyText(value)}
      />
    </TabPageShell>
  );
}
