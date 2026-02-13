"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
    Play, Clock, CheckCircle2, XCircle, Loader2,
    Heart, Calendar, FileText, ChevronDown, ChevronUp,
    Zap, AlertTriangle
} from "lucide-react";
import useSWR from "swr";
import { formatDistanceToNow, format } from "date-fns";
import { ptBR } from "date-fns/locale";
import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";

const fetcher = (url: string) => fetch(url).then((res) => res.json());

// Workflow catalog with metadata
const WORKFLOW_CATALOG = [
    {
        id: "morning_check.yml",
        name: "Morning Check (Platts)",
        description: "Coleta preços de minério de ferro do Platts (Fines, Lump, Pellet, VIU) e envia report formatado via WhatsApp para os contatos cadastrados.",
        schedule: "08:30, 09:00, 09:30, 10:00 BRT",
        emoji: "🪨",
        tags: ["Platts", "Iron Ore", "WhatsApp"],
        dataPoints: ["Brazilian Blend Fines", "Jimblebar Fines", "IODEX", "Pellet Premium"]
    },
    {
        id: "baltic_ingestion.yml",
        name: "Baltic Exchange (Email)",
        description: "Monitora caixa de email do Outlook via Graph API, busca email da Baltic Exchange, extrai PDF com IA Claude, e envia BDI + Rotas Capesize via WhatsApp.",
        schedule: "09:00-11:45 BRT (cada 15min)",
        emoji: "🚢",
        tags: ["Email", "PDF", "Claude AI", "WhatsApp"],
        dataPoints: ["BDI", "C3 Tubarao→Qingdao", "C5 W.Australia→Qingdao", "Capesize Index"]
    },
    {
        id: "daily_report.yml",
        name: "Daily SGX Report",
        description: "Conecta ao LSEG/Refinitiv via API, coleta futuros de minério de ferro 62% Fe da SGX e envia report com todos os vencimentos via WhatsApp.",
        schedule: "05:00, 07:00, 09:30, 12:00, 16:00, 22:05 BRT",
        emoji: "📈",
        tags: ["SGX", "LSEG", "Futures", "WhatsApp"],
        dataPoints: ["IO Swap Fev/26", "IO Swap Mar/26", "IO Swap Abr/26", "..."]
    },
    {
        id: "rationale_news.yml",
        name: "Rationale News (Telegram)",
        description: "Coleta notícias via Apify, processa com cadeia de 3 agentes IA (Writer → Critique → Curator), envia preview para aprovação no Telegram e dispara via WhatsApp.",
        schedule: "12:00, 12:30, 13:00 BRT",
        emoji: "📰",
        tags: ["Telegram", "Claude AI", "WhatsApp", "Apify"],
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

function calculateHealth(runs: WorkflowRun[]): { percentage: number; status: 'good' | 'warning' | 'critical'; count: number } {
    if (!runs || runs.length === 0) {
        return { percentage: 0, status: 'critical', count: 0 };
    }

    const completed = runs.filter(r => r.conclusion !== null);
    const successCount = completed.filter(r => r.conclusion === 'success').length;
    const percentage = completed.length > 0 ? Math.round((successCount / completed.length) * 100) : 0;

    return {
        percentage,
        status: percentage >= 80 ? 'good' : percentage >= 50 ? 'warning' : 'critical',
        count: completed.length
    };
}

function HealthBadge({ health }: { health: ReturnType<typeof calculateHealth> }) {
    const colors = {
        good: "bg-green-500/20 text-green-400 border-green-500/30",
        warning: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
        critical: "bg-red-500/20 text-red-400 border-red-500/30"
    };

    const icons = {
        good: <Heart className="w-3 h-3 fill-current" />,
        warning: <AlertTriangle className="w-3 h-3" />,
        critical: <XCircle className="w-3 h-3" />
    };

    return (
        <Badge variant="outline" className={`${colors[health.status]} gap-1`}>
            {icons[health.status]}
            {health.percentage}% ({health.count} runs)
        </Badge>
    );
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

    return (
        <Card className="bg-zinc-900 border-zinc-800">
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                        <span className="text-3xl">{workflow.emoji}</span>
                        <div>
                            <CardTitle className="text-lg text-zinc-100">{workflow.name}</CardTitle>
                            <CardDescription className="text-zinc-400 mt-1">
                                {workflow.description}
                            </CardDescription>
                        </div>
                    </div>
                    <HealthBadge health={health} />
                </div>
            </CardHeader>

            <CardContent className="space-y-4">
                {/* Schedule */}
                <div className="flex items-center gap-2 text-sm text-zinc-400">
                    <Calendar className="w-4 h-4" />
                    <span>{workflow.schedule}</span>
                </div>

                {/* Tags */}
                <div className="flex flex-wrap gap-2">
                    {workflow.tags.map(tag => (
                        <Badge key={tag} variant="secondary" className="bg-zinc-800 text-zinc-300 text-xs">
                            {tag}
                        </Badge>
                    ))}
                </div>

                {/* Last Execution */}
                {lastRun && (
                    <div className="bg-zinc-800/50 rounded-lg p-3 space-y-2">
                        <div className="flex items-center justify-between">
                            <span className="text-sm text-zinc-400">Última Execução</span>
                            <div className="flex items-center gap-2">
                                {lastRun.conclusion === 'success' ? (
                                    <CheckCircle2 className="w-4 h-4 text-green-500" />
                                ) : lastRun.conclusion === 'failure' ? (
                                    <XCircle className="w-4 h-4 text-red-500" />
                                ) : (
                                    <Loader2 className="w-4 h-4 text-yellow-500 animate-spin" />
                                )}
                                <span className="text-sm text-zinc-300">
                                    {formatDistanceToNow(new Date(lastRun.created_at), {
                                        addSuffix: true,
                                        locale: ptBR
                                    })}
                                </span>
                            </div>
                        </div>

                        {expanded && (
                            <div className="pt-2 border-t border-zinc-700 space-y-2">
                                <div className="text-xs text-zinc-500">
                                    {format(new Date(lastRun.created_at), "dd/MM/yyyy 'às' HH:mm", { locale: ptBR })}
                                </div>
                                <div className="text-xs text-zinc-400">
                                    <strong>Dados coletados:</strong>
                                    <ul className="mt-1 list-disc list-inside">
                                        {workflow.dataPoints.map(dp => (
                                            <li key={dp}>{dp}</li>
                                        ))}
                                    </ul>
                                </div>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    className="text-xs"
                                    onClick={() => onViewLogs(lastRun.id)}
                                >
                                    <FileText className="w-3 h-3 mr-1" />
                                    Ver Logs Completos
                                </Button>
                            </div>
                        )}

                        <Button
                            variant="ghost"
                            size="sm"
                            className="w-full text-xs text-zinc-500"
                            onClick={() => setExpanded(!expanded)}
                        >
                            {expanded ? (
                                <>
                                    <ChevronUp className="w-3 h-3 mr-1" />
                                    Menos detalhes
                                </>
                            ) : (
                                <>
                                    <ChevronDown className="w-3 h-3 mr-1" />
                                    Mais detalhes
                                </>
                            )}
                        </Button>
                    </div>
                )}

                {/* Actions */}
                <div className="flex gap-2 pt-2">
                    <Button
                        onClick={onTrigger}
                        disabled={isTriggering}
                        className="flex-1 bg-emerald-600 hover:bg-emerald-700"
                    >
                        {isTriggering ? (
                            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        ) : (
                            <Zap className="w-4 h-4 mr-2" />
                        )}
                        Executar Agora
                    </Button>
                </div>
            </CardContent>
        </Card>
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
    WORKFLOW_CATALOG.forEach(w => {
        runsByWorkflow[w.id] = [];
    });

    if (data?.workflow_runs) {
        data.workflow_runs.forEach((run: WorkflowRun) => {
            const workflowFile = run.name + ".yml";
            // Match by name similarity
            WORKFLOW_CATALOG.forEach(w => {
                if (run.name.toLowerCase().includes(w.name.split(" ")[0].toLowerCase()) ||
                    w.id.toLowerCase().includes(run.name.toLowerCase().replace(/ /g, "_"))) {
                    runsByWorkflow[w.id].push(run);
                }
            });
        });
    }

    // Better matching: use path from API if available
    if (data?.workflow_runs) {
        // Reset and re-match based on actual workflow path
        WORKFLOW_CATALOG.forEach(w => {
            runsByWorkflow[w.id] = data.workflow_runs.filter((run: any) =>
                run.path?.endsWith(w.id) ||
                run.name?.toLowerCase().replace(/ /g, "_") + ".yml" === w.id.toLowerCase() ||
                run.name?.toLowerCase().includes(w.id.replace(".yml", "").replace(/_/g, " "))
            );
        });
    }

    return (
        <div className="min-h-screen bg-zinc-950 p-4 md:p-6">
            <div className="max-w-6xl mx-auto space-y-6">
                {/* Header */}
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                        <h1 className="text-xl md:text-2xl font-bold text-zinc-100">📊 Catálogo de Automações</h1>
                        <p className="text-zinc-400 mt-1 text-sm">
                            {WORKFLOW_CATALOG.length} workflows ativos
                        </p>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => window.location.href = "/"}>
                        ← Voltar
                    </Button>
                </div>

                {/* Workflow Cards Grid */}
                <div className="grid gap-4 md:gap-6 grid-cols-1 lg:grid-cols-2 xl:grid-cols-3">
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
                    <SheetContent className="w-full sm:w-[600px] sm:max-w-[600px] bg-zinc-900 border-zinc-800">
                        <SheetHeader>
                            <SheetTitle className="text-zinc-100">Logs da Execução #{selectedRunId}</SheetTitle>
                        </SheetHeader>
                        <ScrollArea className="h-[calc(100vh-100px)] mt-4">
                            {isLoadingLogs ? (
                                <div className="flex items-center justify-center h-32">
                                    <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
                                </div>
                            ) : (
                                <pre className="text-xs text-zinc-300 whitespace-pre-wrap font-mono bg-zinc-950 p-4 rounded">
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
