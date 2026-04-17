import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import { GlassCard } from "../components/GlassCard";
import { Skeleton } from "../components/Skeleton";
import type { ReportsResponse } from "../lib/types";

const REPORT_TYPES = ["Market Reports", "Research Reports"];

interface ReportsProps {
  onBack: () => void;
}

export default function Reports({ onBack: _ }: ReportsProps) {
  const [reportType, setReportType] = useState<string | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const { haptic } = useTelegram();

  let apiPath: string | null = null;
  if (reportType) {
    apiPath = `/api/mini/reports?type=${encodeURIComponent(reportType)}`;
    if (year) apiPath += `&year=${year}`;
  }

  const { data, isLoading } = useApi<ReportsResponse>(apiPath);

  const handleDownload = (downloadUrl: string) => {
    haptic?.impactOccurred("medium");
    window.open(downloadUrl, "_blank");
  };

  if (!reportType) {
    return (
      <div className="p-4 space-y-3">
        <h1 className="text-lg font-semibold">{"\uD83D\uDCCA"} Reports</h1>
        {REPORT_TYPES.map((type) => (
          <GlassCard key={type} className="p-4">
            <button
              onClick={() => {
                haptic?.impactOccurred("light");
                setReportType(type);
              }}
              className="w-full text-left"
            >
              <div className="text-sm font-medium text-text-primary">
                {type}
              </div>
              <div className="text-xs text-text-secondary mt-0.5">
                Platts {type}
              </div>
            </button>
          </GlassCard>
        ))}
      </div>
    );
  }

  const years = data?.reports
    ? [
        ...new Set(
          data.reports.map((r) => parseInt(r.date_key.slice(0, 4))),
        ),
      ].sort((a, b) => b - a)
    : [];

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-2 mb-1">
        <button
          onClick={() => {
            haptic?.impactOccurred("light");
            if (year) {
              setYear(null);
            } else {
              setReportType(null);
            }
          }}
          className="text-accent text-sm"
        >
          {"\u2190"} Voltar
        </button>
        <h1 className="text-lg font-semibold truncate">
          {reportType} {year ? `\u2014 ${year}` : ""}
        </h1>
      </div>

      {!year && years.length > 1 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {years.map((y) => (
            <button
              key={y}
              onClick={() => {
                haptic?.impactOccurred("light");
                setYear(y);
              }}
              className="px-3 py-1.5 rounded-chip text-xs bg-white/5 text-text-secondary border border-border"
            >
              {y}
            </button>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : !data?.reports.length ? (
        <p className="text-text-muted text-sm text-center py-8">
          Nenhum relatorio encontrado.
        </p>
      ) : (
        <GlassCard className="divide-y divide-white/[0.04]">
          {data.reports.map((report) => (
            <button
              key={report.id}
              onClick={() => handleDownload(report.download_url)}
              className="w-full flex items-center gap-3 p-3 text-left"
            >
              <span className="text-xl">{"\uD83D\uDCC4"}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-text-primary truncate">
                  {report.report_name}
                </div>
                <div className="text-[10px] text-text-muted">
                  {report.date_key}
                </div>
              </div>
              <span className="text-accent text-xs">{"\u2B07"}</span>
            </button>
          ))}
        </GlassCard>
      )}
    </div>
  );
}
