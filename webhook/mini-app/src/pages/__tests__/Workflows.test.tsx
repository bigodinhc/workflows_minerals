import { render, screen, fireEvent } from "@testing-library/react";
import { vi } from "vitest";

vi.mock("../../hooks/useApi", () => ({
  useApi: (path: string) => {
    if (path?.includes("workflows") && !path.includes("runs")) {
      return {
        data: {
          workflows: [
            {
              id: "morning_check.yml", name: "MORNING CHECK", description: "Precos Platts",
              icon: "\uD83D\uDCCA",
              last_run: { status: "completed", conclusion: "success", created_at: "2026-04-17T08:30:00Z", duration_seconds: 45 },
              health_pct: 100, recent_runs: [],
            },
          ],
        },
        isLoading: false, mutate: vi.fn(),
      };
    }
    if (path?.includes("runs")) {
      return {
        data: { runs: [{ id: 1, status: "completed", conclusion: "success", created_at: "2026-04-17T08:30:00Z", duration_seconds: 45, error: null, html_url: "" }] },
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
    backButton: null, showPopup: vi.fn((_p: unknown, cb: (id: string) => void) => cb("confirm")),
    user: null, colorScheme: "dark", webApp: null, mainButton: null,
  }),
}));

vi.mock("../../lib/api", () => ({ apiFetch: vi.fn().mockResolvedValue({ ok: true }) }));

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
