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
