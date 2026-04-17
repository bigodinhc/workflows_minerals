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
