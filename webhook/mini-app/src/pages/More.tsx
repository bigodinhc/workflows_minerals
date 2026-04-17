import { MenuRow } from "../components/MenuRow";
import { useApi } from "../hooks/useApi";
import type { Stats } from "../lib/types";

interface MoreProps {
  onNavigate: (page: string) => void;
}

export default function More({ onNavigate }: MoreProps) {
  const { data: stats } = useApi<Stats>("/api/mini/stats");

  return (
    <div className="p-4 space-y-3">
      <h1 className="text-lg font-semibold mb-1">{"\u2022\u2022\u2022"} Mais</h1>
      <MenuRow
        icon={"\uD83D\uDCCA"}
        title="Reports"
        subtitle="PDFs Platts \u2014 Market & Research"
        onClick={() => onNavigate("reports")}
      />
      <MenuRow
        icon={"\uD83D\uDC65"}
        title="Contatos"
        subtitle={`${stats?.contacts_active ?? "..."} ativos \u00B7 gerenciar lista`}
        onClick={() => onNavigate("contacts")}
      />
      <MenuRow
        icon={"\u2699\uFE0F"}
        title="Settings"
        subtitle="Notificacoes e preferencias"
        onClick={() => onNavigate("settings")}
      />
    </div>
  );
}
