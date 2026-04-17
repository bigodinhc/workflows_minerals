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
