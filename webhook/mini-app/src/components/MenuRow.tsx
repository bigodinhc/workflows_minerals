interface MenuRowProps {
  icon: string;
  title: string;
  subtitle: string;
  onClick: () => void;
}

export function MenuRow({ icon, title, subtitle, onClick }: MenuRowProps) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-3 p-4 glass rounded-card border border-border text-left"
    >
      <span className="text-2xl">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-text-primary text-sm font-medium">{title}</div>
        <div className="text-text-secondary text-xs">{subtitle}</div>
      </div>
      <span className="text-text-muted text-lg">{"\u203A"}</span>
    </button>
  );
}
