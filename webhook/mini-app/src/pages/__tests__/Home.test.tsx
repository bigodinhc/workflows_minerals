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
    initData: "fake", haptic: null, backButton: null, showPopup: null,
    user: null, colorScheme: "dark", webApp: null, mainButton: null,
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
