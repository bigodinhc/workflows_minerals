import type { Metadata } from "next";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/700.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "MINERALS TRADING // WORKFLOWS",
  description: "Sistema de automação de workflows de trading",
};

import { SideNav } from "@/components/layout/SideNav";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
      </head>
      <body className="antialiased bg-background text-foreground" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
        <SideNav />
        <main className="md:pl-16 pb-20 md:pb-0 min-h-screen">
          {children}
        </main>
      </body>
    </html>
  );
}
