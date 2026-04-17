interface FilterChipOption {
  id: string;
  label: string;
}

interface FilterChipsProps {
  options: FilterChipOption[];
  active: string;
  onChange: (id: string) => void;
}

export function FilterChips({ options, active, onChange }: FilterChipsProps) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-none">
      {options.map((opt) => (
        <button
          key={opt.id}
          onClick={() => onChange(opt.id)}
          className={`px-3 py-1.5 rounded-chip text-xs whitespace-nowrap transition-colors ${
            active === opt.id
              ? "bg-accent/20 text-accent border border-accent/30"
              : "bg-white/5 text-text-secondary border border-border"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
