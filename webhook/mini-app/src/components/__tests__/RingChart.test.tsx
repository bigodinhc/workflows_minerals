import { render } from "@testing-library/react";
import { RingChart } from "../RingChart";

test("renders SVG with two circles", () => {
  const { container } = render(<RingChart value={3} total={5} />);
  const circles = container.querySelectorAll("circle");
  expect(circles.length).toBe(2);
});

test("background circle has low-opacity stroke", () => {
  const { container } = render(<RingChart value={3} total={5} />);
  const bg = container.querySelectorAll("circle")[0];
  expect(bg.getAttribute("stroke")).toBe("rgba(255,255,255,0.06)");
});

test("progress circle uses accent color by default", () => {
  const { container } = render(<RingChart value={3} total={5} />);
  const progress = container.querySelectorAll("circle")[1];
  expect(progress.getAttribute("stroke")).toBe("#14b8a6");
});
