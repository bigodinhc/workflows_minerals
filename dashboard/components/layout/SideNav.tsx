"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
    LayoutDashboard,
    Activity,
    Users,
    Settings,
    LogOut,
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
    { label: "News Review", icon: Newspaper, href: "/news" },
    { label: "Executions", icon: Activity, href: "/executions" },
    { label: "Contacts", icon: Users, href: "/contacts" },
];

export function SideNav() {
    const pathname = usePathname();

    return (
        <div className="fixed left-0 top-0 z-40 h-screen w-16 border-r border-sidebar-border bg-sidebar flex flex-col items-center py-6">
            {/* Brand Logo */}
            <div className="mb-8 flex h-10 w-10 items-center justify-center rounded-xl bg-primary/20 text-primary ring-1 ring-primary/50 shadow-[0_0_15px_-3px_var(--primary)]">
                <Zap className="h-6 w-6" />
            </div>

            {/* Navigation */}
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

            {/* Bottom Actions */}
            <div className="mt-auto flex flex-col gap-4 px-2">
                <TooltipProvider delayDuration={0}>
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <button className="flex h-10 w-10 items-center justify-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors">
                                <LogOut className="h-5 w-5" />
                            </button>
                        </TooltipTrigger>
                        <TooltipContent side="right">Logout</TooltipContent>
                    </Tooltip>
                </TooltipProvider>
            </div>
        </div>
    );
}
