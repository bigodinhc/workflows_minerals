import { useState, useCallback } from "react";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import { GlassCard } from "../components/GlassCard";
import { Skeleton } from "../components/Skeleton";
import { apiFetch } from "../lib/api";
import type { ContactsResponse } from "../lib/types";

interface ContactsProps {
  onBack: () => void;
}

export default function Contacts({ onBack: _ }: ContactsProps) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const { haptic, initData } = useTelegram();

  const query = search ? `&search=${encodeURIComponent(search)}` : "";
  const { data, isLoading, mutate } = useApi<ContactsResponse>(
    `/api/mini/contacts?page=${page}${query}`,
  );

  const handleToggle = useCallback(
    async (phone: string) => {
      haptic?.impactOccurred("medium");
      try {
        await apiFetch(`/api/mini/contacts/${phone}/toggle`, initData, {
          method: "POST",
        });
        haptic?.notificationOccurred("success");
        mutate();
      } catch {
        haptic?.notificationOccurred("error");
      }
    },
    [haptic, initData, mutate],
  );

  const handleSearch = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setSearch(e.target.value);
      setPage(1);
    },
    [],
  );

  return (
    <div className="p-4 space-y-3">
      <h1 className="text-lg font-semibold">{"\uD83D\uDC65"} Contatos</h1>

      <input
        type="text"
        value={search}
        onChange={handleSearch}
        placeholder="Buscar contato..."
        className="w-full px-4 py-2.5 rounded-card bg-white/5 border border-border text-sm text-text-primary placeholder:text-text-muted outline-none focus:border-accent/30"
      />

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : !data?.contacts.length ? (
        <p className="text-text-muted text-sm text-center py-8">Nenhum contato encontrado.</p>
      ) : (
        <GlassCard className="divide-y divide-white/[0.04]">
          {data.contacts.map((contact) => (
            <div key={contact.phone} className="flex items-center gap-3 p-3">
              <div className="flex-1 min-w-0">
                <div className="text-sm text-text-primary">{contact.name}</div>
                <div className="text-[10px] text-text-muted">{contact.phone}</div>
              </div>
              <button
                onClick={() => handleToggle(contact.phone)}
                className={`w-10 h-6 rounded-full transition-colors relative ${
                  contact.active ? "bg-accent" : "bg-white/10"
                }`}
              >
                <div
                  className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                    contact.active ? "left-5" : "left-1"
                  }`}
                />
              </button>
            </div>
          ))}
        </GlassCard>
      )}

      {data && data.total > 20 && (
        <div className="flex justify-center gap-4 pt-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="text-sm text-accent disabled:text-text-muted"
          >
            {"\u2190"} Anterior
          </button>
          <span className="text-sm text-text-muted">Pag {page}</span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={data.contacts.length < 20}
            className="text-sm text-accent disabled:text-text-muted"
          >
            Proxima {"\u2192"}
          </button>
        </div>
      )}
    </div>
  );
}
