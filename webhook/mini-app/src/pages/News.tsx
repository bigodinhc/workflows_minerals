import { useState, useCallback } from "react";
import useSWRInfinite from "swr/infinite";
import { useTelegram } from "../hooks/useTelegram";
import { useInfiniteScroll } from "../hooks/useInfiniteScroll";
import { FilterChips } from "../components/FilterChips";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { GlassCard } from "../components/GlassCard";
import { formatRelativeTime } from "../lib/format";
import { apiFetch } from "../lib/api";
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

const PAGE_SIZE = 20;

interface NewsProps {
  onItemClick: (id: string) => void;
}

export default function News({ onItemClick }: NewsProps) {
  const [status, setStatus] = useState("all");
  const { initData, haptic } = useTelegram();

  const getKey = useCallback(
    (pageIndex: number, prev: NewsResponse | null) => {
      if (prev && prev.items.length === 0) return null;
      if (!initData) return null;
      return `/api/mini/news?status=${status}&page=${pageIndex + 1}&limit=${PAGE_SIZE}`;
    },
    [status, initData],
  );

  const { data, size, setSize, isLoading, isValidating } = useSWRInfinite<NewsResponse>(
    getKey,
    (url: string) => apiFetch<NewsResponse>(url, initData),
    { revalidateOnFocus: false },
  );

  const items = data?.flatMap((page) => page.items) ?? [];
  const total = data?.[0]?.total ?? 0;
  const hasMore = items.length < total;

  const sentinelRef = useInfiniteScroll(
    () => setSize(size + 1),
    hasMore,
    isLoading || isValidating,
  );

  const handleFilterChange = (id: string) => {
    haptic?.impactOccurred("light");
    setStatus(id);
  };

  return (
    <div className="p-4">
      <h1 className="text-lg font-semibold mb-3">{"\uD83D\uDCF0"} News</h1>
      <FilterChips options={FILTER_OPTIONS} active={status} onChange={handleFilterChange} />
      <div className="mt-3">
        {isLoading && items.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : items.length === 0 ? (
          <p className="text-text-muted text-sm text-center py-8">Nenhum item encontrado.</p>
        ) : (
          <GlassCard className="divide-y divide-white/[0.02] px-3">
            {items.map((item, i) => (
              <div key={item.id} ref={i === items.length - 1 ? sentinelRef : undefined}>
                <NewsRow item={item} onClick={() => onItemClick(item.id)} />
              </div>
            ))}
          </GlassCard>
        )}
        {isValidating && items.length > 0 && <Skeleton className="h-12 w-full mt-2" />}
      </div>
    </div>
  );
}
