import { render } from "@testing-library/react";
import { Sparkline } from "../Sparkline";

test("renders SVG with polyline", () => {
  const { container } = render(<Sparkline data={[1, 3, 2, 5, 4]} />);
  expect(container.querySelector("svg")).toBeInTheDocument();
  expect(container.querySelector("polyline")).toBeInTheDocument();
});

test("renders nothing with fewer than 2 data points", () => {
  const { container } = render(<Sparkline data={[1]} />);
  expect(container.querySelector("svg")).toBeNull();
});

test("polyline has correct number of coordinate pairs", () => {
  const { container } = render(<Sparkline data={[1, 2, 3]} />);
  const points = container.querySelector("polyline")!.getAttribute("points")!;
  expect(points.split(" ").length).toBe(3);
});
