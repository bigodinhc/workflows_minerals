"use client";

import { Search, Users, Loader2 } from "lucide-react";
import useSWR from "swr";
import { useState } from "react";

const fetcher = (url: string) => fetch(url).then((res) => res.json());

export default function ContactsPage() {
    const { data: contacts, error, isLoading } = useSWR("/api/contacts", fetcher);
    const [search, setSearch] = useState("");

    const filteredContacts = contacts?.filter((contact: any) => {
        const searchStr = Object.values(contact).join(" ").toLowerCase();
        return searchStr.includes(search.toLowerCase());
    }) || [];

    return (
        <div className="p-4 md:p-6 space-y-6 bg-black text-[#e0e0e0] min-h-screen">
            {/* Header */}
            <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
                <div>
                    <p className="text-[10px] text-[#00FF41] uppercase tracking-[0.3em] mb-1">/ CONTACTS</p>
                    <h1 className="text-xl md:text-2xl font-bold uppercase tracking-tight text-white">
                        DISTRIBUTION LIST
                    </h1>
                    <p className="text-[10px] text-[#555] mt-1 uppercase">WHATSAPP REPORT RECIPIENTS</p>
                </div>

                {/* Search */}
                <div className="relative w-full md:w-80">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[10px] text-[#00FF41]/50 font-mono">grep:</span>
                    <input
                        placeholder="search..."
                        className="w-full bg-[#0a0a0a] border border-[#1a1a1a] text-xs text-white pl-12 pr-3 py-2 font-mono
                          focus:outline-none focus:border-[#00FF41]/30 transition-colors placeholder:text-[#333] uppercase"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                </div>
            </div>

            {/* Count */}
            <div className="flex items-center gap-3">
                <span className="text-[10px] text-[#555] uppercase tracking-wider">
                    {isLoading ? "..." : filteredContacts.length} RECORDS
                </span>
                <div className="flex-1 h-px bg-[#1a1a1a]"></div>
                <span className="text-[9px] text-[#333] uppercase">SYNCED FROM GOOGLE SHEETS</span>
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
                    <div className="overflow-x-auto">
                        <table className="w-full">
                            <thead>
                                <tr className="border-b border-[#1a1a1a] bg-[#050505]">
                                    <th className="text-left px-4 py-2 text-[9px] font-medium text-[#555] uppercase tracking-wider w-[60px]">#</th>
                                    {contacts && contacts.length > 0 && Object.keys(contacts[0]).filter(k => k !== 'id').map((header) => (
                                        <th key={header} className="text-left px-4 py-2 text-[9px] font-medium text-[#555] uppercase tracking-wider">
                                            {header}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {filteredContacts.map((contact: any) => (
                                    <tr key={contact.id} className="border-b border-[#0a0a0a] last:border-0 hover:bg-[#00FF41]/5 transition-colors">
                                        <td className="px-4 py-2 font-mono text-[10px] text-[#333]">{contact.id}</td>
                                        {Object.keys(contacts[0]).filter(k => k !== 'id').map((key) => (
                                            <td key={`${contact.id}-${key}`} className="px-4 py-2 text-[11px] text-[#999]">
                                                {contact[key]}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
