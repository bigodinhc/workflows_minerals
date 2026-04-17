import { render, screen, fireEvent } from "@testing-library/react";
import { MenuRow } from "../MenuRow";

test("renders icon, title, and subtitle", () => {
  const { container } = render(
    <MenuRow icon="📊" title="Reports" subtitle="PDFs Platts" onClick={() => {}} />,
  );
  expect(container.querySelector(".text-2xl")?.textContent).toBe("📊");
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
