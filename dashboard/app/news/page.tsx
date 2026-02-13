
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Loader2, Check, X, FileText, RefreshCw } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { ptBR } from "date-fns/locale";
import useSWR from "swr";

const fetcher = (url: string) => fetch(url).then(res => res.json());

interface Draft {
    id: string;
    created_at: string;
    source_date: string;
    original_count: number;
    ai_text: string;
    source_summary: string;
}

export default function NewsReviewPage() {
    const { data, error, mutate } = useSWR("/api/news", fetcher, { refreshInterval: 5000 });
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editText, setEditText] = useState("");
    const [isProcessing, setIsProcessing] = useState(false);

    if (!data?.drafts?.length) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] text-[#555]">
                <FileText className="w-12 h-12 mb-4 text-[#222]" />
                <h2 className="text-sm font-bold uppercase tracking-wider mb-1 text-[#444]">NO PENDING DRAFTS</h2>
                <p className="text-[10px] uppercase tracking-wider text-[#333]">Waiting for new articles...</p>
                <button
                    onClick={() => mutate()}
                    className="mt-6 text-[10px] text-[#00FF41]/60 hover:text-[#00FF41] uppercase tracking-wider border border-[#00FF41]/20 px-4 py-2 transition-colors"
                >
                    <RefreshCw className="w-3 h-3 inline mr-2" />
                    REFRESH
                </button>
            </div>
        );
    }

    const handleAction = async (draftId: string, action: 'approve' | 'reject') => {
        if (!confirm(action === 'approve' ? "Confirma envio para TODOS os contatos?" : "Rejeitar rascunho?")) return;

        setIsProcessing(true);
        try {
            const textToSend = editingId === draftId ? editText : data.drafts.find((d: Draft) => d.id === draftId)?.ai_text;

            const res = await fetch("/api/news", {
                method: "POST",
                body: JSON.stringify({ action, draftId, text: textToSend }),
                headers: { "Content-Type": "application/json" }
            });

            if (!res.ok) throw new Error("Operation failed");

            mutate();
            setEditingId(null);
            alert(action === 'approve' ? "Mensagem enviada! 🚀" : "Rascunho rejeitado.");

        } catch (e) {
            alert("Error: " + String(e));
        } finally {
            setIsProcessing(false);
        }
    };

    return (
        <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-6 bg-black min-h-screen">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <p className="text-[10px] text-[#00FF41] uppercase tracking-[0.3em] mb-1">/ NEWS REVIEW</p>
                    <h1 className="text-xl font-bold text-white uppercase tracking-tight flex items-center gap-3">
                        DRAFT APPROVAL
                        <span className="text-[10px] text-[#FFD700] border border-[#FFD700]/30 px-2 py-0.5 font-bold">
                            {data.drafts.length} PENDING
                        </span>
                    </h1>
                </div>
            </div>

            <div className="space-y-4">
                {data.drafts.map((draft: Draft) => (
                    <div key={draft.id} className="border border-[#1a1a1a] bg-[#0a0a0a]">
                        {/* Draft Header */}
                        <div className="px-4 py-3 border-b border-[#1a1a1a] flex items-center justify-between">
                            <div>
                                <h3 className="text-xs font-bold text-white uppercase tracking-wider">
                                    RATIONALE // {draft.source_date}
                                </h3>
                                <div className="text-[9px] text-[#444] mt-0.5 flex gap-3 uppercase">
                                    <span>{draft.original_count} ARTICLES</span>
                                    <span>•</span>
                                    <span>{formatDistanceToNow(new Date(draft.created_at), { locale: ptBR, addSuffix: true })}</span>
                                </div>
                            </div>
                        </div>

                        {/* Content */}
                        <div className="p-4">
                            <div className="border border-[#1a1a1a] bg-[#050505] p-3">
                                {editingId === draft.id ? (
                                    <textarea
                                        value={editText}
                                        onChange={(e) => setEditText(e.target.value)}
                                        className="w-full min-h-[300px] font-mono text-[11px] bg-transparent text-[#00FF41]/80 border-none outline-none resize-y"
                                    />
                                ) : (
                                    <pre
                                        className="text-[11px] text-[#ccc] whitespace-pre-wrap font-mono cursor-pointer hover:bg-[#0a0a0a] p-2 transition-colors"
                                        onClick={() => {
                                            setEditingId(draft.id);
                                            setEditText(draft.ai_text);
                                        }}
                                        title="Click to edit"
                                    >
                                        {draft.ai_text}
                                    </pre>
                                )}
                            </div>

                            {editingId !== draft.id && (
                                <p className="text-[9px] text-[#333] mt-2 uppercase tracking-wider">
                                    * CLICK TEXT TO EDIT BEFORE APPROVING
                                </p>
                            )}
                        </div>

                        {/* Actions */}
                        <div className="px-4 py-3 border-t border-[#1a1a1a] flex items-center justify-between">
                            <button
                                className="text-[10px] text-[#ff3333]/60 hover:text-[#ff3333] uppercase tracking-wider transition-colors disabled:opacity-50"
                                onClick={() => handleAction(draft.id, 'reject')}
                                disabled={isProcessing}
                            >
                                [REJECT]
                            </button>

                            <div className="flex gap-2">
                                {editingId === draft.id && (
                                    <button
                                        className="text-[10px] text-[#555] hover:text-white uppercase tracking-wider transition-colors"
                                        onClick={() => setEditingId(null)}
                                        disabled={isProcessing}
                                    >
                                        [CANCEL]
                                    </button>
                                )}
                                <button
                                    className="text-[10px] text-[#00FF41] border border-[#00FF41]/30 px-3 py-1.5 uppercase tracking-wider font-bold
                                      hover:bg-[#00FF41]/10 transition-all disabled:opacity-50"
                                    onClick={() => handleAction(draft.id, 'approve')}
                                    disabled={isProcessing}
                                >
                                    {isProcessing ? (
                                        <Loader2 className="w-3 h-3 animate-spin inline mr-1" />
                                    ) : (
                                        <Check className="w-3 h-3 inline mr-1" />
                                    )}
                                    {editingId === draft.id ? "SAVE & SEND" : "APPROVE & SEND"}
                                </button>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
