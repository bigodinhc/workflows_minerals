"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
    LayoutDashboard,
    Activity,
    Users,
    Zap,
    Boxes,
    Newspaper
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip"

interface NavItem {
    label: string;
    icon: React.ElementType;
    href: string;
}

const navItems: NavItem[] = [
    { label: "Dashboard", icon: LayoutDashboard, href: "/" },
    { label: "Workflows", icon: Boxes, href: "/workflows" },
    { label: "News", icon: Newspaper, href: "/news" },
    { label: "Executions", icon: Activity, href: "/executions" },
    { label: "Contacts", icon: Users, href: "/contacts" },
];

export function SideNav() {
    const pathname = usePathname();

    return (
        <>
            {/* Desktop: Left sidebar */}
            <div className="hidden md:flex fixed left-0 top-0 z-40 h-screen w-16 border-r border-sidebar-border bg-sidebar flex-col items-center py-6">
                <div className="mb-8 flex h-10 w-10 items-center justify-center rounded-xl bg-primary/20 text-primary ring-1 ring-primary/50 shadow-[0_0_15px_-3px_var(--primary)]">
                    <Zap className="h-6 w-6" />
                </div>

                <nav className="flex-1 flex flex-col gap-4 w-full px-2">
                    <TooltipProvider delayDuration={0}>
                        {navItems.map((item) => {
                            const isActive = pathname === item.href;
                            return (
                                <Tooltip key={item.href}>
                                    <TooltipTrigger asChild>
                                        <Link
                                            href={item.href}
                                            className={cn(
                                                "relative flex h-10 w-10 items-center justify-center rounded-lg transition-all duration-300 group mx-auto",
                                                isActive
                                                    ? "bg-sidebar-accent text-primary shadow-[0_0_10px_-4px_var(--primary)]"
                                                    : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground"
                                            )}
                                        >
                                            {isActive && (
                                                <motion.div
                                                    layoutId="activeNav"
                                                    className="absolute inset-0 rounded-lg bg-primary/10"
                                                    transition={{ type: "spring", stiffness: 300, damping: 30 }}
                                                />
                                            )}
                                            <item.icon className="h-5 w-5" />
                                        </Link>
                                    </TooltipTrigger>
                                    <TooltipContent side="right" className="bg-popover text-popover-foreground border-border">
                                        {item.label}
                                    </TooltipContent>
                                </Tooltip>
                            );
                        })}
                    </TooltipProvider>
                </nav>
            </div>

            {/* Mobile: Bottom navigation bar */}
            <div className="md:hidden fixed bottom-0 left-0 right-0 z-40 border-t border-sidebar-border bg-sidebar/95 backdrop-blur-lg safe-area-bottom">
                <nav className="flex items-center justify-around px-2 py-2">
                    {navItems.map((item) => {
                        const isActive = pathname === item.href;
                        return (
                            <Link
                                key={item.href}
                                href={item.href}
                                className={cn(
                                    "relative flex flex-col items-center justify-center gap-1 px-3 py-1.5 rounded-lg transition-all duration-200 min-w-[56px]",
                                    isActive
                                        ? "text-primary"
                                        : "text-muted-foreground active:text-foreground"
                                )}
                            >
                                {isActive && (
                                    <motion.div
                                        layoutId="activeNavMobile"
                                        className="absolute inset-0 rounded-lg bg-primary/10"
                                        transition={{ type: "spring", stiffness: 300, damping: 30 }}
                                    />
                                )}
                                <item.icon className="h-5 w-5 relative z-10" />
                                <span className="text-[10px] font-medium relative z-10">{item.label}</span>
                            </Link>
                        );
                    })}
                </nav>
            </div>
        </>
    );
}
