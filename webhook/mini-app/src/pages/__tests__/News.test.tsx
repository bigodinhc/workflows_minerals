import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

// jsdom lacks IntersectionObserver
const observeMock = vi.fn();
const disconnectMock = vi.fn();
vi.stubGlobal(
  "IntersectionObserver",
  vi.fn(() => ({ observe: observeMock, disconnect: disconnectMock, unobserve: vi.fn() })),
);

vi.mock("../../hooks/useTelegram", () => ({
  useTelegram: () => ({
    initData: "fake", haptic: null, backButton: null, showPopup: null,
    user: null, colorScheme: "dark", webApp: null, mainButton: null,
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
