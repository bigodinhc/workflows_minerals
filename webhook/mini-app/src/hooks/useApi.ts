import useSWR, { type SWRConfiguration } from "swr";
import { useTelegram } from "./useTelegram";
import { apiFetch } from "../lib/api";

export function useApi<T>(path: string | null, config?: SWRConfiguration<T>) {
  const { initData } = useTelegram();

  return useSWR<T>(
    path && initData ? path : null,
    (url: string) => apiFetch<T>(url, initData),
    {
      revalidateOnFocus: false,
      ...config,
    },
  );
}
