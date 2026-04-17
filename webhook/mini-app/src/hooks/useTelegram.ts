function getWebApp() {
  return window.Telegram?.WebApp ?? null;
}

export function useTelegram() {
  const webApp = getWebApp();

  return {
    webApp,
    initData: webApp?.initData ?? "",
    user: webApp?.initDataUnsafe?.user ?? null,
    colorScheme: webApp?.colorScheme ?? "dark",
    haptic: webApp?.HapticFeedback ?? null,
    mainButton: webApp?.MainButton ?? null,
    backButton: webApp?.BackButton ?? null,
    showPopup: webApp?.showPopup?.bind(webApp) ?? null,
  };
}
