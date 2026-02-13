"use client";

import { Button } from "@/components/ui/button";
import {
    Play, CheckCircle2, XCircle, Loader2,
    Calendar, FileText, ChevronDown, ChevronUp,
    AlertTriangle
} from "lucide-react";
import useSWR from "swr";
import { formatDistanceToNow, format } from "date-fns";
import { ptBR } from "date-fns/locale";
import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";

const fetcher = (url: string) => fetch(url).then((res) => res.json());

const WORKFLOW_CATALOG = [
    {
        id: "morning_check.yml",
        name: "MORNING CHECK",
        description: "Coleta preços de minério de ferro do Platts (Fines, Lump, Pellet, VIU) e envia report formatado via WhatsApp.",
        schedule: "08:30, 09:00, 09:30, 10:00 BRT",
        tags: ["PLATTS", "IRON_ORE", "WHATSAPP"],
        dataPoints: ["Brazilian Blend Fines", "Jimblebar Fines", "IODEX", "Pellet Premium"]
    },
    {
        id: "baltic_ingestion.yml",
        name: "BALTIC EXCHANGE",
        description: "Monitora email Outlook via Graph API, busca email da Baltic Exchange, extrai PDF com Claude, envia BDI + Rotas Capesize via WhatsApp.",
        schedule: "09:00-11:45 BRT (15min)",
        tags: ["EMAIL", "PDF", "CLAUDE_AI", "WHATSAPP"],
        dataPoints: ["BDI", "C3 Tubarao→Qingdao", "C5 W.Australia→Qingdao", "Capesize Index"]
    },
    {
        id: "daily_report.yml",
        name: "DAILY SGX REPORT",
        description: "Conecta LSEG/Refinitiv via API, coleta futuros de minério de ferro 62% Fe da SGX e envia report com vencimentos via WhatsApp.",
        schedule: "05:00, 07:00, 09:30, 12:00, 16:00, 22:05 BRT",
        tags: ["SGX", "LSEG", "FUTURES", "WHATSAPP"],
        dataPoints: ["IO Swap Fev/26", "IO Swap Mar/26", "IO Swap Abr/26", "..."]
    },
    {
        id: "rationale_news.yml",
        name: "RATIONALE NEWS",
        description: "Coleta notícias Apify, processa com 3 agentes IA (Writer → Critique → Curator), preview Telegram e disparo WhatsApp.",
        schedule: "12:00, 12:30, 13:00 BRT",
        tags: ["TELEGRAM", "CLAUDE_AI", "WHATSAPP", "APIFY"],
        dataPoints: ["Notícias de Mercado", "Análise IA", "Aprovação Manual", "Disparo WhatsApp"]
    }
];

interface WorkflowRun {
    id: number;
    name: string;
    status: string;
    conclusion: string | null;
    created_at: string;
    html_url: string;
}

function calculateHealth(runs: WorkflowRun[]) {
    if (!runs || runs.length === 0) return { percentage: 0, status: 'critical' as const, count: 0 };
    const completed = runs.filter(r => r.conclusion !== null);
    const successCount = completed.filter(r => r.conclusion === 'success').length;
    const percentage = completed.length > 0 ? Math.round((successCount / completed.length) * 100) : 0;
    return {
        percentage,
        status: (percentage >= 80 ? 'good' : percentage >= 50 ? 'warning' : 'critical') as 'good' | 'warning' | 'critical',
        count: completed.length
    };
}

function WorkflowCard({
    workflow,
    runs,
    onTrigger,
    isTriggering,
    onViewLogs
}: {
    workflow: typeof WORKFLOW_CATALOG[0];
    runs: WorkflowRun[];
    onTrigger: () => void;
    isTriggering: boolean;
    onViewLogs: (runId: number) => void;
}) {
    const [expanded, setExpanded] = useState(false);
    const health = calculateHealth(runs);
    const lastRun = runs?.[0];

    const healthColor = {
        good: "text-[#00FF41] border-[#00FF41]/30",
        warning: "text-[#FFD700] border-[#FFD700]/30",
        critical: "text-[#ff3333] border-[#ff3333]/30"
    };

    return (
        <div className={`border bg-[#0a0a0a] p-4 space-y-3 ${health.count > 0 ? `border-l-2 ${healthColor[health.status].split(' ')[1]}` : 'border-[#1a1a1a]'} border-t-[#1a1a1a] border-r-[#1a1a1a] border-b-[#1a1a1a]`}>
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider">{workflow.name}</h3>
                    <p className="text-[10px] text-[#555] mt-1 leading-relaxed">{workflow.description}</p>
                </div>
                <span className={`text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 border ${healthColor[health.status]}`}>
                    {health.percentage}%
                </span>
            </div>

            {/* Schedule */}
            <div className="flex items-center gap-2 text-[10px] text-[#444]">
                <Calendar className="w-3 h-3" />
                <span className="uppercase">{workflow.schedule}</span>
            </div>

            {/* Tags */}
            <div className="flex flex-wrap gap-1.5">
                {workflow.tags.map(tag => (
                    <span key={tag} className="text-[9px] text-[#00FF41]/60 border border-[#00FF41]/15 px-1.5 py-0.5 uppercase tracking-wider">
                        [{tag}]
                    </span>
                ))}
            </div>

            {/* Last Execution */}
            {lastRun && (
                <div className="border-t border-[#1a1a1a] pt-3 space-y-2">
                    <div className="flex items-center justify-between">
                        <span className="text-[9px] text-[#555] uppercase tracking-wider">LAST EXEC</span>
                        <div className="flex items-center gap-2">
                            {lastRun.conclusion === 'success' ? (
                                <CheckCircle2 className="w-3 h-3 text-[#00FF41]" />
                            ) : lastRun.conclusion === 'failure' ? (
                                <XCircle className="w-3 h-3 text-[#ff3333]" />
                            ) : (
                                <Loader2 className="w-3 h-3 text-[#FFD700] animate-spin" />
                            )}
                            <span className="text-[10px] text-[#666]">
                                {formatDistanceToNow(new Date(lastRun.created_at), {
                                    addSuffix: true,
                                    locale: ptBR
                                })}
                            </span>
                        </div>
                    </div>

                    {expanded && (
                        <div className="space-y-2 pt-1">
                            <div className="text-[9px] text-[#444]">
                                {format(new Date(lastRun.created_at), "dd/MM/yyyy 'às' HH:mm", { locale: ptBR })}
                            </div>
                            <div className="text-[9px] text-[#555]">
                                <span className="text-[#00FF41]/50 uppercase">DATA POINTS:</span>
                                <ul className="mt-1 space-y-0.5 ml-2">
                                    {workflow.dataPoints.map(dp => (
                                        <li key={dp} className="text-[#666]">→ {dp}</li>
                                    ))}
                                </ul>
                            </div>
                            <button
                                className="text-[9px] text-[#00FF41]/50 hover:text-[#00FF41] uppercase tracking-wider"
                                onClick={() => onViewLogs(lastRun.id)}
                            >
                                [VIEW FULL LOG]
                            </button>
                        </div>
                    )}

                    <button
                        className="w-full text-[9px] text-[#333] hover:text-[#555] uppercase tracking-wider py-1 transition-colors"
                        onClick={() => setExpanded(!expanded)}
                    >
                        {expanded ? "▲ LESS" : "▼ MORE"}
                    </button>
                </div>
            )}

            {/* Execute Button */}
            <button
                onClick={onTrigger}
                disabled={isTriggering}
                className="w-full flex items-center justify-center gap-2 py-2 text-[10px] uppercase tracking-wider font-bold
                  border border-[#00FF41]/30 text-[#00FF41] bg-[#00FF41]/5
                  hover:bg-[#00FF41]/15 hover:border-[#00FF41]/50
                  disabled:opacity-50 transition-all"
            >
                {isTriggering ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                    <Play className="w-3 h-3" />
                )}
                EXECUTE NOW
            </button>
        </div>
    );
}

export default function WorkflowsPage() {
    const { data, error, mutate } = useSWR("/api/workflows", fetcher, { refreshInterval: 10000 });
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
            if (res.ok) {
                const text = await res.text();
                setLogContent(text);
            } else {
                setLogContent("Erro ao carregar logs.");
            }
        } catch (e) {
            setLogContent("Erro ao carregar logs.");
        } finally {
            setIsLoadingLogs(false);
        }
    };

    // Group runs by workflow
    const runsByWorkflow: Record<string, WorkflowRun[]> = {};
    WORKFLOW_CATALOG.forEach(w => { runsByWorkflow[w.id] = []; });

    if (data?.workflow_runs) {
        WORKFLOW_CATALOG.forEach(w => {
            runsByWorkflow[w.id] = data.workflow_runs.filter((run: any) =>
                run.path?.endsWith(w.id) ||
                run.name?.toLowerCase().replace(/ /g, "_") + ".yml" === w.id.toLowerCase() ||
                run.name?.toLowerCase().includes(w.id.replace(".yml", "").replace(/_/g, " "))
            );
        });
    }

    return (
        <div className="min-h-screen bg-black p-4 md:p-6">
            <div className="max-w-6xl mx-auto space-y-6">
                {/* Header */}
                <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                    <div>
                        <p className="text-[10px] text-[#00FF41] uppercase tracking-[0.3em] mb-1">/ CATALOG</p>
                        <h1 className="text-xl md:text-2xl font-bold text-white uppercase tracking-tight">
                            AUTOMATION WORKFLOWS
                        </h1>
                        <p className="text-[10px] text-[#555] mt-1 uppercase">
                            {WORKFLOW_CATALOG.length} ACTIVE PROCESSES
                        </p>
                    </div>
                    <button
                        onClick={() => window.location.href = "/"}
                        className="text-[10px] text-[#555] hover:text-[#00FF41] uppercase tracking-wider transition-colors"
                    >
                        ← BACK TO DASHBOARD
                    </button>
                </div>

                {/* Workflow Cards Grid */}
                <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
                    {WORKFLOW_CATALOG.map(workflow => (
                        <WorkflowCard
                            key={workflow.id}
                            workflow={workflow}
                            runs={runsByWorkflow[workflow.id] || []}
                            onTrigger={() => handleTrigger(workflow.id)}
                            isTriggering={triggeringId === workflow.id}
                            onViewLogs={handleViewLogs}
                        />
                    ))}
                </div>

                {/* Log Viewer Sheet */}
                <Sheet open={selectedRunId !== null} onOpenChange={() => setSelectedRunId(null)}>
                    <SheetContent className="w-full sm:w-[600px] sm:max-w-[600px] bg-black border-l border-[#00FF41]/20">
                        <SheetHeader>
                            <SheetTitle className="text-[#00FF41] uppercase text-sm tracking-wider">
                                LOG #{selectedRunId}
                            </SheetTitle>
                        </SheetHeader>
                        <ScrollArea className="h-[calc(100vh-100px)] mt-4">
                            {isLoadingLogs ? (
                                <div className="flex items-center justify-center h-32">
                                    <Loader2 className="w-6 h-6 animate-spin text-[#00FF41]" />
                                </div>
                            ) : (
                                <pre className="text-[11px] text-[#00FF41]/80 whitespace-pre-wrap font-mono bg-[#050505] p-4 border border-[#1a1a1a]">
                                    {logContent}
                                </pre>
                            )}
                        </ScrollArea>
                    </SheetContent>
                </Sheet>
            </div>
        </div>
    );
}
