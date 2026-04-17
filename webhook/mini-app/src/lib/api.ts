const BASE_URL = import.meta.env.VITE_API_URL || "";

export async function apiFetch<T>(
  path: string,
  initData: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "X-Telegram-Init-Data": initData,
      ...options?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText}`);
  }
  return response.json();
}
