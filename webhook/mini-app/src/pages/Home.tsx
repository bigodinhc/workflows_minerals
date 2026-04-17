import { useApi } from "../hooks/useApi";
import { GlassCard } from "../components/GlassCard";
import { RingChart } from "../components/RingChart";
import { Sparkline } from "../components/Sparkline";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { formatRelativeTime } from "../lib/format";
import type { Stats, WorkflowsResponse, Workflow } from "../lib/types";

function conclusionToStatus(c: string | null): "success" | "error" | "warning" | "running" {
  if (c === "success") return "success";
  if (c === "failure") return "error";
  return "running";
}

function StatItem({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-center">
      <div className="text-lg font-semibold text-text-primary">{value}</div>
      <div className="text-[9px] text-text-muted uppercase tracking-wide">{label}</div>
    </div>
  );
}

function HeroCard({ stats }: { stats: Stats }) {
  return (
    <div className="relative rounded-hero p-6 glass border border-accent/10 overflow-hidden">
      <div className="absolute -top-20 -right-20 w-40 h-40 rounded-full bg-accent/[0.04] blur-3xl" />
      <div className="absolute -bottom-10 -left-10 w-32 h-32 rounded-full bg-accent/[0.03] blur-2xl" />
      <div className="relative">
        <span className="text-[9px] font-medium tracking-widest text-accent uppercase">System Health</span>
        <div className="flex items-center justify-between mt-2">
          <span className="text-4xl font-bold text-text-primary">{stats.health_pct}%</span>
          <RingChart value={stats.workflows_ok} total={stats.workflows_total} size={48} strokeWidth={4} />
        </div>
        <div className="flex justify-between mt-4 pt-4 border-t border-white/[0.06]">
          <StatItem label="Runs" value={stats.runs_today} />
          <StatItem label="Contatos" value={stats.contacts_active} />
          <StatItem label="News" value={stats.news_today} />
        </div>
      </div>
    </div>
  );
}

function WorkflowRow({ workflow }: { workflow: Workflow }) {
  const sparkData = workflow.recent_runs.map((r) => (r.conclusion === "success" ? 1 : 0));
  const status = conclusionToStatus(workflow.last_run?.conclusion ?? null);
  return (
    <div className="flex items-center gap-3 p-3">
      <div className="w-9 h-9 rounded-icon bg-accent/10 flex items-center justify-center text-lg">{workflow.icon}</div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-text-primary truncate">{workflow.name}</div>
        <div className="text-[10px] text-text-muted">
          {workflow.last_run ? formatRelativeTime(workflow.last_run.created_at) : "Nunca"}
        </div>
      </div>
      <Sparkline data={sparkData.length >= 2 ? sparkData : [0, 0]} />
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-text-secondary">{workflow.health_pct}%</span>
        <StatusDot status={status} />
      </div>
    </div>
  );
}

interface HomeProps {
  onNavigate: (page: string, params?: Record<string, string>) => void;
}

export default function Home({ onNavigate }: HomeProps) {
  const { data: stats, isLoading: statsLoading } = useApi<Stats>("/api/mini/stats");
  const { data: wfData, isLoading: wfLoading } = useApi<WorkflowsResponse>("/api/mini/workflows");

  return (
    <div className="p-4 space-y-4">
      {statsLoading || !stats ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <HeroCard stats={stats} />
      )}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[9px] font-medium tracking-widest text-text-muted uppercase">Workflows</span>
          <button onClick={() => onNavigate("workflows-tab")} className="text-[11px] text-accent">
            Ver todos &rarr;
          </button>
        </div>
        <GlassCard>
          {wfLoading || !wfData ? (
            <div className="space-y-3 p-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : (
            <div className="divide-y divide-white/[0.04]">
              {wfData.workflows.map((wf) => (
                <WorkflowRow key={wf.id} workflow={wf} />
              ))}
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
