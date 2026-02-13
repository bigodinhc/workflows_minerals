"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Activity, Play, Clock, AlertCircle, CheckCircle2, XCircle, Loader2, FileText } from "lucide-react";
import useSWR from "swr";
import { formatDistanceToNow } from "date-fns";
import { ptBR } from "date-fns/locale";
import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";

const fetcher = (url: string) => fetch(url).then((res) => res.json());

const WORKFLOWS = [
  { id: "morning_check.yml", name: "Morning Check (Platts)", type: "Morning" },
  { id: "baltic_ingestion.yml", name: "Baltic Ingestion (Email)", type: "Mid-Day" },
  { id: "daily_report.yml", name: "Daily Report (SGX)", type: "Afternoon" },
  { id: "rationale_news.yml", name: "Rationale News (Telegram)", type: "On-Demand" }
];

export default function Home() {
  const { data: runs, error, mutate } = useSWR("/api/workflows", fetcher, { refreshInterval: 10000 });
  const [triggeringId, setTriggeringId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [logContent, setLogContent] = useState<string | null>(null);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);

  // ... (handleTrigger remains same) ...
  const handleTrigger = async (workflowId: string) => {
    setTriggeringId(workflowId);
    try {
      await fetch("/api/workflows", {
        method: "POST",
        body: JSON.stringify({ workflow_id: workflowId }),
        headers: { "Content-Type": "application/json" }
      });
      // Invalidate cache to show new pending run nicely
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

  // Helper to parse "Item:" logs if present
  const renderParsedLogs = () => {
    if (!logContent) return null;

    const itemLines = logContent.split('\n').filter(l => l.includes('Item:'));
    if (itemLines.length === 0) return null;

    return (
      <div className="mb-6 rounded-md border border-border bg-muted/50 p-4">
        <h4 className="mb-2 font-semibold text-sm">Dados Coletados (Resumo)</h4>
        <div className="space-y-1 text-xs font-mono">
          {itemLines.map((line, i) => {
            // Cleanup timestamp if present "[INFO] ..."
            const clean = line.substring(line.indexOf("Item:"));
            return <div key={i}>{clean}</div>;
          })}
        </div>
      </div>
    );
  };

  const lastRun = runs?.[0];
  const isOnline = !error;
  const lastSuccess = runs?.find((r: any) => r.conclusion === "success");
  const failedToday = runs?.filter((r: any) => {
    if (!r.created_at) return false;
    const isToday = new Date(r.created_at).toDateString() === new Date().toDateString();
    return isToday && r.conclusion === "failure";
  }).length || 0;

  return (
    <div className="p-4 md:p-8 space-y-6 md:space-y-8 bg-background text-foreground min-h-screen">
      {/* Header */}
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-white/90">Dashboard</h1>
          <p className="text-muted-foreground mt-1 text-sm">Monitoramento em tempo real dos workflows.</p>
        </div>
        <div className="flex flex-wrap gap-2 md:gap-3">
          <Button
            variant="secondary"
            onClick={() => window.location.href = "/workflows"}
            className="bg-zinc-800 hover:bg-zinc-700 text-xs md:text-sm"
          >
            📊 Catálogo
          </Button>
          {WORKFLOWS.map(wf => (
            <Button
              key={wf.id}
              onClick={() => handleTrigger(wf.id)}
              disabled={triggeringId === wf.id}
              variant="outline"
              size="sm"
              className={`border-primary/20 hover:bg-primary/10 transition-all text-xs ${triggeringId === wf.id ? "opacity-80" : ""}`}
            >
              {triggeringId === wf.id ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Play className="mr-1 h-3 w-3" />}
              <span className="hidden sm:inline">{wf.name}</span>
              <span className="sm:hidden">{wf.type}</span>
            </Button>
          ))}
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid gap-4 md:gap-6 grid-cols-2 lg:grid-cols-4">
        {/* ... (Stats Cards remain same) ... */}
        <Card className="bg-card border-border shadow-sm hover:shadow-md transition-shadow">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Status do Sistema</CardTitle>
            <Activity className="h-4 w-4 text-primary" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-green-500 flex items-center gap-2">
              {isOnline ? "Online" : "Offline"}
              {isOnline && <span className="relative flex h-3 w-3"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span><span className="relative inline-flex rounded-full h-3 w-3 bg-green-500"></span></span>}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              GitHub API Connected
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Última Execução</CardTitle>
            {lastRun?.conclusion === "success" ? <CheckCircle2 className="h-4 w-4 text-green-500" /> : <XCircle className="h-4 w-4 text-red-500" />}
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold capitalize">{lastRun?.conclusion || lastRun?.status || "..."}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {lastRun ? formatDistanceToNow(new Date(lastRun.created_at), { addSuffix: true, locale: ptBR }) : "Carregando..."}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Último Sucesso</CardTitle>
            <CheckCircle2 className="h-4 w-4 text-primary" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">Done</div>
            <p className="text-xs text-muted-foreground mt-1">
              {lastSuccess ? formatDistanceToNow(new Date(lastSuccess.created_at), { addSuffix: true, locale: ptBR }) : "-"}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-card border-border">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Falhas Hoje</CardTitle>
            <AlertCircle className={`h-4 w-4 ${failedToday > 0 ? "text-red-500" : "text-muted-foreground"}`} />
          </CardHeader>
          <CardContent>
            <div className={`text-2xl font-bold ${failedToday > 0 ? "text-red-500" : ""}`}>{failedToday}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {failedToday === 0 ? "+100% estabilidade" : "Verifique os logs"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Recents Table */}
      <div className="mt-8 space-y-4">
        <h2 className="text-xl font-semibold tracking-tight">Histórico de Execuções</h2>

        <div className="rounded-xl border border-border bg-card overflow-hidden">
          {!runs ? (
            <div className="p-8 text-center text-muted-foreground">Carregando dados...</div>
          ) : (
            <div className="w-full">
              <div className="hidden md:grid grid-cols-5 gap-4 p-4 text-sm font-medium text-muted-foreground border-b border-border/50">
                <div className="col-span-2">Workflow / Commit</div>
                <div>Status</div>
                <div>Data</div>
                <div className="text-right">Ação</div>
              </div>
              {/* Mobile header */}
              <div className="md:hidden grid grid-cols-3 gap-2 p-3 text-xs font-medium text-muted-foreground border-b border-border/50">
                <div>Workflow</div>
                <div>Status</div>
                <div className="text-right">Ação</div>
              </div>
              {runs.map((run: any) => (
                <div key={run.id}>
                  {/* Desktop row */}
                  <div className="hidden md:grid grid-cols-5 gap-4 p-4 text-sm items-center hover:bg-muted/50 transition-colors border-b border-border/50 last:border-0">
                    <div className="col-span-2 font-mono truncate">
                      <div className="font-semibold text-foreground">{run.name} #{run.run_number}</div>
                    </div>
                    <div>
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium capitalize
                                      ${run.conclusion === 'success' ? 'bg-green-500/10 text-green-500' :
                          run.conclusion === 'failure' ? 'bg-red-500/10 text-red-500' :
                            'bg-yellow-500/10 text-yellow-500'}`}>
                        {run.status === 'completed' ? run.conclusion : run.status}
                      </span>
                    </div>
                    <div className="text-muted-foreground">
                      {new Date(run.created_at).toLocaleString('pt-BR')}
                    </div>
                    <div className="text-right flex justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleViewLogs(run.id)}
                        className="text-primary hover:text-primary/80 hover:bg-primary/10 h-8 px-2"
                      >
                        <FileText className="h-4 w-4 mr-1" />
                        Ver Log
                      </Button>
                    </div>
                  </div>
                  {/* Mobile row */}
                  <div className="md:hidden grid grid-cols-3 gap-2 p-3 text-xs items-center border-b border-border/50 last:border-0">
                    <div className="truncate">
                      <div className="font-semibold text-foreground truncate">{run.name}</div>
                      <div className="text-muted-foreground text-[10px]">{new Date(run.created_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</div>
                    </div>
                    <div>
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium capitalize
                                      ${run.conclusion === 'success' ? 'bg-green-500/10 text-green-500' :
                          run.conclusion === 'failure' ? 'bg-red-500/10 text-red-500' :
                            'bg-yellow-500/10 text-yellow-500'}`}>
                        {run.status === 'completed' ? run.conclusion : run.status}
                      </span>
                    </div>
                    <div className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleViewLogs(run.id)}
                        className="text-primary h-7 px-2 text-[10px]"
                      >
                        <FileText className="h-3 w-3 mr-1" />
                        Log
                      </Button>
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
        <SheetContent className="w-full sm:w-[800px] sm:max-w-[90vw] flex flex-col h-full bg-card border-l border-border">
          <SheetHeader className="mb-4">
            <SheetTitle>Logs de Execução #{selectedRunId}</SheetTitle>
            <SheetDescription>
              Detalhes completos do job no GitHub Actions
            </SheetDescription>
          </SheetHeader>

          <div className="flex-1 overflow-hidden rounded-md border border-border bg-black/90 text-white font-mono text-xs relative">
            {isLoadingLogs ? (
              <div className="absolute inset-0 flex items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            ) : (
              <ScrollArea className="h-full w-full p-4">
                {renderParsedLogs()}
                <pre className="whitespace-pre-wrap break-all opacity-80">
                  {logContent || "Sem conteúdo de log."}
                </pre>
              </ScrollArea>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
