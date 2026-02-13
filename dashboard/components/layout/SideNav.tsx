"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
    LayoutDashboard,
    Activity,
    Users,
    Boxes,
    Newspaper
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
    label: string;
    icon: React.ElementType;
    href: string;
}

const navItems: NavItem[] = [
    { label: "SYS", icon: LayoutDashboard, href: "/" },
    { label: "WF", icon: Boxes, href: "/workflows" },
    { label: "NEWS", icon: Newspaper, href: "/news" },
    { label: "EXEC", icon: Activity, href: "/executions" },
    { label: "USERS", icon: Users, href: "/contacts" },
];

export function SideNav() {
    const pathname = usePathname();

    return (
        <>
            {/* Desktop: Left sidebar — terminal style */}
            <div className="hidden md:flex fixed left-0 top-0 z-40 h-screen w-16 border-r border-[#00FF41]/20 bg-black flex-col items-center py-4">
                {/* Brand */}
                <div className="mb-6 flex h-10 w-10 items-center justify-center border border-[#00FF41]/40 text-[#00FF41] text-xs font-bold">
                    MT
                </div>

                <nav className="flex-1 flex flex-col gap-1 w-full px-2">
                    {navItems.map((item) => {
                        const isActive = pathname === item.href;
                        return (
                            <Link
                                key={item.href}
                                href={item.href}
                                className={cn(
                                    "flex flex-col items-center justify-center gap-0.5 py-2 px-1 transition-all duration-150 text-[10px] font-medium uppercase tracking-wider",
                                    isActive
                                        ? "bg-[#00FF41]/10 text-[#00FF41] border-l-2 border-[#00FF41]"
                                        : "text-[#555] hover:text-[#00FF41]/70 hover:bg-[#00FF41]/5 border-l-2 border-transparent"
                                )}
                            >
                                <item.icon className="h-4 w-4" />
                                <span>{item.label}</span>
                            </Link>
                        );
                    })}
                </nav>

                {/* Version */}
                <div className="text-[8px] text-[#333] uppercase tracking-widest">
                    v2.0
                </div>
            </div>

            {/* Mobile: Bottom navigation bar */}
            <div className="md:hidden fixed bottom-0 left-0 right-0 z-40 border-t border-[#00FF41]/20 bg-black/95 backdrop-blur-sm safe-area-bottom">
                <nav className="flex items-center justify-around px-1 py-1.5">
                    {navItems.map((item) => {
                        const isActive = pathname === item.href;
                        return (
                            <Link
                                key={item.href}
                                href={item.href}
                                className={cn(
                                    "flex flex-col items-center justify-center gap-0.5 px-2 py-1 min-w-[48px] transition-all duration-150 text-[9px] font-medium uppercase tracking-wider",
                                    isActive
                                        ? "text-[#00FF41]"
                                        : "text-[#555] active:text-[#00FF41]/70"
                                )}
                            >
                                <item.icon className="h-4 w-4" />
                                <span>{item.label}</span>
                                {isActive && (
                                    <div className="absolute top-0 left-1/2 -translate-x-1/2 w-6 h-[2px] bg-[#00FF41]" />
                                )}
                            </Link>
                        );
                    })}
                </nav>
            </div>
        </>
    );
}
