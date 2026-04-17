const STATUS_COLORS: Record<string, string> = {
  success: "#4ade80",
  error: "#f87171",
  warning: "#facc15",
  running: "#14b8a6",
};

interface StatusDotProps {
  status: "success" | "error" | "warning" | "running";
  size?: number;
}

export function StatusDot({ status, size = 8 }: StatusDotProps) {
  const color = STATUS_COLORS[status];
  return (
    <span
      className="inline-block rounded-full"
      style={{
        width: size,
        height: size,
        backgroundColor: color,
        boxShadow: `0 0 ${size}px ${color}4D`,
      }}
    />
  );
}
