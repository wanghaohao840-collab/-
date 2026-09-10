import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import * as api from "./api";

export const insightKeys = {
  all: (user: string) => ["insights", user] as const,
  overview: (user: string) => [...insightKeys.all(user), "overview"] as const,
  stats: (user: string) => [...insightKeys.all(user), "stats"] as const,
  reports: (user: string) => [...insightKeys.all(user), "reports"] as const,
  report: (user: string, id: string) => [...insightKeys.all(user), "report", id] as const,
};
const identity = (auth: ReturnType<typeof useAuth>) => auth.status === "authenticated" ? auth.username : "anonymous";
export function useOverview() { const auth = useAuth(); const user = identity(auth); return useQuery({ queryKey: insightKeys.overview(user), queryFn: ({ signal }) => api.getOverview(auth.request, signal) }); }
export function useStats() { const auth = useAuth(); const user = identity(auth); return useQuery({ queryKey: insightKeys.stats(user), queryFn: ({ signal }) => api.getStats(auth.request, 30, signal) }); }
export function useReports() { const auth = useAuth(); const user = identity(auth); return useQuery({ queryKey: insightKeys.reports(user), queryFn: ({ signal }) => api.listReports(auth.request, signal) }); }
export function useReport(id?: string) { const auth = useAuth(); const user = identity(auth); return useQuery({ queryKey: insightKeys.report(user, id ?? ""), queryFn: ({ signal }) => api.getReport(auth.request, id!, signal), enabled: Boolean(id) }); }
export function useCreateReport() { const auth = useAuth(); const user = identity(auth); const client = useQueryClient(); return useMutation({ mutationFn: () => api.createReport(auth.request), onMutate: () => user, onSuccess: (report, _variables, owner) => { client.setQueryData(insightKeys.report(owner, report.id), report); void client.invalidateQueries({ queryKey: insightKeys.all(owner) }); } }); }
