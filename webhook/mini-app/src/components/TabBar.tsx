interface Tab {
  id: string;
  label: string;
  icon: string;
}

const TABS: Tab[] = [
  { id: "home", label: "Home", icon: "\uD83C\uDFE0" },
  { id: "workflows", label: "Workflows", icon: "\u26A1" },
  { id: "news", label: "News", icon: "\uD83D\uDCF0" },
  { id: "more", label: "Mais", icon: "\u2022\u2022\u2022" },
];

interface TabBarProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
}

export function TabBar({ activeTab, onTabChange }: TabBarProps) {
  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-[rgba(9,9,11,0.95)] backdrop-blur-[20px] border-t border-border safe-bottom z-50">
      <div className="flex justify-around py-2">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`relative flex flex-col items-center gap-0.5 min-w-[64px] min-h-[44px] justify-center ${
              activeTab === tab.id
                ? "text-accent"
                : "text-text-muted opacity-40"
            }`}
          >
            {activeTab === tab.id && (
              <div className="absolute top-0 w-8 h-0.5 bg-accent rounded-full" />
            )}
            <span className="text-xl leading-none">{tab.icon}</span>
            <span className="text-[10px]">{tab.label}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}
