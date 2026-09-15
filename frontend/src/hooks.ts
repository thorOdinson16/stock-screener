import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./api";
import { useRefetchInterval } from "./settings";

export function useApi<T>(key: unknown[], path: string, enabled = true) {
  const refetchInterval = useRefetchInterval();
  return useQuery<T>({
    queryKey: key,
    queryFn: () => apiGet<T>(path),
    enabled,
    refetchInterval,
    staleTime: 15000,
  });
}
