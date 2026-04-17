interface GlassCardProps {
  children: React.ReactNode;
  className?: string;
}

export function GlassCard({ children, className = "" }: GlassCardProps) {
  return (
    <div className={`glass rounded-card border border-border ${className}`}>
      {children}
    </div>
  );
}
