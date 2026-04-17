import { useState, useCallback, useEffect } from "react";
import { useTelegram } from "./useTelegram";

interface NavState {
  tab: string;
  page: string | null;
  params: Record<string, string>;
}

export function useNavigation() {
  const { backButton, haptic } = useTelegram();
  const [state, setState] = useState<NavState>({
    tab: "home",
    page: null,
    params: {},
  });

  const setTab = useCallback(
    (tab: string) => {
      haptic?.impactOccurred("light");
      setState({ tab, page: null, params: {} });
      backButton?.hide();
    },
    [haptic, backButton],
  );

  const pushPage = useCallback(
    (page: string, params: Record<string, string> = {}) => {
      haptic?.impactOccurred("light");
      setState((prev) => ({ ...prev, page, params }));
      backButton?.show();
    },
    [haptic, backButton],
  );

  const goBack = useCallback(() => {
    setState((prev) => ({ ...prev, page: null, params: {} }));
    backButton?.hide();
  }, [backButton]);

  useEffect(() => {
    if (!backButton) return;
    const handler = () => goBack();
    backButton.onClick(handler);
    return () => backButton.offClick(handler);
  }, [backButton, goBack]);

  return { ...state, setTab, pushPage, goBack };
}
