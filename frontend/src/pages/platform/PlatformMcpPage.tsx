import { useParams } from "react-router-dom";

import { PlatformMcpManager } from "../../components/admin/PlatformMcpManager";
import { TabPageShell } from "./TabPageShell";

export default function PlatformMcpPage() {
  const params = useParams();
  const platformId = Number(params.platformId);
  if (!Number.isFinite(platformId) || platformId <= 0) return null;

  return (
    <TabPageShell>
      <PlatformMcpManager platformId={platformId} />
    </TabPageShell>
  );
}
