import { lazy, Suspense } from "react";
import { TabBar } from "./components/TabBar";
import { Skeleton } from "./components/Skeleton";
import { useNavigation } from "./hooks/useNavigation";

const Home = lazy(() => import("./pages/Home"));
const Workflows = lazy(() => import("./pages/Workflows"));
const News = lazy(() => import("./pages/News"));
const NewsDetail = lazy(() => import("./pages/NewsDetail"));
const More = lazy(() => import("./pages/More"));
const Reports = lazy(() => import("./pages/Reports"));
const Contacts = lazy(() => import("./pages/Contacts"));

function PageSkeleton() {
  return (
    <div className="space-y-3 p-4">
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

export default function App() {
  const { tab, page, params, setTab, pushPage, goBack } = useNavigation();

  const renderContent = () => {
    if (page === "news-detail") {
      return <NewsDetail itemId={params.id ?? ""} onBack={goBack} />;
    }
    if (page === "reports") {
      return <Reports onBack={goBack} />;
    }
    if (page === "contacts") {
      return <Contacts onBack={goBack} />;
    }
    if (page === "settings") {
      return (
        <div className="p-4 text-center pt-12">
          <p className="text-text-secondary text-sm">
            Gerencie suas notificacoes pelo bot.
          </p>
          <button onClick={goBack} className="mt-4 text-accent text-sm">
            {"\u2190"} Voltar
          </button>
        </div>
      );
    }

    switch (tab) {
      case "home":
        return <Home onNavigate={pushPage} />;
      case "workflows":
        return <Workflows />;
      case "news":
        return (
          <News
            onItemClick={(id) => pushPage("news-detail", { id })}
          />
        );
      case "more":
        return <More onNavigate={pushPage} />;
      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen bg-bg text-text-primary">
      <main className="pb-20">
        <Suspense fallback={<PageSkeleton />}>{renderContent()}</Suspense>
      </main>
      {!page && <TabBar activeTab={tab} onTabChange={setTab} />}
    </div>
  );
}
