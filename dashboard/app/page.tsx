"use client";

import { Button } from "@/components/ui/button";
import { Play, CheckCircle2, XCircle, Loader2, FileText, AlertTriangle } from "lucide-react";
import useSWR from "swr";
import { formatDistanceToNow } from "date-fns";
import { ptBR } from "date-fns/locale";
import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";

const fetcher = (url: string) => fetch(url).then((res) => res.json());

const WORKFLOWS = [
  { id: "morning_check.yml", name: "MORNING CHECK", tag: "PLATTS", type: "Morning" },
  { id: "baltic_ingestion.yml", name: "BALTIC INGESTION", tag: "EMAIL", type: "Mid-Day" },
  { id: "daily_report.yml", name: "DAILY REPORT", tag: "SGX", type: "Afternoon" },
  { id: "rationale_news.yml", name: "RATIONALE NEWS", tag: "TELEGRAM", type: "On-Demand" },
  { id: "market_news.yml", name: "MARKET NEWS", tag: "MARKET", type: "4x Daily" }
];

export default function Home() {
  const { data: runs, error, mutate } = useSWR("/api/workflows", fetcher, { refreshInterval: 10000 });
  const [triggeringId, setTriggeringId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [logContent, setLogContent] = useState<string | null>(null);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);

  const handleTrigger = async (workflowId: string) => {
    setTriggeringId(workflowId);
    try {
      await fetch("/api/workflows", {
        method: "POST",
        body: JSON.stringify({ workflow_id: workflowId }),
        headers: { "Content-Type": "application/json" }
      });
      setTimeout(() => mutate(), 2000);
    } catch (e) {
      console.error(e);
    } finally {
      setTriggeringId(null);
    }
  };

  const handleViewLogs = async (runId: number) => {
    setSelectedRunId(runId);
    setIsLoadingLogs(true);
    setLogContent(null);

    try {
      const res = await fetch(`/api/logs?run_id=${runId}`);
      const text = await res.text();
      setLogContent(text);
    } catch (e) {
      setLogContent("Failed to load logs.");
    } finally {
      setIsLoadingLogs(false);
    }
  };

  const lastRun = runs?.[0];
  const isOnline = !error;
  const lastSuccess = runs?.find((r: any) => r.conclusion === "success");
  const failedToday = runs?.filter((r: any) => {
    if (!r.created_at) return false;
    const isToday = new Date(r.created_at).toDateString() === new Date().toDateString();
    return isToday && r.conclusion === "failure";
  }).length || 0;
  const successToday = runs?.filter((r: any) => {
    if (!r.created_at) return false;
    const isToday = new Date(r.created_at).toDateString() === new Date().toDateString();
    return isToday && r.conclusion === "success";
  }).length || 0;

  return (
    <div className="p-4 md:p-6 space-y-6 bg-black text-[#e0e0e0] min-h-screen">
      {/* Header */}
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[10px] text-[#00FF41] uppercase tracking-[0.3em] mb-1">/ SYSTEM OVERVIEW</p>
          <h1 className="text-xl md:text-2xl font-bold uppercase tracking-tight text-white cursor-blink">
            MINERALS TRADING
          </h1>
          <p className="text-xs text-[#555] mt-1 uppercase">WORKFLOW AUTOMATION DASHBOARD</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {WORKFLOWS.map(wf => (
            <button
              key={wf.id}
              onClick={() => handleTrigger(wf.id)}
              disabled={triggeringId === wf.id}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-[10px] uppercase tracking-wider font-medium
                border border-[#00FF41]/30 bg-transparent text-[#00FF41] 
                hover:bg-[#00FF41]/10 hover:border-[#00FF41]/60 
                disabled:opacity-50 transition-all duration-150`}
            >
              {triggeringId === wf.id ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Play className="h-3 w-3" />
              )}
              {wf.tag}
            </button>
          ))}
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid gap-3 md:gap-4 grid-cols-2 lg:grid-cols-4">
        {/* System Status */}
        <div className="border border-[#1a1a1a] bg-[#0a0a0a] p-4 neon-border">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] text-[#555] uppercase tracking-wider">STATUS</span>
            {isOnline && (
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full bg-[#00FF41] opacity-75"></span>
                <span className="relative inline-flex h-2 w-2 bg-[#00FF41] pulse-neon"></span>
              </span>
            )}
          </div>
          <div className={`text-lg font-bold uppercase ${isOnline ? "text-[#00FF41] neon-glow" : "text-[#ff3333]"}`}>
            {isOnline ? "ONLINE" : "OFFLINE"}
          </div>
          <p className="text-[10px] text-[#333] mt-1 uppercase">GITHUB API CONNECTED</p>
        </div>

        {/* Last Run */}
        <div className="border border-[#1a1a1a] bg-[#0a0a0a] p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] text-[#555] uppercase tracking-wider">LAST RUN</span>
            {lastRun?.conclusion === "success" ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-[#00FF41]" />
            ) : (
              <XCircle className="h-3.5 w-3.5 text-[#ff3333]" />
            )}
          </div>
          <div className="text-lg font-bold uppercase text-white">
            {lastRun?.conclusion || lastRun?.status || "..."}
          </div>
          <p className="text-[10px] text-[#333] mt-1">
            {lastRun ? formatDistanceToNow(new Date(lastRun.created_at), { addSuffix: true, locale: ptBR }) : "—"}
          </p>
        </div>

        {/* Success Today */}
        <div className="border border-[#1a1a1a] bg-[#0a0a0a] p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] text-[#555] uppercase tracking-wider">SUCCESS TODAY</span>
            <CheckCircle2 className="h-3.5 w-3.5 text-[#00FF41]/50" />
          </div>
          <div className="text-lg font-bold text-[#00FF41]">{successToday}</div>
          <p className="text-[10px] text-[#333] mt-1 uppercase">COMPLETED OK</p>
        </div>

        {/* Failures Today */}
        <div className={`border bg-[#0a0a0a] p-4 ${failedToday > 0 ? "border-[#ff3333]/30" : "border-[#1a1a1a]"}`}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] text-[#555] uppercase tracking-wider">FAILURES</span>
            <AlertTriangle className={`h-3.5 w-3.5 ${failedToday > 0 ? "text-[#ff3333]" : "text-[#333]"}`} />
          </div>
          <div className={`text-lg font-bold ${failedToday > 0 ? "text-[#ff3333]" : "text-[#555]"}`}>{failedToday}</div>
          <p className="text-[10px] text-[#333] mt-1 uppercase">
            {failedToday === 0 ? "ALL CLEAR" : "CHECK LOGS"}
          </p>
        </div>
      </div>

      {/* Execution Log */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <p className="text-[10px] text-[#00FF41] uppercase tracking-[0.3em]">/ EXECUTION LOG</p>
          <div className="flex-1 h-px bg-[#1a1a1a]"></div>
        </div>

        <div className="border border-[#1a1a1a] bg-[#0a0a0a] overflow-hidden">
          {!runs ? (
            <div className="p-8 text-center text-[#555] text-xs uppercase">
              <Loader2 className="h-5 w-5 animate-spin mx-auto mb-2 text-[#00FF41]" />
              LOADING DATA...
            </div>
          ) : (
            <div className="w-full">
              {/* Desktop header */}
              <div className="hidden md:grid grid-cols-5 gap-4 px-4 py-2 text-[10px] font-medium text-[#555] uppercase tracking-wider border-b border-[#1a1a1a] bg-[#050505]">
                <div className="col-span-2">WORKFLOW</div>
                <div>STATUS</div>
                <div>TIMESTAMP</div>
                <div className="text-right">ACTION</div>
              </div>
              {/* Mobile header */}
              <div className="md:hidden grid grid-cols-3 gap-2 px-3 py-2 text-[9px] font-medium text-[#555] uppercase tracking-wider border-b border-[#1a1a1a] bg-[#050505]">
                <div>WORKFLOW</div>
                <div>STATUS</div>
                <div className="text-right">LOG</div>
              </div>

              {runs.map((run: any) => (
                <div key={run.id}>
                  {/* Desktop row */}
                  <div className="hidden md:grid grid-cols-5 gap-4 px-4 py-3 text-xs items-center hover:bg-[#00FF41]/5 transition-colors border-b border-[#0a0a0a] last:border-0">
                    <div className="col-span-2 truncate">
                      <span className="text-white font-medium">{run.name}</span>
                      <span className="text-[#333] ml-2">#{run.run_number}</span>
                    </div>
                    <div>
                      <span className={`inline-flex items-center gap-1.5 text-[10px] uppercase tracking-wider font-medium
                        ${run.conclusion === 'success' ? 'text-[#00FF41]' :
                          run.conclusion === 'failure' ? 'text-[#ff3333]' :
                            'text-[#FFD700]'}`}>
                        <span className={`inline-block w-1.5 h-1.5 
                          ${run.conclusion === 'success' ? 'bg-[#00FF41]' :
                            run.conclusion === 'failure' ? 'bg-[#ff3333]' :
                              'bg-[#FFD700]'}`}></span>
                        {run.status === 'completed' ? run.conclusion : run.status}
                      </span>
                    </div>
                    <div className="text-[#555] text-[11px]">
                      {new Date(run.created_at).toLocaleString('pt-BR')}
                    </div>
                    <div className="text-right">
                      <button
                        onClick={() => handleViewLogs(run.id)}
                        className="text-[#00FF41]/60 hover:text-[#00FF41] text-[10px] uppercase tracking-wider transition-colors"
                      >
                        [VIEW LOG]
                      </button>
                    </div>
                  </div>
                  {/* Mobile row */}
                  <div className="md:hidden grid grid-cols-3 gap-2 px-3 py-2.5 text-[11px] items-center border-b border-[#0a0a0a] last:border-0">
                    <div className="truncate">
                      <div className="font-medium text-white truncate text-[10px]">{run.name}</div>
                      <div className="text-[#333] text-[9px]">
                        {new Date(run.created_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                      </div>
                    </div>
                    <div>
                      <span className={`inline-flex items-center gap-1 text-[9px] uppercase font-medium
                        ${run.conclusion === 'success' ? 'text-[#00FF41]' :
                          run.conclusion === 'failure' ? 'text-[#ff3333]' :
                            'text-[#FFD700]'}`}>
                        <span className={`inline-block w-1 h-1 
                          ${run.conclusion === 'success' ? 'bg-[#00FF41]' :
                            run.conclusion === 'failure' ? 'bg-[#ff3333]' :
                              'bg-[#FFD700]'}`}></span>
                        {run.status === 'completed' ? run.conclusion : run.status}
                      </span>
                    </div>
                    <div className="text-right">
                      <button
                        onClick={() => handleViewLogs(run.id)}
                        className="text-[#00FF41]/60 text-[9px] uppercase"
                      >
                        [LOG]
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Logs Sheet */}
      <Sheet open={!!selectedRunId} onOpenChange={(open) => !open && setSelectedRunId(null)}>
        <SheetContent className="w-full sm:w-[800px] sm:max-w-[90vw] flex flex-col h-full bg-black border-l border-[#00FF41]/20">
          <SheetHeader className="mb-4">
            <SheetTitle className="text-[#00FF41] uppercase text-sm tracking-wider">
              LOG OUTPUT #{selectedRunId}
            </SheetTitle>
            <SheetDescription className="text-[#555] text-xs uppercase">
              GitHub Actions execution log
            </SheetDescription>
          </SheetHeader>

          <div className="flex-1 overflow-hidden border border-[#1a1a1a] bg-[#050505] text-[#00FF41] font-mono text-xs relative">
            {isLoadingLogs ? (
              <div className="absolute inset-0 flex items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-[#00FF41]" />
              </div>
            ) : (
              <ScrollArea className="h-full w-full p-4">
                <pre className="whitespace-pre-wrap break-all text-[11px] text-[#00FF41]/80">
                  {logContent || "No log content."}
                </pre>
              </ScrollArea>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
