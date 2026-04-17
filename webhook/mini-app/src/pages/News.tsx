import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import { FilterChips } from "../components/FilterChips";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { GlassCard } from "../components/GlassCard";
import { formatRelativeTime } from "../lib/format";
import type { NewsItem, NewsResponse } from "../lib/types";

const FILTER_OPTIONS = [
  { id: "all", label: "Todos" },
  { id: "pending", label: "Pendentes" },
  { id: "archived", label: "Arquivados" },
  { id: "rejected", label: "Recusados" },
];

const STATUS_MAP: Record<string, "success" | "error" | "warning"> = {
  archived: "success",
  pending: "warning",
  rejected: "error",
};

function NewsRow({ item, onClick }: { item: NewsItem; onClick: () => void }) {
  return (
    <button onClick={onClick} className="w-full flex items-center gap-3 py-3 px-1 text-left">
      <StatusDot status={STATUS_MAP[item.status] ?? "warning"} />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-text-primary truncate">{item.title}</div>
      </div>
      <span className="text-[10px] text-text-muted whitespace-nowrap ml-2">
        {formatRelativeTime(item.date)}
      </span>
    </button>
  );
}

function QueueBanner({
  onGoToQueue,
}: {
  onGoToQueue: () => void;
}) {
  const { data } = useApi<NewsResponse>(
    "/api/mini/news?status=pending&page=1&limit=1",
  );
  const count = data?.total ?? 0;

  if (count === 0) return null;

  return (
    <button
      onClick={onGoToQueue}
      className="w-full flex items-center gap-3 p-3 mb-3 glass rounded-card border border-warning/20 text-left"
    >
      <span className="text-xl">{"\uD83D\uDCE8"}</span>
      <div className="flex-1">
        <span className="text-sm font-medium text-text-primary">Fila de curadoria</span>
        <span className="text-xs text-text-secondary ml-2">{count} aguardando</span>
      </div>
      <span className="px-2 py-0.5 rounded-chip bg-warning/20 text-warning text-xs font-medium">
        {count}
      </span>
    </button>
  );
}

const PAGE_SIZE = 20;

interface NewsProps {
  onItemClick: (id: string) => void;
}

export default function News({ onItemClick }: NewsProps) {
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const { haptic } = useTelegram();

  const { data, isLoading, error } = useApi<NewsResponse>(
    `/api/mini/news?status=${status}&page=${page}&limit=${PAGE_SIZE}`,
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasMore = page * PAGE_SIZE < total;

  const handleFilterChange = (id: string) => {
    haptic?.impactOccurred("light");
    setStatus(id);
    setPage(1);
  };

  const handleGoToQueue = () => {
    haptic?.impactOccurred("light");
    setStatus("pending");
    setPage(1);
  };

  return (
    <div className="p-4">
      <h1 className="text-lg font-semibold mb-3">{"\uD83D\uDCF0"} News</h1>

      {status !== "pending" && <QueueBanner onGoToQueue={handleGoToQueue} />}

      <FilterChips options={FILTER_OPTIONS} active={status} onChange={handleFilterChange} />
      <div className="mt-3">
        {isLoading && items.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : error ? (
          <p className="text-error text-sm text-center py-8">Erro ao carregar noticias.</p>
        ) : items.length === 0 ? (
          <p className="text-text-muted text-sm text-center py-8">Nenhum item encontrado.</p>
        ) : (
          <GlassCard className="divide-y divide-white/[0.02] px-3">
            {items.map((item) => (
              <NewsRow key={item.id} item={item} onClick={() => onItemClick(item.id)} />
            ))}
          </GlassCard>
        )}

        {total > PAGE_SIZE && (
          <div className="flex justify-center items-center gap-4 pt-3">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="text-sm text-accent disabled:text-text-muted"
            >
              {"\u2190"} Anterior
            </button>
            <span className="text-xs text-text-muted">
              {page} / {Math.ceil(total / PAGE_SIZE)}
            </span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasMore}
              className="text-sm text-accent disabled:text-text-muted"
            >
              Proxima {"\u2192"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
