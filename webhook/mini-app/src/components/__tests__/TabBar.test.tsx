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
