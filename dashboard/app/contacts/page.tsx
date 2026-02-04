"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Search, Users, Loader2 } from "lucide-react";
import useSWR from "swr";
import { useState } from "react";

const fetcher = (url: string) => fetch(url).then((res) => res.json());

export default function ContactsPage() {
    const { data: contacts, error, isLoading } = useSWR("/api/contacts", fetcher);
    const [search, setSearch] = useState("");

    const filteredContacts = contacts?.filter((contact: any) => {
        // Create searchable string from all values
        const searchStr = Object.values(contact).join(" ").toLowerCase();
        return searchStr.includes(search.toLowerCase());
    }) || [];

    return (
        <div className="p-8 space-y-8 bg-background text-foreground min-h-screen">
            {/* Header */}
            <div className="flex flex-col md:flex-row md:items-center md:justify-between py-4 gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight text-white/90">Contatos</h1>
                    <p className="text-muted-foreground mt-1">Gerencie a lista de distribuição do relatório.</p>
                </div>

                <div className="relative w-full md:w-96">
                    <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Buscar por nome, telefone..."
                        className="pl-8 bg-card border-border"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                </div>
            </div>

            {/* Main Content */}
            <Card className="bg-card border-border">
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <CardTitle>Base de Contatos</CardTitle>
                            <Badge variant="secondary" className="ml-2 font-mono">
                                {isLoading ? "..." : filteredContacts.length} total
                            </Badge>
                        </div>
                        <Users className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <CardDescription>
                        Dados sincronizados da planilha Google Sheets.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {isLoading ? (
                        <div className="flex items-center justify-center py-20 text-muted-foreground">
                            <Loader2 className="h-8 w-8 animate-spin" />
                        </div>
                    ) : error ? (
                        <div className="flex items-center justify-center py-20 text-red-400">
                            Erro ao carregar dados: {error.message || "Verifique o console"}
                        </div>
                    ) : (
                        <div className="rounded-md border border-border">
                            <Table>
                                <TableHeader>
                                    <TableRow className="border-border hover:bg-muted/50">
                                        <TableHead className="w-[100px]">Linha</TableHead>
                                        {contacts && contacts.length > 0 && Object.keys(contacts[0]).filter(k => k !== 'id').map((header) => (
                                            <TableHead key={header} className="capitalize">{header}</TableHead>
                                        ))}
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filteredContacts.map((contact: any) => (
                                        <TableRow key={contact.id} className="border-border hover:bg-muted/50">
                                            <TableCell className="font-mono text-xs text-muted-foreground">#{contact.id}</TableCell>
                                            {Object.keys(contacts[0]).filter(k => k !== 'id').map((key) => (
                                                <TableCell key={`${contact.id}-${key}`}>
                                                    {contact[key]}
                                                </TableCell>
                                            ))}
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
