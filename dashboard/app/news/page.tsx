
"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Loader2, Check, X, FileText, AlertCircle, RefreshCw } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { ptBR } from "date-fns/locale";
import useSWR from "swr";
// import { toast } from "sonner";

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

    // If no drafts
    if (!data?.drafts?.length) {
        return (
            <div className="flex flex-col items-center justify-center min-h-[60vh] text-zinc-400">
                <FileText className="w-16 h-16 mb-4 opacity-20" />
                <h2 className="text-xl font-medium mb-2">Nenhum Rascunho Pendente</h2>
                <p className="text-sm">O robô ainda não encontrou novas notícias para revisão.</p>
                <Button variant="outline" className="mt-6" onClick={() => mutate()}>
                    <RefreshCw className="w-4 h-4 mr-2" /> Atualizar
                </Button>
            </div>
        );
    }

    const handleAction = async (draftId: string, action: 'approve' | 'reject') => {
        if (!confirm(action === 'approve' ? "Confirma o envio para TODOS os contatos?" : "Rejeitar rascunho?")) return;

        setIsProcessing(true);
        try {
            const textToSend = editingId === draftId ? editText : data.drafts.find((d: Draft) => d.id === draftId)?.ai_text;

            const res = await fetch("/api/news", {
                method: "POST",
                body: JSON.stringify({
                    action,
                    draftId,
                    text: textToSend
                }),
                headers: { "Content-Type": "application/json" }
            });

            if (!res.ok) throw new Error("Falha na operação");

            mutate(); // Reload list
            setEditingId(null);
            alert(action === 'approve' ? "Mensagem enviada com sucesso! 🚀" : "Rascunho rejeitado.");

        } catch (e) {
            alert("Erro ao processar: " + String(e));
        } finally {
            setIsProcessing(false);
        }
    };

    return (
        <div className="p-6 max-w-4xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-zinc-100 flex items-center gap-2">
                    📰 Revisão de Notícias
                    <Badge variant="secondary" className="bg-yellow-500/10 text-yellow-400">
                        {data.drafts.length} Pendentes
                    </Badge>
                </h1>
            </div>

            <div className="grid gap-6">
                {data.drafts.map((draft: Draft) => (
                    <Card key={draft.id} className="bg-zinc-900 border-zinc-800">
                        <CardHeader>
                            <div className="flex justify-between items-start">
                                <div>
                                    <CardTitle className="text-lg text-zinc-200">
                                        Rationale ({draft.source_date})
                                    </CardTitle>
                                    <div className="text-sm text-zinc-500 mt-1 flex gap-2">
                                        <span>{draft.original_count} artigos originais</span>
                                        <span>•</span>
                                        <span>Criado {formatDistanceToNow(new Date(draft.created_at), { locale: ptBR, addSuffix: true })}</span>
                                    </div>
                                </div>
                            </div>
                        </CardHeader>

                        <CardContent className="space-y-4">
                            <div className="bg-zinc-950 p-4 rounded-md border border-zinc-800">
                                {editingId === draft.id ? (
                                    <Textarea
                                        value={editText}
                                        onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setEditText(e.target.value)}
                                        className="min-h-[300px] font-mono text-xs bg-zinc-900 border-none focus-visible:ring-1"
                                    />
                                ) : (
                                    <pre
                                        className="text-xs text-zinc-300 whitespace-pre-wrap font-mono cursor-pointer hover:bg-zinc-900/50 p-2 rounded transition-colors"
                                        onClick={() => {
                                            setEditingId(draft.id);
                                            setEditText(draft.ai_text);
                                        }}
                                        title="Clique para editar"
                                    >
                                        {draft.ai_text}
                                    </pre>
                                )}
                            </div>

                            {editingId !== draft.id && (
                                <div className="text-xs text-zinc-500 italic">
                                    * Clique no texto acima para editar antes de aprovar.
                                </div>
                            )}
                        </CardContent>

                        <CardFooter className="flex justify-between pt-0 pb-6 px-6">
                            <Button
                                variant="ghost"
                                className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
                                onClick={() => handleAction(draft.id, 'reject')}
                                disabled={isProcessing}
                            >
                                <X className="w-4 h-4 mr-2" /> Rejeitar
                            </Button>

                            <div className="flex gap-2">
                                {editingId === draft.id ? (
                                    <Button
                                        variant="secondary"
                                        onClick={() => setEditingId(null)}
                                        disabled={isProcessing}
                                    >
                                        Cancelar Edição
                                    </Button>
                                ) : null}

                                <Button
                                    className="bg-emerald-600 hover:bg-emerald-700 text-white"
                                    onClick={() => handleAction(draft.id, 'approve')}
                                    disabled={isProcessing}
                                >
                                    {isProcessing ? (
                                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                    ) : (
                                        <Check className="w-4 h-4 mr-2" />
                                    )}
                                    {editingId === draft.id ? "Salvar e Enviar" : "Aprovar e Enviar"}
                                </Button>
                            </div>
                        </CardFooter>
                    </Card>
                ))}
            </div>
        </div>
    );
}
