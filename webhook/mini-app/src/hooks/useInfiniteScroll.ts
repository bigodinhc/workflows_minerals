import { useCallback, useRef } from "react";

export function useInfiniteScroll(
  onLoadMore: () => void,
  hasMore: boolean,
  isLoading: boolean,
) {
  const observer = useRef<IntersectionObserver>(null);

  const sentinelRef = useCallback(
    (node: HTMLElement | null) => {
      if (isLoading) return;
      if (observer.current) observer.current.disconnect();

      observer.current = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && hasMore) {
          onLoadMore();
        }
      });

      if (node) observer.current.observe(node);
    },
    [isLoading, hasMore, onLoadMore],
  );

  return sentinelRef;
}
