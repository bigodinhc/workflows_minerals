"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Loader2, ExternalLink, GitCommit, Calendar, Clock, PlayCircle, CheckCircle2, XCircle, AlertCircle } from "lucide-react";
import useSWR from "swr";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";

const fetcher = (url: string) => fetch(url).then((res) => res.json());

export default function ExecutionsPage() {
    const { data: runs, error, isLoading } = useSWR("/api/workflows", fetcher, { refreshInterval: 10000 });

    const getStatusIcon = (status: string, conclusion: string) => {
        if (status === "queued" || status === "in_progress") return <Loader2 className="h-4 w-4 text-blue-400 animate-spin" />;
        if (conclusion === "success") return <CheckCircle2 className="h-4 w-4 text-green-400" />;
        if (conclusion === "failure") return <XCircle className="h-4 w-4 text-red-400" />;
        return <AlertCircle className="h-4 w-4 text-gray-400" />;
    };

    const getEventBadge = (event: string) => {
        switch (event) {
            case 'schedule': return <Badge variant="outline" className="border-blue-500 text-blue-400">Schedule</Badge>;
            case 'workflow_dispatch': return <Badge variant="outline" className="border-purple-500 text-purple-400">Manual</Badge>;
            case 'push': return <Badge variant="outline" className="border-yellow-500 text-yellow-400">Push</Badge>;
            default: return <Badge variant="outline">{event}</Badge>;
        }
    }

    return (
        <div className="p-8 space-y-8 bg-background text-foreground min-h-screen">
            {/* Header */}
            <div>
                <h1 className="text-3xl font-bold tracking-tight text-white/90">Histórico de Execuções</h1>
                <p className="text-muted-foreground mt-1">Log detalhado de todas as automações do sistema.</p>
            </div>

            {/* Main Content */}
            <Card className="bg-card border-border">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <PlayCircle className="h-5 w-5 text-primary" />
                        Workflow Runs
                    </CardTitle>
                    <CardDescription>
                        Exibindo as últimas 30 execuções do GitHub Actions.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {isLoading ? (
                        <div className="flex items-center justify-center py-20 text-muted-foreground">
                            <Loader2 className="h-8 w-8 animate-spin" />
                        </div>
                    ) : error ? (
                        <div className="flex items-center justify-center py-20 text-red-400">
                            Erro ao carregar dados: {error.message || "Verifique a conexão"}
                        </div>
                    ) : (
                        <div className="rounded-md border border-border">
                            <Table>
                                <TableHeader>
                                    <TableRow className="border-border hover:bg-muted/50">
                                        <TableHead className="w-[100px]">Run #</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Tipo</TableHead>
                                        <TableHead>Commit / Mensagem</TableHead>
                                        <TableHead>Duração</TableHead>
                                        <TableHead>Data</TableHead>
                                        <TableHead className="text-right">Link</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {runs?.map((run: any) => (
                                        <TableRow key={run.id} className="border-border hover:bg-muted/50">
                                            <TableCell className="font-mono text-muted-foreground">#{run.run_number}</TableCell>
                                            <TableCell>
                                                <div className="flex items-center gap-2 text-sm">
                                                    {getStatusIcon(run.status, run.conclusion)}
                                                    <span className="capitalize">{run.conclusion || run.status}</span>
                                                </div>
                                            </TableCell>
                                            <TableCell>{getEventBadge(run.event)}</TableCell>
                                            <TableCell>
                                                <div className="flex flex-col gap-1 max-w-[300px]">
                                                    <span className="truncate font-medium text-white/80">{run.commit?.message || run.name}</span>
                                                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                                        <GitCommit className="h-3 w-3" />
                                                        <span className="font-mono">{run.commit?.sha?.substring(0, 7)}</span>
                                                        {run.commit?.author && <span>by {run.commit.author}</span>}
                                                    </div>
                                                </div>
                                            </TableCell>
                                            <TableCell className="font-mono text-sm">
                                                {run.duration ? `${run.duration}s` : "-"}
                                            </TableCell>
                                            <TableCell className="text-muted-foreground text-sm">
                                                <div className="flex items-center gap-1">
                                                    <Calendar className="h-3 w-3" />
                                                    {format(new Date(run.created_at), "dd/MM/yyyy HH:mm", { locale: ptBR })}
                                                </div>
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <a href={run.html_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary hover:text-primary/80 transition-colors text-sm font-medium">
                                                    Logs <ExternalLink className="h-3 w-3" />
                                                </a>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

        </div>
    );
}
