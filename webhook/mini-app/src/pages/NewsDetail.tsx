import { useApi } from "../hooks/useApi";
import { GlassCard } from "../components/GlassCard";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { formatRelativeTime } from "../lib/format";
import type { NewsDetail as NewsDetailType } from "../lib/types";

const STATUS_MAP: Record<string, "success" | "error" | "warning"> = {
  archived: "success",
  pending: "warning",
  rejected: "error",
};

interface NewsDetailProps {
  itemId: string;
  onBack: () => void;
}

export default function NewsDetail({ itemId }: NewsDetailProps) {
  const { data, isLoading } = useApi<NewsDetailType>(`/api/mini/news/${itemId}`);

  if (isLoading || !data) {
    return (
      <div className="p-4 space-y-3">
        <Skeleton className="h-6 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <div>
        <div className="flex items-center gap-2 mb-2">
          <StatusDot status={STATUS_MAP[data.status] ?? "warning"} />
          <span className="text-[10px] text-text-muted uppercase">{data.status}</span>
          <span className="text-[10px] text-text-muted">{"\u00B7"}</span>
          <span className="text-[10px] text-text-muted">{formatRelativeTime(data.date)}</span>
        </div>
        <h1 className="text-lg font-semibold leading-tight">{data.title}</h1>
        {data.source && <p className="text-xs text-text-secondary mt-1">{data.source}</p>}
      </div>

      <GlassCard className="p-4">
        <div className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
          {data.fullText}
        </div>
      </GlassCard>

      {data.tables.length > 0 &&
        data.tables.map((table, ti) => (
          <GlassCard key={ti} className="p-3 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr>
                  {table.header.map((h, i) => (
                    <th key={i} className="text-left py-1 px-2 text-accent font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, ri) => (
                  <tr key={ri} className="border-t border-white/[0.04]">
                    {row.map((cell, ci) => (
                      <td key={ci} className="py-1 px-2 text-text-secondary">{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </GlassCard>
        ))}
    </div>
  );
}
