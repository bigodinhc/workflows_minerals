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
