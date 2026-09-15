import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { apiGet, apiPost } from "./api";
import { useRefetchInterval } from "./settings";
import type { PipelineRun, RunOptions } from "./types";

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

export function usePipelineStatus(): UseQueryResult<PipelineRun> {
  return useQuery<PipelineRun>({
    queryKey: ["pipeline-status"],
    queryFn: () => apiGet<PipelineRun>("/pipeline/status"),
    // Poll briskly while a run is active, otherwise only on demand.
    refetchInterval: (query) => (query.state.data?.active ? 3000 : false),
  });
}

export function useRunPipeline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (options: RunOptions) => apiPost<PipelineRun>("/pipeline/run", options),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pipeline-status"] }),
  });
}

export function useRetrainModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<PipelineRun>("/pipeline/retrain", {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pipeline-status"] }),
  });
}
