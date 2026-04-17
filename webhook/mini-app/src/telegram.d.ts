interface TelegramWebApp {
  ready(): void;
  expand(): void;
  close(): void;
  MainButton: {
    text: string;
    show(): void;
    hide(): void;
    onClick(callback: () => void): void;
    offClick(callback: () => void): void;
  };
  BackButton: {
    show(): void;
    hide(): void;
    onClick(callback: () => void): void;
    offClick(callback: () => void): void;
  };
  HapticFeedback: {
    impactOccurred(style: "light" | "medium" | "heavy" | "rigid" | "soft"): void;
    notificationOccurred(type: "error" | "success" | "warning"): void;
    selectionChanged(): void;
  };
  showPopup(params: {
    title?: string;
    message: string;
    buttons?: Array<{
      id?: string;
      type?: "default" | "ok" | "close" | "cancel" | "destructive";
      text?: string;
    }>;
  }, callback?: (buttonId: string) => void): void;
  initData: string;
  initDataUnsafe: {
    user?: {
      id: number;
      first_name: string;
      last_name?: string;
      username?: string;
      language_code?: string;
    };
    [key: string]: unknown;
  };
  colorScheme: "light" | "dark";
  themeParams: Record<string, string>;
  viewportHeight: number;
  viewportStableHeight: number;
}

interface Window {
  Telegram?: {
    WebApp?: TelegramWebApp;
  };
}
