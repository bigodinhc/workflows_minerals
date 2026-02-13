"use client";

import { Loader2, ExternalLink, GitCommit, Calendar, CheckCircle2, XCircle, AlertCircle, PlayCircle } from "lucide-react";
import useSWR from "swr";
import { format } from "date-fns";
import { ptBR } from "date-fns/locale";

const fetcher = (url: string) => fetch(url).then((res) => res.json());

export default function ExecutionsPage() {
    const { data: runs, error, isLoading } = useSWR("/api/workflows", fetcher, { refreshInterval: 10000 });

    const getStatusColor = (status: string, conclusion: string) => {
        if (status === "queued" || status === "in_progress") return "text-[#FFD700]";
        if (conclusion === "success") return "text-[#00FF41]";
        if (conclusion === "failure") return "text-[#ff3333]";
        return "text-[#555]";
    };

    const getStatusDot = (status: string, conclusion: string) => {
        if (status === "queued" || status === "in_progress") return "bg-[#FFD700]";
        if (conclusion === "success") return "bg-[#00FF41]";
        if (conclusion === "failure") return "bg-[#ff3333]";
        return "bg-[#555]";
    };

    const getEventLabel = (event: string) => {
        switch (event) {
            case 'schedule': return { text: 'SCHEDULE', color: 'text-[#00bfff] border-[#00bfff]/30' };
            case 'workflow_dispatch': return { text: 'MANUAL', color: 'text-[#ff00ff] border-[#ff00ff]/30' };
            case 'push': return { text: 'PUSH', color: 'text-[#FFD700] border-[#FFD700]/30' };
            default: return { text: event.toUpperCase(), color: 'text-[#555] border-[#555]/30' };
        }
    };

    return (
        <div className="p-4 md:p-6 space-y-6 bg-black text-[#e0e0e0] min-h-screen">
            {/* Header */}
            <div>
                <p className="text-[10px] text-[#00FF41] uppercase tracking-[0.3em] mb-1">/ EXECUTIONS</p>
                <h1 className="text-xl md:text-2xl font-bold uppercase tracking-tight text-white">
                    EXECUTION HISTORY
                </h1>
                <p className="text-[10px] text-[#555] mt-1 uppercase">FULL LOG OF ALL AUTOMATION RUNS</p>
            </div>

            {/* Table */}
            <div className="border border-[#1a1a1a] bg-[#0a0a0a] overflow-hidden">
                {isLoading ? (
                    <div className="flex items-center justify-center py-20">
                        <Loader2 className="h-6 w-6 animate-spin text-[#00FF41]" />
                    </div>
                ) : error ? (
                    <div className="flex items-center justify-center py-20 text-[#ff3333] text-xs uppercase">
                        ERROR: {error.message || "CONNECTION FAILED"}
                    </div>
                ) : (
                    <>
                        {/* Desktop Table */}
                        <div className="hidden md:block">
                            <div className="grid grid-cols-7 gap-2 px-4 py-2 text-[9px] font-medium text-[#555] uppercase tracking-wider border-b border-[#1a1a1a] bg-[#050505]">
                                <div>RUN #</div>
                                <div>STATUS</div>
                                <div>TYPE</div>
                                <div className="col-span-2">COMMIT / MESSAGE</div>
                                <div>TIMESTAMP</div>
                                <div className="text-right">LINK</div>
                            </div>
                            {runs?.map((run: any) => {
                                const evt = getEventLabel(run.event);
                                return (
                                    <div key={run.id} className="grid grid-cols-7 gap-2 px-4 py-2.5 text-xs items-center hover:bg-[#00FF41]/5 transition-colors border-b border-[#0a0a0a] last:border-0">
                                        <div className="font-mono text-[#555]">#{run.run_number}</div>
                                        <div>
                                            <span className={`inline-flex items-center gap-1.5 text-[10px] uppercase tracking-wider ${getStatusColor(run.status, run.conclusion)}`}>
                                                <span className={`inline-block w-1.5 h-1.5 ${getStatusDot(run.status, run.conclusion)}`}></span>
                                                {run.conclusion || run.status}
                                            </span>
                                        </div>
                                        <div>
                                            <span className={`text-[9px] border px-1.5 py-0.5 uppercase tracking-wider ${evt.color}`}>
                                                {evt.text}
                                            </span>
                                        </div>
                                        <div className="col-span-2 truncate">
                                            <span className="text-white/80 text-[11px]">{run.commit?.message || run.name}</span>
                                            {run.commit?.sha && (
                                                <span className="text-[#333] ml-2 text-[9px] font-mono">{run.commit.sha.substring(0, 7)}</span>
                                            )}
                                        </div>
                                        <div className="text-[#555] text-[10px]">
                                            {format(new Date(run.created_at), "dd/MM HH:mm", { locale: ptBR })}
                                        </div>
                                        <div className="text-right">
                                            <a href={run.html_url} target="_blank" rel="noopener noreferrer"
                                                className="text-[#00FF41]/50 hover:text-[#00FF41] text-[9px] uppercase tracking-wider transition-colors">
                                                [OPEN]
                                            </a>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>

                        {/* Mobile Table */}
                        <div className="md:hidden">
                            <div className="grid grid-cols-3 gap-2 px-3 py-2 text-[9px] font-medium text-[#555] uppercase tracking-wider border-b border-[#1a1a1a] bg-[#050505]">
                                <div>RUN</div>
                                <div>STATUS</div>
                                <div className="text-right">LINK</div>
                            </div>
                            {runs?.map((run: any) => (
                                <div key={run.id} className="grid grid-cols-3 gap-2 px-3 py-2 text-[10px] items-center border-b border-[#0a0a0a] last:border-0">
                                    <div className="truncate">
                                        <div className="text-white text-[10px] font-medium truncate">{run.name}</div>
                                        <div className="text-[#333] text-[8px]">
                                            {format(new Date(run.created_at), "dd/MM HH:mm")}
                                        </div>
                                    </div>
                                    <div>
                                        <span className={`inline-flex items-center gap-1 text-[9px] uppercase ${getStatusColor(run.status, run.conclusion)}`}>
                                            <span className={`inline-block w-1 h-1 ${getStatusDot(run.status, run.conclusion)}`}></span>
                                            {run.conclusion || run.status}
                                        </span>
                                    </div>
                                    <div className="text-right">
                                        <a href={run.html_url} target="_blank" rel="noopener noreferrer"
                                            className="text-[#00FF41]/50 text-[9px] uppercase">
                                            [→]
                                        </a>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
