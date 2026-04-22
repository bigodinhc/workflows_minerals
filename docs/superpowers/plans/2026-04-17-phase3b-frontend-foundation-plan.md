# Phase 3B: Frontend Foundation + Design System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Vite + React + Tailwind foundation, Telegram integration, design system components, and build pipeline so Phase 3C screens have everything they need.

**Architecture:** Standalone Vite SPA inside `webhook/mini-app/` with Tailwind v4 glassmorphism design tokens. Uses native Telegram WebApp API (no SDK dependency — the `<script>` tag injects `window.Telegram.WebApp`). SWR for data fetching with `initData` auth header. Multi-stage Docker build serves the Vite `dist/` as static files from aiohttp.

**Tech Stack:** React 19, Vite 6, TypeScript 5, Tailwind CSS v4 (@tailwindcss/vite), SWR 2, Vitest + React Testing Library

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `webhook/mini-app/package.json` | Dependencies and scripts |
| Create | `webhook/mini-app/vite.config.ts` | Vite + React + Tailwind plugins, dev proxy, vitest config |
| Create | `webhook/mini-app/tsconfig.json` | TypeScript strict config |
| Create | `webhook/mini-app/tsconfig.node.json` | Node config for vite.config.ts |
| Create | `webhook/mini-app/index.html` | SPA entry with Telegram WebApp script |
| Create | `webhook/mini-app/src/main.tsx` | React root + Telegram ready/expand |
| Create | `webhook/mini-app/src/App.tsx` | TabBar shell + placeholder pages |
| Create | `webhook/mini-app/src/index.css` | Tailwind import + @theme tokens + glass utilities |
| Create | `webhook/mini-app/src/vite-env.d.ts` | Vite client types |
| Create | `webhook/mini-app/src/telegram.d.ts` | Window.Telegram type declarations |
| Create | `webhook/mini-app/src/test-setup.ts` | Vitest + testing-library setup |
| Create | `webhook/mini-app/src/lib/types.ts` | TypeScript interfaces for all API responses |
| Create | `webhook/mini-app/src/lib/api.ts` | Fetch wrapper with initData header |
| Create | `webhook/mini-app/src/hooks/useTelegram.ts` | Thin hook over window.Telegram.WebApp |
| Create | `webhook/mini-app/src/hooks/useApi.ts` | SWR hook with auth |
| Create | `webhook/mini-app/src/components/GlassCard.tsx` | Glassmorphism container |
| Create | `webhook/mini-app/src/components/TabBar.tsx` | Bottom 3+1 navigation |
| Create | `webhook/mini-app/src/components/StatusDot.tsx` | Colored dot with glow |
| Create | `webhook/mini-app/src/components/Skeleton.tsx` | Loading placeholder |
| Create | `webhook/mini-app/src/components/Sparkline.tsx` | SVG trend line |
| Create | `webhook/mini-app/src/components/RingChart.tsx` | SVG donut |
| Create | `webhook/mini-app/src/components/FilterChips.tsx` | Status filter pills |
| Create | `webhook/mini-app/src/components/MenuRow.tsx` | "Mais" menu item |
| Create | `webhook/mini-app/src/components/__tests__/` | Component tests |
| Create | `webhook/routes/mini_static.py` | Serve Vite dist/ + SPA fallback |
| Modify | `webhook/bot/main.py:92-95` | Mount mini_static routes |
| Modify | `Dockerfile` | Multi-stage: Node build + Python runtime |
| Create | `.dockerignore` | Exclude node_modules, dist, .next |

---

### Task 1: Vite + React + TypeScript + Tailwind v4 Scaffolding + Design Tokens

**Files:**
- Create: `webhook/mini-app/package.json`
- Create: `webhook/mini-app/vite.config.ts`
- Create: `webhook/mini-app/tsconfig.json`
- Create: `webhook/mini-app/tsconfig.node.json`
- Create: `webhook/mini-app/index.html`
- Create: `webhook/mini-app/src/vite-env.d.ts`
- Create: `webhook/mini-app/src/index.css`
- Create: `webhook/mini-app/src/main.tsx`
- Create: `webhook/mini-app/src/App.tsx`
- Create: `webhook/mini-app/src/test-setup.ts`

- [ ] **Step 1: Create package.json**

Create `webhook/mini-app/package.json`:

```json
{
  "name": "supermustache-mini-app",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "swr": "^2.4.0"
  },
  "devDependencies": {
    "@tailwindcss/vite": "^4",
    "@testing-library/jest-dom": "^6.0.0",
    "@testing-library/react": "^16.0.0",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "@vitejs/plugin-react": "^4.3.0",
    "jsdom": "^26.0.0",
    "tailwindcss": "^4",
    "typescript": "^5.6.0",
    "vite": "^6.0.0",
    "vitest": "^3.0.0"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

Create `webhook/mini-app/vite.config.ts`:

```ts
/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "/mini/",
  plugins: [tailwindcss(), react()],
  server: {
    proxy: {
      "/api": "http://localhost:8080",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts",
  },
});
```

- [ ] **Step 3: Create TypeScript configs**

Create `webhook/mini-app/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true
  },
  "include": ["src"]
}
```

Create `webhook/mini-app/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 4: Create index.html**

Create `webhook/mini-app/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <title>SuperMustache</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create src type declarations**

Create `webhook/mini-app/src/vite-env.d.ts`:

```ts
/// <reference types="vite/client" />
```

- [ ] **Step 6: Create index.css with Tailwind + design tokens**

Create `webhook/mini-app/src/index.css`:

```css
@import "tailwindcss";

@theme {
  --color-bg: #09090b;
  --color-card: rgba(28, 28, 31, 0.7);
  --color-accent: #14b8a6;
  --color-accent-light: #5eead4;
  --color-success: #4ade80;
  --color-error: #f87171;
  --color-warning: #facc15;
  --color-text-primary: #fafafa;
  --color-text-secondary: #71717a;
  --color-text-muted: #52525b;
  --color-border: rgba(255, 255, 255, 0.04);
  --radius-card: 14px;
  --radius-hero: 20px;
  --radius-icon: 12px;
  --radius-chip: 20px;
}

body {
  margin: 0;
  background: var(--color-bg);
  color: var(--color-text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
  -webkit-tap-highlight-color: transparent;
}

/* Glassmorphism utility */
.glass {
  background: var(--color-card);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

/* Ambient glow behind hero */
.ambient-glow {
  background: radial-gradient(
    ellipse at 50% 0%,
    rgba(20, 184, 166, 0.06) 0%,
    transparent 70%
  );
}

/* Hide scrollbar for filter chips */
.scrollbar-none::-webkit-scrollbar {
  display: none;
}
.scrollbar-none {
  -ms-overflow-style: none;
  scrollbar-width: none;
}

/* Safe area for bottom tab bar */
.safe-bottom {
  padding-bottom: max(env(safe-area-inset-bottom, 0px), 8px);
}
```

- [ ] **Step 7: Create main.tsx and App.tsx**

Create `webhook/mini-app/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

window.Telegram?.WebApp?.ready();
window.Telegram?.WebApp?.expand();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

Create `webhook/mini-app/src/App.tsx`:

```tsx
export default function App() {
  return (
    <div className="min-h-screen bg-bg text-text-primary p-4">
      <h1 className="text-lg font-semibold">SuperMustache</h1>
      <p className="text-text-secondary text-sm mt-2">Mini App foundation ready.</p>
    </div>
  );
}
```

- [ ] **Step 8: Create test setup**

Create `webhook/mini-app/src/test-setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 9: Install dependencies and verify build**

Run:
```bash
cd webhook/mini-app && npm install
```
Expected: `node_modules/` created, no errors.

Run:
```bash
cd webhook/mini-app && npm run build
```
Expected: `dist/` created with `index.html` and `assets/` folder.

- [ ] **Step 10: Commit**

```bash
git add webhook/mini-app/
git commit -m "feat(mini-app): Vite + React + TypeScript + Tailwind v4 scaffolding with design tokens"
```

---

### Task 2: Telegram Types + API Client + SWR Hook

**Files:**
- Create: `webhook/mini-app/src/telegram.d.ts`
- Create: `webhook/mini-app/src/lib/types.ts`
- Create: `webhook/mini-app/src/lib/api.ts`
- Create: `webhook/mini-app/src/hooks/useTelegram.ts`
- Create: `webhook/mini-app/src/hooks/useApi.ts`

- [ ] **Step 1: Create Telegram type declarations**

Create `webhook/mini-app/src/telegram.d.ts`:

```ts
interface TelegramWebApp {
  initData: string;
  initDataUnsafe: {
    user?: {
      id: number;
      first_name: string;
      last_name?: string;
      username?: string;
    };
    auth_date: number;
    hash: string;
  };
  themeParams: {
    bg_color?: string;
    text_color?: string;
    hint_color?: string;
    link_color?: string;
    button_color?: string;
    button_text_color?: string;
    secondary_bg_color?: string;
  };
  colorScheme: "light" | "dark";
  ready: () => void;
  expand: () => void;
  close: () => void;
  HapticFeedback: {
    impactOccurred: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
    notificationOccurred: (type: "error" | "success" | "warning") => void;
    selectionChanged: () => void;
  };
  showPopup: (
    params: {
      title?: string;
      message: string;
      buttons?: Array<{ id?: string; type?: string; text?: string }>;
    },
    callback?: (id: string) => void,
  ) => void;
  MainButton: {
    text: string;
    isVisible: boolean;
    show: () => void;
    hide: () => void;
    onClick: (cb: () => void) => void;
    offClick: (cb: () => void) => void;
    setText: (text: string) => void;
    enable: () => void;
    disable: () => void;
  };
  BackButton: {
    isVisible: boolean;
    show: () => void;
    hide: () => void;
    onClick: (cb: () => void) => void;
    offClick: (cb: () => void) => void;
  };
}

interface Window {
  Telegram?: {
    WebApp: TelegramWebApp;
  };
}
```

- [ ] **Step 2: Create API response types**

Create `webhook/mini-app/src/lib/types.ts`:

```ts
export interface WorkflowRun {
  conclusion: string | null;
  created_at: string;
}

export interface WorkflowLastRun {
  status: string;
  conclusion: string | null;
  created_at: string;
  duration_seconds: number | null;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  icon: string;
  last_run: WorkflowLastRun | null;
  health_pct: number;
  recent_runs: WorkflowRun[];
}

export interface WorkflowsResponse {
  workflows: Workflow[];
}

export interface RunDetail {
  id: number;
  status: string;
  conclusion: string | null;
  created_at: string;
  duration_seconds: number | null;
  error: string | null;
  html_url: string;
}

export interface RunsResponse {
  runs: RunDetail[];
}

export interface NewsItem {
  id: string;
  title: string;
  source: string;
  source_feed: string;
  date: string;
  status: "pending" | "archived" | "rejected";
  preview_url: string | null;
}

export interface NewsResponse {
  items: NewsItem[];
  total: number;
  page: number;
}

export interface NewsDetail {
  id: string;
  title: string;
  source: string;
  source_feed: string;
  date: string;
  status: string;
  fullText: string;
  tables: Array<{ header: string[]; rows: string[][] }>;
  preview_url: string;
}

export interface Report {
  id: string;
  report_name: string;
  date_key: string;
  download_url: string;
}

export interface ReportsResponse {
  reports: Report[];
}

export interface Contact {
  name: string;
  phone: string;
  active: boolean;
}

export interface ContactsResponse {
  contacts: Contact[];
  total: number;
  page: number;
}

export interface Stats {
  health_pct: number;
  workflows_ok: number;
  workflows_total: number;
  runs_today: number;
  contacts_active: number;
  news_today: number;
}
```

- [ ] **Step 3: Create API fetch client**

Create `webhook/mini-app/src/lib/api.ts`:

```ts
const BASE_URL = import.meta.env.VITE_API_URL || "";

export async function apiFetch<T>(
  path: string,
  initData: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "X-Telegram-Init-Data": initData,
      ...options?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText}`);
  }
  return response.json();
}
```

- [ ] **Step 4: Create useTelegram hook**

Create `webhook/mini-app/src/hooks/useTelegram.ts`:

```ts
function getWebApp() {
  return window.Telegram?.WebApp ?? null;
}

export function useTelegram() {
  const webApp = getWebApp();

  return {
    webApp,
    initData: webApp?.initData ?? "",
    user: webApp?.initDataUnsafe?.user ?? null,
    colorScheme: webApp?.colorScheme ?? "dark",
    haptic: webApp?.HapticFeedback ?? null,
    mainButton: webApp?.MainButton ?? null,
    backButton: webApp?.BackButton ?? null,
    showPopup: webApp?.showPopup?.bind(webApp) ?? null,
  };
}
```

- [ ] **Step 5: Create useApi SWR hook**

Create `webhook/mini-app/src/hooks/useApi.ts`:

```ts
import useSWR, { type SWRConfiguration } from "swr";
import { useTelegram } from "./useTelegram";
import { apiFetch } from "../lib/api";

export function useApi<T>(path: string | null, config?: SWRConfiguration<T>) {
  const { initData } = useTelegram();

  return useSWR<T>(
    path && initData ? path : null,
    (url: string) => apiFetch<T>(url, initData),
    {
      revalidateOnFocus: false,
      ...config,
    },
  );
}
```

- [ ] **Step 6: Verify TypeScript compiles**

Run:
```bash
cd webhook/mini-app && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add webhook/mini-app/src/
git commit -m "feat(mini-app): Telegram types, API client, useTelegram + useApi hooks"
```

---

### Task 3: Layout Components + Tests

**Files:**
- Create: `webhook/mini-app/src/components/GlassCard.tsx`
- Create: `webhook/mini-app/src/components/TabBar.tsx`
- Create: `webhook/mini-app/src/components/StatusDot.tsx`
- Create: `webhook/mini-app/src/components/Skeleton.tsx`
- Create: `webhook/mini-app/src/components/__tests__/GlassCard.test.tsx`
- Create: `webhook/mini-app/src/components/__tests__/TabBar.test.tsx`
- Create: `webhook/mini-app/src/components/__tests__/StatusDot.test.tsx`
- Create: `webhook/mini-app/src/components/__tests__/Skeleton.test.tsx`

- [ ] **Step 1: Write failing tests for layout components**

Create `webhook/mini-app/src/components/__tests__/GlassCard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { GlassCard } from "../GlassCard";

test("renders children", () => {
  render(<GlassCard>Hello</GlassCard>);
  expect(screen.getByText("Hello")).toBeInTheDocument();
});

test("applies glass class", () => {
  const { container } = render(<GlassCard>Content</GlassCard>);
  expect(container.firstChild).toHaveClass("glass");
});

test("merges custom className", () => {
  const { container } = render(<GlassCard className="p-4">Content</GlassCard>);
  expect(container.firstChild).toHaveClass("p-4");
});
```

Create `webhook/mini-app/src/components/__tests__/TabBar.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { TabBar } from "../TabBar";

test("renders all 4 tabs", () => {
  render(<TabBar activeTab="home" onTabChange={() => {}} />);
  expect(screen.getByText("Home")).toBeInTheDocument();
  expect(screen.getByText("Workflows")).toBeInTheDocument();
  expect(screen.getByText("News")).toBeInTheDocument();
  expect(screen.getByText("Mais")).toBeInTheDocument();
});

test("calls onTabChange when tab clicked", () => {
  const onChange = vi.fn();
  render(<TabBar activeTab="home" onTabChange={onChange} />);
  fireEvent.click(screen.getByText("News"));
  expect(onChange).toHaveBeenCalledWith("news");
});

test("highlights active tab with accent color class", () => {
  render(<TabBar activeTab="workflows" onTabChange={() => {}} />);
  const btn = screen.getByText("Workflows").closest("button")!;
  expect(btn.className).toContain("text-accent");
});
```

Create `webhook/mini-app/src/components/__tests__/StatusDot.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { StatusDot } from "../StatusDot";

test("renders with success color", () => {
  const { container } = render(<StatusDot status="success" />);
  const dot = container.firstChild as HTMLElement;
  expect(dot.style.backgroundColor).toBe("rgb(74, 222, 128)");
});

test("renders with error color", () => {
  const { container } = render(<StatusDot status="error" />);
  const dot = container.firstChild as HTMLElement;
  expect(dot.style.backgroundColor).toBe("rgb(248, 113, 113)");
});

test("applies custom size", () => {
  const { container } = render(<StatusDot status="success" size={12} />);
  const dot = container.firstChild as HTMLElement;
  expect(dot.style.width).toBe("12px");
});
```

Create `webhook/mini-app/src/components/__tests__/Skeleton.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { Skeleton } from "../Skeleton";

test("renders with animate-pulse class", () => {
  const { container } = render(<Skeleton />);
  expect(container.firstChild).toHaveClass("animate-pulse");
});

test("merges custom className", () => {
  const { container } = render(<Skeleton className="h-4 w-20" />);
  expect(container.firstChild).toHaveClass("h-4");
  expect(container.firstChild).toHaveClass("w-20");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webhook/mini-app && npx vitest run`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement layout components**

Create `webhook/mini-app/src/components/GlassCard.tsx`:

```tsx
interface GlassCardProps {
  children: React.ReactNode;
  className?: string;
}

export function GlassCard({ children, className = "" }: GlassCardProps) {
  return (
    <div className={`glass rounded-card border border-border ${className}`}>
      {children}
    </div>
  );
}
```

Create `webhook/mini-app/src/components/TabBar.tsx`:

```tsx
interface Tab {
  id: string;
  label: string;
  icon: string;
}

const TABS: Tab[] = [
  { id: "home", label: "Home", icon: "\uD83C\uDFE0" },
  { id: "workflows", label: "Workflows", icon: "\u26A1" },
  { id: "news", label: "News", icon: "\uD83D\uDCF0" },
  { id: "more", label: "Mais", icon: "\u2022\u2022\u2022" },
];

interface TabBarProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
}

export function TabBar({ activeTab, onTabChange }: TabBarProps) {
  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-[rgba(9,9,11,0.95)] backdrop-blur-[20px] border-t border-border safe-bottom z-50">
      <div className="flex justify-around py-2">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`relative flex flex-col items-center gap-0.5 min-w-[64px] min-h-[44px] justify-center ${
              activeTab === tab.id
                ? "text-accent"
                : "text-text-muted opacity-40"
            }`}
          >
            {activeTab === tab.id && (
              <div className="absolute top-0 w-8 h-0.5 bg-accent rounded-full" />
            )}
            <span className="text-xl leading-none">{tab.icon}</span>
            <span className="text-[10px]">{tab.label}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}
```

Create `webhook/mini-app/src/components/StatusDot.tsx`:

```tsx
const STATUS_COLORS: Record<string, string> = {
  success: "#4ade80",
  error: "#f87171",
  warning: "#facc15",
  running: "#14b8a6",
};

interface StatusDotProps {
  status: "success" | "error" | "warning" | "running";
  size?: number;
}

export function StatusDot({ status, size = 8 }: StatusDotProps) {
  const color = STATUS_COLORS[status];
  return (
    <span
      className="inline-block rounded-full"
      style={{
        width: size,
        height: size,
        backgroundColor: color,
        boxShadow: `0 0 ${size}px ${color}4D`,
      }}
    />
  );
}
```

Create `webhook/mini-app/src/components/Skeleton.tsx`:

```tsx
interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className = "" }: SkeletonProps) {
  return (
    <div className={`animate-pulse bg-white/5 rounded-card ${className}`} />
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webhook/mini-app && npx vitest run`
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add webhook/mini-app/src/components/
git commit -m "feat(mini-app): layout components — GlassCard, TabBar, StatusDot, Skeleton"
```

---

### Task 4: Data Visualization Components + Tests

**Files:**
- Create: `webhook/mini-app/src/components/Sparkline.tsx`
- Create: `webhook/mini-app/src/components/RingChart.tsx`
- Create: `webhook/mini-app/src/components/FilterChips.tsx`
- Create: `webhook/mini-app/src/components/MenuRow.tsx`
- Create: `webhook/mini-app/src/components/__tests__/Sparkline.test.tsx`
- Create: `webhook/mini-app/src/components/__tests__/RingChart.test.tsx`
- Create: `webhook/mini-app/src/components/__tests__/FilterChips.test.tsx`
- Create: `webhook/mini-app/src/components/__tests__/MenuRow.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `webhook/mini-app/src/components/__tests__/Sparkline.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { Sparkline } from "../Sparkline";

test("renders SVG with polyline", () => {
  const { container } = render(<Sparkline data={[1, 3, 2, 5, 4]} />);
  expect(container.querySelector("svg")).toBeInTheDocument();
  expect(container.querySelector("polyline")).toBeInTheDocument();
});

test("renders nothing with fewer than 2 data points", () => {
  const { container } = render(<Sparkline data={[1]} />);
  expect(container.querySelector("svg")).toBeNull();
});

test("polyline has correct number of coordinate pairs", () => {
  const { container } = render(<Sparkline data={[1, 2, 3]} />);
  const points = container.querySelector("polyline")!.getAttribute("points")!;
  expect(points.split(" ").length).toBe(3);
});
```

Create `webhook/mini-app/src/components/__tests__/RingChart.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { RingChart } from "../RingChart";

test("renders SVG with two circles", () => {
  const { container } = render(<RingChart value={3} total={5} />);
  const circles = container.querySelectorAll("circle");
  expect(circles.length).toBe(2);
});

test("background circle has low-opacity stroke", () => {
  const { container } = render(<RingChart value={3} total={5} />);
  const bg = container.querySelectorAll("circle")[0];
  expect(bg.getAttribute("stroke")).toBe("rgba(255,255,255,0.06)");
});

test("progress circle uses accent color by default", () => {
  const { container } = render(<RingChart value={3} total={5} />);
  const progress = container.querySelectorAll("circle")[1];
  expect(progress.getAttribute("stroke")).toBe("#14b8a6");
});
```

Create `webhook/mini-app/src/components/__tests__/FilterChips.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { FilterChips } from "../FilterChips";

const OPTIONS = [
  { id: "all", label: "Todos" },
  { id: "pending", label: "Pendentes" },
  { id: "archived", label: "Arquivados" },
];

test("renders all options", () => {
  render(<FilterChips options={OPTIONS} active="all" onChange={() => {}} />);
  expect(screen.getByText("Todos")).toBeInTheDocument();
  expect(screen.getByText("Pendentes")).toBeInTheDocument();
  expect(screen.getByText("Arquivados")).toBeInTheDocument();
});

test("calls onChange with option id", () => {
  const onChange = vi.fn();
  render(<FilterChips options={OPTIONS} active="all" onChange={onChange} />);
  fireEvent.click(screen.getByText("Pendentes"));
  expect(onChange).toHaveBeenCalledWith("pending");
});

test("active chip has accent styling", () => {
  render(<FilterChips options={OPTIONS} active="pending" onChange={() => {}} />);
  const btn = screen.getByText("Pendentes");
  expect(btn.className).toContain("text-accent");
});
```

Create `webhook/mini-app/src/components/__tests__/MenuRow.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { MenuRow } from "../MenuRow";

test("renders icon, title, and subtitle", () => {
  render(
    <MenuRow
      icon="\uD83D\uDCCA"
      title="Reports"
      subtitle="PDFs Platts"
      onClick={() => {}}
    />,
  );
  expect(screen.getByText("\uD83D\uDCCA")).toBeInTheDocument();
  expect(screen.getByText("Reports")).toBeInTheDocument();
  expect(screen.getByText("PDFs Platts")).toBeInTheDocument();
});

test("calls onClick when clicked", () => {
  const onClick = vi.fn();
  render(
    <MenuRow icon="\uD83D\uDCCA" title="Reports" subtitle="PDFs" onClick={onClick} />,
  );
  fireEvent.click(screen.getByText("Reports").closest("button")!);
  expect(onClick).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webhook/mini-app && npx vitest run`
Expected: new tests FAIL — modules not found.

- [ ] **Step 3: Implement data visualization components**

Create `webhook/mini-app/src/components/Sparkline.tsx`:

```tsx
interface SparklineProps {
  data: number[];
  color?: string;
  width?: number;
  height?: number;
}

export function Sparkline({
  data,
  color = "#14b8a6",
  width = 60,
  height = 12,
}: SparklineProps) {
  if (data.length < 2) return null;

  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;

  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - min) / range) * height;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
```

Create `webhook/mini-app/src/components/RingChart.tsx`:

```tsx
interface RingChartProps {
  value: number;
  total: number;
  size?: number;
  strokeWidth?: number;
  color?: string;
}

export function RingChart({
  value,
  total,
  size = 36,
  strokeWidth = 3,
  color = "#14b8a6",
}: RingChartProps) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = total > 0 ? (value / total) * circumference : 0;

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="rgba(255,255,255,0.06)"
        strokeWidth={strokeWidth}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={circumference}
        strokeDashoffset={circumference - progress}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}
```

Create `webhook/mini-app/src/components/FilterChips.tsx`:

```tsx
interface FilterChipOption {
  id: string;
  label: string;
}

interface FilterChipsProps {
  options: FilterChipOption[];
  active: string;
  onChange: (id: string) => void;
}

export function FilterChips({ options, active, onChange }: FilterChipsProps) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-none">
      {options.map((opt) => (
        <button
          key={opt.id}
          onClick={() => onChange(opt.id)}
          className={`px-3 py-1.5 rounded-chip text-xs whitespace-nowrap transition-colors ${
            active === opt.id
              ? "bg-accent/20 text-accent border border-accent/30"
              : "bg-white/5 text-text-secondary border border-border"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
```

Create `webhook/mini-app/src/components/MenuRow.tsx`:

```tsx
interface MenuRowProps {
  icon: string;
  title: string;
  subtitle: string;
  onClick: () => void;
}

export function MenuRow({ icon, title, subtitle, onClick }: MenuRowProps) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-3 p-4 glass rounded-card border border-border text-left"
    >
      <span className="text-2xl">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-text-primary text-sm font-medium">{title}</div>
        <div className="text-text-secondary text-xs">{subtitle}</div>
      </div>
      <span className="text-text-muted text-lg">{"\u203A"}</span>
    </button>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webhook/mini-app && npx vitest run`
Expected: all tests PASS (layout + data viz).

- [ ] **Step 5: Verify build still works**

Run: `cd webhook/mini-app && npm run build`
Expected: builds successfully.

- [ ] **Step 6: Commit**

```bash
git add webhook/mini-app/src/components/
git commit -m "feat(mini-app): data viz components — Sparkline, RingChart, FilterChips, MenuRow"
```

---

### Task 5: App Shell + Static Serving + Dockerfile

**Files:**
- Modify: `webhook/mini-app/src/App.tsx`
- Create: `webhook/routes/mini_static.py`
- Modify: `webhook/bot/main.py:92-95`
- Modify: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Update App.tsx with TabBar navigation**

Replace `webhook/mini-app/src/App.tsx`:

```tsx
import { useState } from "react";
import { TabBar } from "./components/TabBar";

export default function App() {
  const [activeTab, setActiveTab] = useState("home");

  return (
    <div className="min-h-screen bg-bg text-text-primary">
      <main className="p-4 pb-20">
        {activeTab === "home" && (
          <div className="text-center pt-12">
            <h1 className="text-lg font-semibold">SuperMustache</h1>
            <p className="text-text-secondary text-sm mt-2">Home — Phase 3C</p>
          </div>
        )}
        {activeTab === "workflows" && (
          <div className="text-center pt-12">
            <p className="text-text-secondary text-sm">Workflows — Phase 3C</p>
          </div>
        )}
        {activeTab === "news" && (
          <div className="text-center pt-12">
            <p className="text-text-secondary text-sm">News — Phase 3C</p>
          </div>
        )}
        {activeTab === "more" && (
          <div className="text-center pt-12">
            <p className="text-text-secondary text-sm">Mais — Phase 3C</p>
          </div>
        )}
      </main>
      <TabBar activeTab={activeTab} onTabChange={setActiveTab} />
    </div>
  );
}
```

- [ ] **Step 2: Build the mini-app**

Run:
```bash
cd webhook/mini-app && npm run build
```
Expected: `dist/` created with `index.html` and `assets/`.

- [ ] **Step 3: Create static file server**

Create `webhook/routes/mini_static.py`:

```python
"""Serve Telegram Mini App static files from Vite dist/.

SPA fallback: any path under /mini/ that isn't a real file returns index.html.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

_DIST_DIR = Path(__file__).resolve().parent.parent / "mini-app" / "dist"

routes = web.RouteTableDef()


@routes.get("/mini/{path:.*}")
async def serve_mini_app(request: web.Request) -> web.Response:
    path = request.match_info.get("path", "")

    if path:
        file_path = (_DIST_DIR / path).resolve()
        if file_path.is_file() and str(file_path).startswith(str(_DIST_DIR)):
            return web.FileResponse(file_path)

    index = _DIST_DIR / "index.html"
    if index.is_file():
        return web.FileResponse(index)

    return web.Response(text="Mini App not built. Run: cd webhook/mini-app && npm run build", status=404)
```

- [ ] **Step 4: Mount static routes in main.py**

In `webhook/bot/main.py`, add import after line 36:

```python
from routes.mini_static import routes as mini_static_routes
```

In `create_app()`, add after `app.router.add_routes(mini_api_routes)` (line 95):

```python
    app.router.add_routes(mini_static_routes)
```

- [ ] **Step 5: Create .dockerignore**

Create `.dockerignore` at project root:

```
.git
.worktrees
.next
dashboard/node_modules
dashboard/.next
webhook/mini-app/node_modules
webhook/mini-app/dist
*.pyc
__pycache__
.pytest_cache
.env
```

- [ ] **Step 6: Update Dockerfile with multi-stage build**

Replace `Dockerfile`:

```dockerfile
# ── Stage 1: Build Mini App frontend ──
FROM node:20-slim AS frontend
WORKDIR /build
COPY webhook/mini-app/package.json webhook/mini-app/package-lock.json* ./
RUN npm install
COPY webhook/mini-app/ ./
RUN npm run build

# ── Stage 2: Python runtime ──
FROM python:3.11-slim

WORKDIR /app

COPY webhook/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY webhook/ ./webhook/
COPY execution/ ./execution/
COPY .github/workflows/ ./.github/workflows/

# Copy built frontend from stage 1
COPY --from=frontend /build/dist ./webhook/mini-app/dist/

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "webhook.bot.main"]
```

- [ ] **Step 7: Verify static serving works locally**

Run:
```bash
cd webhook/mini-app && npm run build
```

Then (in another terminal or same):
```bash
cd webhook && python -c "
from bot.main import create_app
from aiohttp import web
app = create_app()
print('Routes:')
for r in app.router.routes():
    info = r.get_info()
    path = info.get('path') or info.get('formatter', '')
    print(f'  {r.method} {path}')
" 2>&1 | grep mini
```
Expected: should show `/mini/{path}` route registered.

- [ ] **Step 8: Commit**

```bash
git add webhook/mini-app/src/App.tsx webhook/routes/mini_static.py webhook/bot/main.py Dockerfile .dockerignore
git commit -m "feat(mini-app): app shell + static serving + multi-stage Docker build"
```
