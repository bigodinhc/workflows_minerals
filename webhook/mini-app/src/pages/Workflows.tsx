import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import { GlassCard } from "../components/GlassCard";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { formatRelativeTime, formatDuration } from "../lib/format";
import { apiFetch } from "../lib/api";
import type { WorkflowsResponse, Workflow, RunsResponse } from "../lib/types";

function conclusionToStatus(c: string | null): "success" | "error" | "warning" | "running" {
  if (c === "success") return "success";
  if (c === "failure") return "error";
  return "running";
}

function WorkflowRuns({ workflowId }: { workflowId: string }) {
  const { data, isLoading } = useApi<RunsResponse>(`/api/mini/workflows/${workflowId}/runs?limit=5`);
  if (isLoading) return <Skeleton className="h-20 w-full mt-3" />;
  if (!data?.runs.length) return <p className="text-xs text-text-muted mt-3">Sem execucoes recentes.</p>;
  return (
    <div className="mt-3 space-y-1.5">
      <span className="text-[9px] font-medium tracking-widest text-text-muted uppercase">Ultimas execucoes</span>
      {data.runs.map((run) => (
        <div key={run.id} className="flex items-center gap-2 text-xs">
          <StatusDot status={conclusionToStatus(run.conclusion)} size={6} />
          <span className="text-text-secondary">{formatRelativeTime(run.created_at)}</span>
          {run.conclusion === "success" && run.duration_seconds != null && (
            <span className="text-text-muted">{formatDuration(run.duration_seconds)}</span>
          )}
          {run.conclusion === "failure" && <span className="text-error text-[10px]">falhou</span>}
        </div>
      ))}
    </div>
  );
}

function WorkflowCard({ workflow, isExpanded, onToggle, onTrigger }: {
  workflow: Workflow; isExpanded: boolean; onToggle: () => void; onTrigger: () => void;
}) {
  return (
    <GlassCard className={`overflow-hidden ${
      workflow.last_run
        ? `border-l-2 ${workflow.last_run.conclusion === "success" ? "border-l-success/20" : workflow.last_run.conclusion === "failure" ? "border-l-error/20" : "border-l-warning/20"}`
        : ""
    }`}>
      <button onClick={onToggle} className="w-full p-4 text-left">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-icon bg-accent/10 flex items-center justify-center text-xl">{workflow.icon}</div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium">{workflow.name}</div>
            <div className="text-xs text-text-secondary">{workflow.description}</div>
          </div>
          <div className="text-right">
            <div className="text-sm font-medium">{workflow.health_pct}%</div>
            <div className="text-[10px] text-text-muted">
              {workflow.last_run ? formatRelativeTime(workflow.last_run.created_at) : "\u2014"}
            </div>
          </div>
        </div>
      </button>
      {isExpanded && (
        <div className="px-4 pb-4 border-t border-white/[0.04]">
          <WorkflowRuns workflowId={workflow.id} />
          <div className="flex gap-2 mt-4">
            <button onClick={onTrigger} className="flex-1 py-2.5 rounded-card bg-accent text-bg text-sm font-medium active:opacity-80">
              {"\u25B6"} Executar agora
            </button>
          </div>
        </div>
      )}
    </GlassCard>
  );
}

export default function Workflows() {
  const { data, isLoading, mutate } = useApi<WorkflowsResponse>("/api/mini/workflows");
  const { haptic, showPopup, initData } = useTelegram();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const handleToggle = (id: string) => {
    haptic?.impactOccurred("light");
    setExpandedId(expandedId === id ? null : id);
  };

  const handleTrigger = (workflow: Workflow) => {
    if (showPopup) {
      showPopup(
        { title: "Confirmar", message: `Executar ${workflow.name} agora?`,
          buttons: [{ id: "confirm", type: "default", text: "Executar" }, { id: "cancel", type: "cancel" }] },
        async (buttonId) => {
          if (buttonId !== "confirm") return;
          try {
            await apiFetch("/api/mini/trigger", initData, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ workflow_id: workflow.id }),
            });
            haptic?.notificationOccurred("success");
            mutate();
          } catch { haptic?.notificationOccurred("error"); }
        },
      );
    }
  };

  return (
    <div className="p-4 space-y-3">
      <h1 className="text-lg font-semibold">{"\u26A1"} Workflows</h1>
      {isLoading || !data ? (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : (
        data.workflows.map((wf) => (
          <WorkflowCard key={wf.id} workflow={wf} isExpanded={expandedId === wf.id}
            onToggle={() => handleToggle(wf.id)} onTrigger={() => handleTrigger(wf)} />
        ))
      )}
    </div>
  );
}
