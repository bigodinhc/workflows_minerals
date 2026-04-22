# Phase 3C: Screens + Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 5 actual screens (Home, Workflows, News, Reports, Contacts) with real API data, connecting Phase 3B components to Phase 3A endpoints, with haptic feedback, BackButton navigation, showPopup confirmation, and infinite scroll.

**Architecture:** State-based navigation (no router — YAGNI for a tab app). Each page is a lazy-loaded component that uses `useApi` to fetch data and composes Phase 3B design system components. BackButton manages sub-page navigation. Reports uses internal drill-down state. News uses `useSWRInfinite` for infinite scroll pagination.

**Tech Stack:** React 19 (lazy/Suspense), SWR 2 (useSWRInfinite), Telegram WebApp API (hapticFeedback, showPopup, BackButton), existing Phase 3B components

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `webhook/mini-app/src/lib/format.ts` | formatRelativeTime, formatDuration helpers |
| Create | `webhook/mini-app/src/hooks/useNavigation.ts` | Tab + sub-page state, BackButton wiring |
| Create | `webhook/mini-app/src/hooks/useInfiniteScroll.ts` | IntersectionObserver for infinite scroll |
| Create | `webhook/mini-app/src/pages/Home.tsx` | Hero card + workflow overview |
| Create | `webhook/mini-app/src/pages/Workflows.tsx` | Expandable workflow cards + trigger |
| Create | `webhook/mini-app/src/pages/News.tsx` | Filter chips + news list + infinite scroll |
| Create | `webhook/mini-app/src/pages/NewsDetail.tsx` | Full article view |
| Create | `webhook/mini-app/src/pages/More.tsx` | Menu linking to Reports, Contacts, Settings |
| Create | `webhook/mini-app/src/pages/Reports.tsx` | Type → list → download drill-down |
| Create | `webhook/mini-app/src/pages/Contacts.tsx` | Search + list + toggle |
| Modify | `webhook/mini-app/src/App.tsx` | Replace placeholders with lazy-loaded pages + navigation |
| Create | `webhook/mini-app/src/lib/__tests__/format.test.ts` | Format helper tests |
| Create | `webhook/mini-app/src/pages/__tests__/Home.test.tsx` | Home render test |
| Create | `webhook/mini-app/src/pages/__tests__/Workflows.test.tsx` | Workflows render test |
| Create | `webhook/mini-app/src/pages/__tests__/News.test.tsx` | News render test |

---

### Task 1: Navigation System + Helpers

**Files:**
- Create: `webhook/mini-app/src/lib/format.ts`
- Create: `webhook/mini-app/src/hooks/useNavigation.ts`
- Create: `webhook/mini-app/src/hooks/useInfiniteScroll.ts`
- Modify: `webhook/mini-app/src/App.tsx`
- Create: `webhook/mini-app/src/lib/__tests__/format.test.ts`

- [ ] **Step 1: Write format helper tests**

Create `webhook/mini-app/src/lib/__tests__/format.test.ts`:

```ts
import { formatRelativeTime, formatDuration } from "../format";

describe("formatRelativeTime", () => {
  test("returns 'agora' for recent timestamps", () => {
    const now = new Date().toISOString();
    expect(formatRelativeTime(now)).toBe("agora");
  });

  test("returns minutes for < 1 hour", () => {
    const date = new Date(Date.now() - 15 * 60 * 1000).toISOString();
    expect(formatRelativeTime(date)).toBe("15min");
  });

  test("returns hours for < 24 hours", () => {
    const date = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
    expect(formatRelativeTime(date)).toBe("3h");
  });

  test("returns days for < 7 days", () => {
    const date = new Date(Date.now() - 2 * 86400 * 1000).toISOString();
    expect(formatRelativeTime(date)).toBe("2d");
  });
});

describe("formatDuration", () => {
  test("formats seconds", () => {
    expect(formatDuration(45)).toBe("45s");
  });

  test("formats minutes and seconds", () => {
    expect(formatDuration(125)).toBe("2m 5s");
  });

  test("formats exact minutes", () => {
    expect(formatDuration(120)).toBe("2m");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webhook/mini-app && npx vitest run src/lib/__tests__/format.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement format helpers**

Create `webhook/mini-app/src/lib/format.ts`:

```ts
export function formatRelativeTime(isoDate: string): string {
  const date = new Date(isoDate);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  const diffHrs = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMin < 1) return "agora";
  if (diffMin < 60) return `${diffMin}min`;
  if (diffHrs < 24) return `${diffHrs}h`;
  if (diffDays < 7) return `${diffDays}d`;
  return date.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const min = Math.floor(seconds / 60);
  const sec = seconds % 60;
  return sec > 0 ? `${min}m ${sec}s` : `${min}m`;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webhook/mini-app && npx vitest run src/lib/__tests__/format.test.ts`
Expected: all 7 tests PASS.

- [ ] **Step 5: Create useNavigation hook**

Create `webhook/mini-app/src/hooks/useNavigation.ts`:

```ts
import { useState, useCallback, useEffect } from "react";
import { useTelegram } from "./useTelegram";

interface NavState {
  tab: string;
  page: string | null;
  params: Record<string, string>;
}

export function useNavigation() {
  const { backButton, haptic } = useTelegram();
  const [state, setState] = useState<NavState>({
    tab: "home",
    page: null,
    params: {},
  });

  const setTab = useCallback(
    (tab: string) => {
      haptic?.impactOccurred("light");
      setState({ tab, page: null, params: {} });
      backButton?.hide();
    },
    [haptic, backButton],
  );

  const pushPage = useCallback(
    (page: string, params: Record<string, string> = {}) => {
      haptic?.impactOccurred("light");
      setState((prev) => ({ ...prev, page, params }));
      backButton?.show();
    },
    [haptic, backButton],
  );

  const goBack = useCallback(() => {
    setState((prev) => ({ ...prev, page: null, params: {} }));
    backButton?.hide();
  }, [backButton]);

  useEffect(() => {
    if (!backButton) return;
    const handler = () => goBack();
    backButton.onClick(handler);
    return () => backButton.offClick(handler);
  }, [backButton, goBack]);

  return { ...state, setTab, pushPage, goBack };
}
```

- [ ] **Step 6: Create useInfiniteScroll hook**

Create `webhook/mini-app/src/hooks/useInfiniteScroll.ts`:

```ts
import { useCallback, useRef } from "react";

export function useInfiniteScroll(
  onLoadMore: () => void,
  hasMore: boolean,
  isLoading: boolean,
) {
  const observer = useRef<IntersectionObserver>();

  const sentinelRef = useCallback(
    (node: HTMLElement | null) => {
      if (isLoading) return;
      if (observer.current) observer.current.disconnect();

      observer.current = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && hasMore) {
          onLoadMore();
        }
      });

      if (node) observer.current.observe(node);
    },
    [isLoading, hasMore, onLoadMore],
  );

  return sentinelRef;
}
```

- [ ] **Step 7: Update App.tsx with lazy loading + navigation**

Replace `webhook/mini-app/src/App.tsx`:

```tsx
import { lazy, Suspense } from "react";
import { TabBar } from "./components/TabBar";
import { Skeleton } from "./components/Skeleton";
import { useNavigation } from "./hooks/useNavigation";

const Home = lazy(() => import("./pages/Home"));
const Workflows = lazy(() => import("./pages/Workflows"));
const News = lazy(() => import("./pages/News"));
const NewsDetail = lazy(() => import("./pages/NewsDetail"));
const More = lazy(() => import("./pages/More"));
const Reports = lazy(() => import("./pages/Reports"));
const Contacts = lazy(() => import("./pages/Contacts"));

function PageSkeleton() {
  return (
    <div className="space-y-3 p-4">
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

export default function App() {
  const { tab, page, params, setTab, pushPage, goBack } = useNavigation();

  const renderContent = () => {
    if (page === "news-detail") {
      return <NewsDetail itemId={params.id ?? ""} onBack={goBack} />;
    }
    if (page === "reports") {
      return <Reports onBack={goBack} />;
    }
    if (page === "contacts") {
      return <Contacts onBack={goBack} />;
    }
    if (page === "settings") {
      return (
        <div className="p-4 text-center pt-12">
          <p className="text-text-secondary text-sm">
            Gerencie suas notificacoes pelo bot.
          </p>
          <button onClick={goBack} className="mt-4 text-accent text-sm">
            {"\u2190"} Voltar
          </button>
        </div>
      );
    }

    switch (tab) {
      case "home":
        return <Home onNavigate={pushPage} />;
      case "workflows":
        return <Workflows />;
      case "news":
        return (
          <News
            onItemClick={(id) => pushPage("news-detail", { id })}
          />
        );
      case "more":
        return <More onNavigate={pushPage} />;
      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen bg-bg text-text-primary">
      <main className="pb-20">
        <Suspense fallback={<PageSkeleton />}>{renderContent()}</Suspense>
      </main>
      {!page && <TabBar activeTab={tab} onTabChange={setTab} />}
    </div>
  );
}
```

- [ ] **Step 8: Create placeholder page files so build passes**

Create these minimal placeholder files so TypeScript compiles (they'll be replaced in subsequent tasks):

`webhook/mini-app/src/pages/Home.tsx`:
```tsx
export default function Home({ onNavigate: _ }: { onNavigate: (page: string, params?: Record<string, string>) => void }) {
  return <div className="p-4 text-text-secondary">Home loading...</div>;
}
```

`webhook/mini-app/src/pages/Workflows.tsx`:
```tsx
export default function Workflows() {
  return <div className="p-4 text-text-secondary">Workflows loading...</div>;
}
```

`webhook/mini-app/src/pages/News.tsx`:
```tsx
export default function News({ onItemClick: _ }: { onItemClick: (id: string) => void }) {
  return <div className="p-4 text-text-secondary">News loading...</div>;
}
```

`webhook/mini-app/src/pages/NewsDetail.tsx`:
```tsx
export default function NewsDetail({ itemId: _, onBack: __ }: { itemId: string; onBack: () => void }) {
  return <div className="p-4 text-text-secondary">News detail loading...</div>;
}
```

`webhook/mini-app/src/pages/More.tsx`:
```tsx
export default function More({ onNavigate: _ }: { onNavigate: (page: string) => void }) {
  return <div className="p-4 text-text-secondary">More loading...</div>;
}
```

`webhook/mini-app/src/pages/Reports.tsx`:
```tsx
export default function Reports({ onBack: _ }: { onBack: () => void }) {
  return <div className="p-4 text-text-secondary">Reports loading...</div>;
}
```

`webhook/mini-app/src/pages/Contacts.tsx`:
```tsx
export default function Contacts({ onBack: _ }: { onBack: () => void }) {
  return <div className="p-4 text-text-secondary">Contacts loading...</div>;
}
```

- [ ] **Step 9: Verify build**

Run: `cd webhook/mini-app && npm run build`
Expected: builds successfully.

- [ ] **Step 10: Commit**

```bash
git add webhook/mini-app/src/
git commit -m "feat(mini-app): navigation system, format helpers, code splitting, page placeholders"
```

---

### Task 2: Home Screen

**Files:**
- Replace: `webhook/mini-app/src/pages/Home.tsx`
- Create: `webhook/mini-app/src/pages/__tests__/Home.test.tsx`

- [ ] **Step 1: Write Home render test**

Create `webhook/mini-app/src/pages/__tests__/Home.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

vi.mock("../../hooks/useApi", () => ({
  useApi: (path: string) => {
    if (path.includes("stats")) {
      return {
        data: {
          health_pct: 80,
          workflows_ok: 4,
          workflows_total: 5,
          runs_today: 47,
          contacts_active: 14,
          news_today: 12,
        },
        isLoading: false,
      };
    }
    if (path.includes("workflows")) {
      return {
        data: {
          workflows: [
            {
              id: "morning_check.yml",
              name: "MORNING CHECK",
              description: "Precos Platts",
              icon: "\uD83D\uDCCA",
              last_run: { status: "completed", conclusion: "success", created_at: new Date().toISOString(), duration_seconds: 45 },
              health_pct: 100,
              recent_runs: [{ conclusion: "success", created_at: new Date().toISOString() }],
            },
          ],
        },
        isLoading: false,
      };
    }
    return { data: null, isLoading: true };
  },
}));

vi.mock("../../hooks/useTelegram", () => ({
  useTelegram: () => ({
    initData: "fake",
    haptic: null,
    backButton: null,
    showPopup: null,
    user: null,
    colorScheme: "dark",
    webApp: null,
    mainButton: null,
  }),
}));

test("renders health percentage", async () => {
  const Home = (await import("../Home")).default;
  render(<Home onNavigate={() => {}} />);
  expect(screen.getByText("80%")).toBeInTheDocument();
});

test("renders stats row", async () => {
  const Home = (await import("../Home")).default;
  render(<Home onNavigate={() => {}} />);
  expect(screen.getByText("47")).toBeInTheDocument();
  expect(screen.getByText("14")).toBeInTheDocument();
});

test("renders workflow name", async () => {
  const Home = (await import("../Home")).default;
  render(<Home onNavigate={() => {}} />);
  expect(screen.getByText("MORNING CHECK")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webhook/mini-app && npx vitest run src/pages/__tests__/Home.test.tsx`
Expected: FAIL — placeholder doesn't render stats.

- [ ] **Step 3: Implement Home page**

Replace `webhook/mini-app/src/pages/Home.tsx`:

```tsx
import { useApi } from "../hooks/useApi";
import { GlassCard } from "../components/GlassCard";
import { RingChart } from "../components/RingChart";
import { Sparkline } from "../components/Sparkline";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { formatRelativeTime } from "../lib/format";
import type { Stats, WorkflowsResponse, Workflow } from "../lib/types";

function conclusionToStatus(c: string | null): "success" | "error" | "warning" | "running" {
  if (c === "success") return "success";
  if (c === "failure") return "error";
  return "running";
}

function StatItem({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-center">
      <div className="text-lg font-semibold text-text-primary">{value}</div>
      <div className="text-[9px] text-text-muted uppercase tracking-wide">{label}</div>
    </div>
  );
}

function HeroCard({ stats }: { stats: Stats }) {
  return (
    <div className="relative rounded-hero p-6 glass border border-accent/10 overflow-hidden">
      <div className="absolute -top-20 -right-20 w-40 h-40 rounded-full bg-accent/[0.04] blur-3xl" />
      <div className="absolute -bottom-10 -left-10 w-32 h-32 rounded-full bg-accent/[0.03] blur-2xl" />

      <div className="relative">
        <span className="text-[9px] font-medium tracking-widest text-accent uppercase">
          System Health
        </span>

        <div className="flex items-center justify-between mt-2">
          <span className="text-4xl font-bold text-text-primary">{stats.health_pct}%</span>
          <RingChart value={stats.workflows_ok} total={stats.workflows_total} size={48} strokeWidth={4} />
        </div>

        <div className="flex justify-between mt-4 pt-4 border-t border-white/[0.06]">
          <StatItem label="Runs" value={stats.runs_today} />
          <StatItem label="Contatos" value={stats.contacts_active} />
          <StatItem label="News" value={stats.news_today} />
        </div>
      </div>
    </div>
  );
}

function WorkflowRow({ workflow }: { workflow: Workflow }) {
  const sparkData = workflow.recent_runs.map((r) => (r.conclusion === "success" ? 1 : 0));
  const status = conclusionToStatus(workflow.last_run?.conclusion ?? null);

  return (
    <div className="flex items-center gap-3 p-3">
      <div className="w-9 h-9 rounded-icon bg-accent/10 flex items-center justify-center text-lg">
        {workflow.icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-text-primary truncate">{workflow.name}</div>
        <div className="text-[10px] text-text-muted">
          {workflow.last_run ? formatRelativeTime(workflow.last_run.created_at) : "Nunca"}
        </div>
      </div>
      <Sparkline data={sparkData.length >= 2 ? sparkData : [0, 0]} />
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-text-secondary">{workflow.health_pct}%</span>
        <StatusDot status={status} />
      </div>
    </div>
  );
}

interface HomeProps {
  onNavigate: (page: string, params?: Record<string, string>) => void;
}

export default function Home({ onNavigate }: HomeProps) {
  const { data: stats, isLoading: statsLoading } = useApi<Stats>("/api/mini/stats");
  const { data: wfData, isLoading: wfLoading } = useApi<WorkflowsResponse>("/api/mini/workflows");

  return (
    <div className="p-4 space-y-4">
      {statsLoading || !stats ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <HeroCard stats={stats} />
      )}

      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[9px] font-medium tracking-widest text-text-muted uppercase">
            Workflows
          </span>
          <button
            onClick={() => onNavigate("workflows-tab")}
            className="text-[11px] text-accent"
          >
            Ver todos &rarr;
          </button>
        </div>

        <GlassCard>
          {wfLoading || !wfData ? (
            <div className="space-y-3 p-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : (
            <div className="divide-y divide-white/[0.04]">
              {wfData.workflows.map((wf) => (
                <WorkflowRow key={wf.id} workflow={wf} />
              ))}
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webhook/mini-app && npx vitest run src/pages/__tests__/Home.test.tsx`
Expected: all 3 tests PASS.

- [ ] **Step 5: Verify build**

Run: `cd webhook/mini-app && npm run build`

- [ ] **Step 6: Commit**

```bash
git add webhook/mini-app/src/pages/Home.tsx webhook/mini-app/src/pages/__tests__/Home.test.tsx
git commit -m "feat(mini-app): Home screen — hero card with system health + workflow overview"
```

---

### Task 3: Workflows Screen

**Files:**
- Replace: `webhook/mini-app/src/pages/Workflows.tsx`
- Create: `webhook/mini-app/src/pages/__tests__/Workflows.test.tsx`

- [ ] **Step 1: Write Workflows render test**

Create `webhook/mini-app/src/pages/__tests__/Workflows.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { vi } from "vitest";

const mockTrigger = vi.fn();

vi.mock("../../hooks/useApi", () => ({
  useApi: (path: string) => {
    if (path?.includes("workflows") && !path.includes("runs")) {
      return {
        data: {
          workflows: [
            {
              id: "morning_check.yml",
              name: "MORNING CHECK",
              description: "Precos Platts",
              icon: "\uD83D\uDCCA",
              last_run: { status: "completed", conclusion: "success", created_at: "2026-04-17T08:30:00Z", duration_seconds: 45 },
              health_pct: 100,
              recent_runs: [],
            },
          ],
        },
        isLoading: false,
      };
    }
    if (path?.includes("runs")) {
      return {
        data: {
          runs: [
            { id: 1, status: "completed", conclusion: "success", created_at: "2026-04-17T08:30:00Z", duration_seconds: 45, error: null, html_url: "" },
          ],
        },
        isLoading: false,
      };
    }
    return { data: null, isLoading: true };
  },
}));

vi.mock("../../hooks/useTelegram", () => ({
  useTelegram: () => ({
    initData: "fake",
    haptic: { impactOccurred: vi.fn(), notificationOccurred: vi.fn() },
    backButton: null,
    showPopup: vi.fn((_params: unknown, cb: (id: string) => void) => cb("confirm")),
    user: null,
    colorScheme: "dark",
    webApp: null,
    mainButton: null,
  }),
}));

vi.mock("../../lib/api", () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true }),
}));

test("renders workflow names", async () => {
  const Workflows = (await import("../Workflows")).default;
  render(<Workflows />);
  expect(screen.getByText("MORNING CHECK")).toBeInTheDocument();
});

test("expands card on click", async () => {
  const Workflows = (await import("../Workflows")).default;
  render(<Workflows />);
  fireEvent.click(screen.getByText("MORNING CHECK"));
  expect(screen.getByText(/Executar agora/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webhook/mini-app && npx vitest run src/pages/__tests__/Workflows.test.tsx`
Expected: FAIL — placeholder doesn't render workflow names.

- [ ] **Step 3: Implement Workflows page**

Replace `webhook/mini-app/src/pages/Workflows.tsx`:

```tsx
import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import { GlassCard } from "../components/GlassCard";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { formatRelativeTime, formatDuration } from "../lib/format";
import { apiFetch } from "../lib/api";
import type { WorkflowsResponse, Workflow, RunsResponse } from "../lib/types";

function conclusionToStatus(c: string | null): "success" | "error" | "warning" | "running" {
  if (c === "success") return "success";
  if (c === "failure") return "error";
  return "running";
}

function WorkflowRuns({ workflowId }: { workflowId: string }) {
  const { data, isLoading } = useApi<RunsResponse>(
    `/api/mini/workflows/${workflowId}/runs?limit=5`,
  );

  if (isLoading) return <Skeleton className="h-20 w-full mt-3" />;
  if (!data?.runs.length) return <p className="text-xs text-text-muted mt-3">Sem execucoes recentes.</p>;

  return (
    <div className="mt-3 space-y-1.5">
      <span className="text-[9px] font-medium tracking-widest text-text-muted uppercase">
        Ultimas execucoes
      </span>
      {data.runs.map((run) => (
        <div key={run.id} className="flex items-center gap-2 text-xs">
          <StatusDot status={conclusionToStatus(run.conclusion)} size={6} />
          <span className="text-text-secondary">{formatRelativeTime(run.created_at)}</span>
          {run.conclusion === "success" && run.duration_seconds != null && (
            <span className="text-text-muted">{formatDuration(run.duration_seconds)}</span>
          )}
          {run.conclusion === "failure" && (
            <span className="text-error text-[10px]">falhou</span>
          )}
        </div>
      ))}
    </div>
  );
}

function WorkflowCard({
  workflow,
  isExpanded,
  onToggle,
  onTrigger,
}: {
  workflow: Workflow;
  isExpanded: boolean;
  onToggle: () => void;
  onTrigger: () => void;
}) {
  return (
    <GlassCard
      className={`overflow-hidden ${
        workflow.last_run
          ? `border-l-2 ${
              workflow.last_run.conclusion === "success"
                ? "border-l-success/20"
                : workflow.last_run.conclusion === "failure"
                  ? "border-l-error/20"
                  : "border-l-warning/20"
            }`
          : ""
      }`}
    >
      <button onClick={onToggle} className="w-full p-4 text-left">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-icon bg-accent/10 flex items-center justify-center text-xl">
            {workflow.icon}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium">{workflow.name}</div>
            <div className="text-xs text-text-secondary">{workflow.description}</div>
          </div>
          <div className="text-right">
            <div className="text-sm font-medium">{workflow.health_pct}%</div>
            <div className="text-[10px] text-text-muted">
              {workflow.last_run ? formatRelativeTime(workflow.last_run.created_at) : "\u2014"}
            </div>
          </div>
        </div>
      </button>

      {isExpanded && (
        <div className="px-4 pb-4 border-t border-white/[0.04]">
          <WorkflowRuns workflowId={workflow.id} />
          <div className="flex gap-2 mt-4">
            <button
              onClick={onTrigger}
              className="flex-1 py-2.5 rounded-card bg-accent text-bg text-sm font-medium active:opacity-80"
            >
              \u25B6 Executar agora
            </button>
          </div>
        </div>
      )}
    </GlassCard>
  );
}

export default function Workflows() {
  const { data, isLoading, mutate } = useApi<WorkflowsResponse>("/api/mini/workflows");
  const { haptic, showPopup, initData } = useTelegram();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const handleToggle = (id: string) => {
    haptic?.impactOccurred("light");
    setExpandedId(expandedId === id ? null : id);
  };

  const handleTrigger = (workflow: Workflow) => {
    if (showPopup) {
      showPopup(
        {
          title: "Confirmar",
          message: `Executar ${workflow.name} agora?`,
          buttons: [
            { id: "confirm", type: "default", text: "Executar" },
            { id: "cancel", type: "cancel" },
          ],
        },
        async (buttonId) => {
          if (buttonId !== "confirm") return;
          try {
            await apiFetch("/api/mini/trigger", initData, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ workflow_id: workflow.id }),
            });
            haptic?.notificationOccurred("success");
            mutate();
          } catch {
            haptic?.notificationOccurred("error");
          }
        },
      );
    }
  };

  return (
    <div className="p-4 space-y-3">
      <h1 className="text-lg font-semibold">\u26A1 Workflows</h1>

      {isLoading || !data ? (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : (
        data.workflows.map((wf) => (
          <WorkflowCard
            key={wf.id}
            workflow={wf}
            isExpanded={expandedId === wf.id}
            onToggle={() => handleToggle(wf.id)}
            onTrigger={() => handleTrigger(wf)}
          />
        ))
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webhook/mini-app && npx vitest run src/pages/__tests__/Workflows.test.tsx`
Expected: all 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add webhook/mini-app/src/pages/Workflows.tsx webhook/mini-app/src/pages/__tests__/Workflows.test.tsx
git commit -m "feat(mini-app): Workflows screen — expandable cards, run history, trigger with popup"
```

---

### Task 4: News Screen + Detail

**Files:**
- Replace: `webhook/mini-app/src/pages/News.tsx`
- Replace: `webhook/mini-app/src/pages/NewsDetail.tsx`
- Create: `webhook/mini-app/src/pages/__tests__/News.test.tsx`

- [ ] **Step 1: Write News render test**

Create `webhook/mini-app/src/pages/__tests__/News.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { vi } from "vitest";

vi.mock("../../hooks/useTelegram", () => ({
  useTelegram: () => ({
    initData: "fake",
    haptic: null,
    backButton: null,
    showPopup: null,
    user: null,
    colorScheme: "dark",
    webApp: null,
    mainButton: null,
  }),
}));

vi.mock("../../lib/api", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    items: [
      { id: "p1", title: "Iron ore surges", source: "Platts", date: "2026-04-17T08:00:00Z", status: "pending", preview_url: null, source_feed: "" },
      { id: "p2", title: "Steel output rises", source: "Platts", date: "2026-04-17T07:00:00Z", status: "archived", preview_url: null, source_feed: "" },
    ],
    total: 2,
    page: 1,
  }),
}));

test("renders news items", async () => {
  const News = (await import("../News")).default;
  render(<News onItemClick={() => {}} />);
  expect(await screen.findByText("Iron ore surges")).toBeInTheDocument();
  expect(screen.getByText("Steel output rises")).toBeInTheDocument();
});

test("renders filter chips", async () => {
  const News = (await import("../News")).default;
  render(<News onItemClick={() => {}} />);
  expect(screen.getByText("Todos")).toBeInTheDocument();
  expect(screen.getByText("Pendentes")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webhook/mini-app && npx vitest run src/pages/__tests__/News.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement News page**

Replace `webhook/mini-app/src/pages/News.tsx`:

```tsx
import { useState, useCallback } from "react";
import useSWRInfinite from "swr/infinite";
import { useTelegram } from "../hooks/useTelegram";
import { useInfiniteScroll } from "../hooks/useInfiniteScroll";
import { FilterChips } from "../components/FilterChips";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { GlassCard } from "../components/GlassCard";
import { formatRelativeTime } from "../lib/format";
import { apiFetch } from "../lib/api";
import type { NewsItem, NewsResponse } from "../lib/types";

const FILTER_OPTIONS = [
  { id: "all", label: "Todos" },
  { id: "pending", label: "Pendentes" },
  { id: "archived", label: "Arquivados" },
  { id: "rejected", label: "Recusados" },
];

const STATUS_MAP: Record<string, "success" | "error" | "warning"> = {
  archived: "success",
  pending: "warning",
  rejected: "error",
};

function NewsRow({ item, onClick }: { item: NewsItem; onClick: () => void }) {
  return (
    <button onClick={onClick} className="w-full flex items-center gap-3 py-3 px-1 text-left">
      <StatusDot status={STATUS_MAP[item.status] ?? "warning"} />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-text-primary truncate">{item.title}</div>
      </div>
      <span className="text-[10px] text-text-muted whitespace-nowrap ml-2">
        {formatRelativeTime(item.date)}
      </span>
    </button>
  );
}

const PAGE_SIZE = 20;

interface NewsProps {
  onItemClick: (id: string) => void;
}

export default function News({ onItemClick }: NewsProps) {
  const [status, setStatus] = useState("all");
  const { initData, haptic } = useTelegram();

  const getKey = useCallback(
    (pageIndex: number, prev: NewsResponse | null) => {
      if (prev && prev.items.length === 0) return null;
      if (!initData) return null;
      return `/api/mini/news?status=${status}&page=${pageIndex + 1}&limit=${PAGE_SIZE}`;
    },
    [status, initData],
  );

  const { data, size, setSize, isLoading, isValidating } = useSWRInfinite<NewsResponse>(
    getKey,
    (url: string) => apiFetch<NewsResponse>(url, initData),
    { revalidateOnFocus: false },
  );

  const items = data?.flatMap((page) => page.items) ?? [];
  const total = data?.[0]?.total ?? 0;
  const hasMore = items.length < total;

  const sentinelRef = useInfiniteScroll(
    () => setSize(size + 1),
    hasMore,
    isLoading || isValidating,
  );

  const handleFilterChange = (id: string) => {
    haptic?.impactOccurred("light");
    setStatus(id);
  };

  return (
    <div className="p-4">
      <h1 className="text-lg font-semibold mb-3">{"\uD83D\uDCF0"} News</h1>

      <FilterChips options={FILTER_OPTIONS} active={status} onChange={handleFilterChange} />

      <div className="mt-3">
        {isLoading && items.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : items.length === 0 ? (
          <p className="text-text-muted text-sm text-center py-8">Nenhum item encontrado.</p>
        ) : (
          <GlassCard className="divide-y divide-white/[0.02] px-3">
            {items.map((item, i) => (
              <div key={item.id} ref={i === items.length - 1 ? sentinelRef : undefined}>
                <NewsRow item={item} onClick={() => onItemClick(item.id)} />
              </div>
            ))}
          </GlassCard>
        )}

        {isValidating && items.length > 0 && (
          <Skeleton className="h-12 w-full mt-2" />
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement NewsDetail page**

Replace `webhook/mini-app/src/pages/NewsDetail.tsx`:

```tsx
import { useApi } from "../hooks/useApi";
import { GlassCard } from "../components/GlassCard";
import { StatusDot } from "../components/StatusDot";
import { Skeleton } from "../components/Skeleton";
import { formatRelativeTime } from "../lib/format";
import type { NewsDetail as NewsDetailType } from "../lib/types";

const STATUS_MAP: Record<string, "success" | "error" | "warning"> = {
  archived: "success",
  pending: "warning",
  rejected: "error",
};

interface NewsDetailProps {
  itemId: string;
  onBack: () => void;
}

export default function NewsDetail({ itemId }: NewsDetailProps) {
  const { data, isLoading } = useApi<NewsDetailType>(`/api/mini/news/${itemId}`);

  if (isLoading || !data) {
    return (
      <div className="p-4 space-y-3">
        <Skeleton className="h-6 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <div>
        <div className="flex items-center gap-2 mb-2">
          <StatusDot status={STATUS_MAP[data.status] ?? "warning"} />
          <span className="text-[10px] text-text-muted uppercase">{data.status}</span>
          <span className="text-[10px] text-text-muted">{"\u00B7"}</span>
          <span className="text-[10px] text-text-muted">{formatRelativeTime(data.date)}</span>
        </div>
        <h1 className="text-lg font-semibold leading-tight">{data.title}</h1>
        {data.source && (
          <p className="text-xs text-text-secondary mt-1">{data.source}</p>
        )}
      </div>

      <GlassCard className="p-4">
        <div className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
          {data.fullText}
        </div>
      </GlassCard>

      {data.tables.length > 0 &&
        data.tables.map((table, ti) => (
          <GlassCard key={ti} className="p-3 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr>
                  {table.header.map((h, i) => (
                    <th key={i} className="text-left py-1 px-2 text-accent font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, ri) => (
                  <tr key={ri} className="border-t border-white/[0.04]">
                    {row.map((cell, ci) => (
                      <td key={ci} className="py-1 px-2 text-text-secondary">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </GlassCard>
        ))}
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd webhook/mini-app && npx vitest run src/pages/__tests__/News.test.tsx`
Expected: all 2 tests PASS.

- [ ] **Step 6: Verify build**

Run: `cd webhook/mini-app && npm run build`

- [ ] **Step 7: Commit**

```bash
git add webhook/mini-app/src/pages/News.tsx webhook/mini-app/src/pages/NewsDetail.tsx webhook/mini-app/src/pages/__tests__/News.test.tsx
git commit -m "feat(mini-app): News screen — filter chips, infinite scroll, detail view"
```

---

### Task 5: More Menu + Reports Screen

**Files:**
- Replace: `webhook/mini-app/src/pages/More.tsx`
- Replace: `webhook/mini-app/src/pages/Reports.tsx`

- [ ] **Step 1: Implement More page**

Replace `webhook/mini-app/src/pages/More.tsx`:

```tsx
import { MenuRow } from "../components/MenuRow";
import { useApi } from "../hooks/useApi";
import type { Stats } from "../lib/types";

interface MoreProps {
  onNavigate: (page: string) => void;
}

export default function More({ onNavigate }: MoreProps) {
  const { data: stats } = useApi<Stats>("/api/mini/stats");

  return (
    <div className="p-4 space-y-3">
      <h1 className="text-lg font-semibold mb-1">{"\u2022\u2022\u2022"} Mais</h1>

      <MenuRow
        icon={"\uD83D\uDCCA"}
        title="Reports"
        subtitle="PDFs Platts \u2014 Market & Research"
        onClick={() => onNavigate("reports")}
      />
      <MenuRow
        icon={"\uD83D\uDC65"}
        title="Contatos"
        subtitle={`${stats?.contacts_active ?? "..."} ativos \u00B7 gerenciar lista`}
        onClick={() => onNavigate("contacts")}
      />
      <MenuRow
        icon={"\u2699\uFE0F"}
        title="Settings"
        subtitle="Notificacoes e preferencias"
        onClick={() => onNavigate("settings")}
      />
    </div>
  );
}
```

- [ ] **Step 2: Implement Reports page**

Replace `webhook/mini-app/src/pages/Reports.tsx`:

```tsx
import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import { GlassCard } from "../components/GlassCard";
import { Skeleton } from "../components/Skeleton";
import type { ReportsResponse } from "../lib/types";

const REPORT_TYPES = ["Market Reports", "Research Reports"];

interface ReportsProps {
  onBack: () => void;
}

export default function Reports({ onBack: _ }: ReportsProps) {
  const [reportType, setReportType] = useState<string | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const { haptic } = useTelegram();

  let apiPath: string | null = null;
  if (reportType) {
    apiPath = `/api/mini/reports?type=${encodeURIComponent(reportType)}`;
    if (year) apiPath += `&year=${year}`;
  }

  const { data, isLoading } = useApi<ReportsResponse>(apiPath);

  const handleDownload = (downloadUrl: string) => {
    haptic?.impactOccurred("medium");
    window.open(downloadUrl, "_blank");
  };

  if (!reportType) {
    return (
      <div className="p-4 space-y-3">
        <h1 className="text-lg font-semibold">{"\uD83D\uDCCA"} Reports</h1>
        {REPORT_TYPES.map((type) => (
          <GlassCard key={type} className="p-4">
            <button
              onClick={() => {
                haptic?.impactOccurred("light");
                setReportType(type);
              }}
              className="w-full text-left"
            >
              <div className="text-sm font-medium text-text-primary">{type}</div>
              <div className="text-xs text-text-secondary mt-0.5">Platts {type}</div>
            </button>
          </GlassCard>
        ))}
      </div>
    );
  }

  const years = data?.reports
    ? [...new Set(data.reports.map((r) => parseInt(r.date_key.slice(0, 4))))].sort((a, b) => b - a)
    : [];

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center gap-2 mb-1">
        <button
          onClick={() => {
            haptic?.impactOccurred("light");
            if (year) {
              setYear(null);
            } else {
              setReportType(null);
            }
          }}
          className="text-accent text-sm"
        >
          {"\u2190"} Voltar
        </button>
        <h1 className="text-lg font-semibold truncate">
          {reportType} {year ? `\u2014 ${year}` : ""}
        </h1>
      </div>

      {!year && years.length > 1 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {years.map((y) => (
            <button
              key={y}
              onClick={() => {
                haptic?.impactOccurred("light");
                setYear(y);
              }}
              className="px-3 py-1.5 rounded-chip text-xs bg-white/5 text-text-secondary border border-border"
            >
              {y}
            </button>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : !data?.reports.length ? (
        <p className="text-text-muted text-sm text-center py-8">Nenhum relatorio encontrado.</p>
      ) : (
        <GlassCard className="divide-y divide-white/[0.04]">
          {data.reports.map((report) => (
            <button
              key={report.id}
              onClick={() => handleDownload(report.download_url)}
              className="w-full flex items-center gap-3 p-3 text-left"
            >
              <span className="text-xl">{"\uD83D\uDCC4"}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-text-primary truncate">{report.report_name}</div>
                <div className="text-[10px] text-text-muted">{report.date_key}</div>
              </div>
              <span className="text-accent text-xs">{"\u2B07"}</span>
            </button>
          ))}
        </GlassCard>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Verify build**

Run: `cd webhook/mini-app && npm run build`

- [ ] **Step 4: Commit**

```bash
git add webhook/mini-app/src/pages/More.tsx webhook/mini-app/src/pages/Reports.tsx
git commit -m "feat(mini-app): More menu + Reports screen with type/year drill-down"
```

---

### Task 6: Contacts Screen

**Files:**
- Replace: `webhook/mini-app/src/pages/Contacts.tsx`

- [ ] **Step 1: Implement Contacts page**

Replace `webhook/mini-app/src/pages/Contacts.tsx`:

```tsx
import { useState, useCallback } from "react";
import { useApi } from "../hooks/useApi";
import { useTelegram } from "../hooks/useTelegram";
import { GlassCard } from "../components/GlassCard";
import { Skeleton } from "../components/Skeleton";
import { apiFetch } from "../lib/api";
import type { ContactsResponse } from "../lib/types";

interface ContactsProps {
  onBack: () => void;
}

export default function Contacts({ onBack: _ }: ContactsProps) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const { haptic, initData } = useTelegram();

  const query = search ? `&search=${encodeURIComponent(search)}` : "";
  const { data, isLoading, mutate } = useApi<ContactsResponse>(
    `/api/mini/contacts?page=${page}${query}`,
  );

  const handleToggle = useCallback(
    async (phone: string) => {
      haptic?.impactOccurred("medium");
      try {
        await apiFetch(`/api/mini/contacts/${phone}/toggle`, initData, {
          method: "POST",
        });
        haptic?.notificationOccurred("success");
        mutate();
      } catch {
        haptic?.notificationOccurred("error");
      }
    },
    [haptic, initData, mutate],
  );

  const handleSearch = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setSearch(e.target.value);
      setPage(1);
    },
    [],
  );

  return (
    <div className="p-4 space-y-3">
      <h1 className="text-lg font-semibold">{"\uD83D\uDC65"} Contatos</h1>

      <input
        type="text"
        value={search}
        onChange={handleSearch}
        placeholder="Buscar contato..."
        className="w-full px-4 py-2.5 rounded-card bg-white/5 border border-border text-sm text-text-primary placeholder:text-text-muted outline-none focus:border-accent/30"
      />

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : !data?.contacts.length ? (
        <p className="text-text-muted text-sm text-center py-8">Nenhum contato encontrado.</p>
      ) : (
        <GlassCard className="divide-y divide-white/[0.04]">
          {data.contacts.map((contact) => (
            <div key={contact.phone} className="flex items-center gap-3 p-3">
              <div className="flex-1 min-w-0">
                <div className="text-sm text-text-primary">{contact.name}</div>
                <div className="text-[10px] text-text-muted">{contact.phone}</div>
              </div>
              <button
                onClick={() => handleToggle(contact.phone)}
                className={`w-10 h-6 rounded-full transition-colors relative ${
                  contact.active ? "bg-accent" : "bg-white/10"
                }`}
              >
                <div
                  className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                    contact.active ? "left-5" : "left-1"
                  }`}
                />
              </button>
            </div>
          ))}
        </GlassCard>
      )}

      {data && data.total > 20 && (
        <div className="flex justify-center gap-4 pt-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="text-sm text-accent disabled:text-text-muted"
          >
            {"\u2190"} Anterior
          </button>
          <span className="text-sm text-text-muted">Pag {page}</span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={data.contacts.length < 20}
            className="text-sm text-accent disabled:text-text-muted"
          >
            Proxima {"\u2192"}
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd webhook/mini-app && npm run build`

- [ ] **Step 3: Run all tests**

Run: `cd webhook/mini-app && npx vitest run`
Expected: all tests PASS (component tests + page tests + format tests).

- [ ] **Step 4: Commit**

```bash
git add webhook/mini-app/src/pages/Contacts.tsx
git commit -m "feat(mini-app): Contacts screen — search, list, toggle with haptic feedback"
```
