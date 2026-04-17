import { useState } from "react";
import { TabBar } from "./components/TabBar";

export default function App() {
  const [activeTab, setActiveTab] = useState("home");

  return (
    <div className="min-h-screen bg-bg text-text-primary">
      <main className="p-4 pb-20">
        {activeTab === "home" && (
          <div className="text-center pt-12">
            <h1 className="text-lg font-semibold">SuperMustache</h1>
            <p className="text-text-secondary text-sm mt-2">Home — Phase 3C</p>
          </div>
        )}
        {activeTab === "workflows" && (
          <div className="text-center pt-12">
            <p className="text-text-secondary text-sm">Workflows — Phase 3C</p>
          </div>
        )}
        {activeTab === "news" && (
          <div className="text-center pt-12">
            <p className="text-text-secondary text-sm">News — Phase 3C</p>
          </div>
        )}
        {activeTab === "more" && (
          <div className="text-center pt-12">
            <p className="text-text-secondary text-sm">Mais — Phase 3C</p>
          </div>
        )}
      </main>
      <TabBar activeTab={activeTab} onTabChange={setActiveTab} />
    </div>
  );
}
