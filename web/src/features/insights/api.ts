import type { AuthContextValue } from "../../auth/AuthProvider";
import type { LearningReport, LearningReportItem, LearningStats, Overview } from "./types";

type Request = AuthContextValue["request"];
const json = (body: unknown) => ({ headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const getOverview = (request: Request, signal?: AbortSignal) => request<Overview>("/api/v1/overview", { signal });
export const getStats = (request: Request, days = 30, signal?: AbortSignal) => request<LearningStats>(`/api/v1/insights/stats?days=${days}`, { signal });
export const listReports = (request: Request, signal?: AbortSignal) => request<LearningReportItem[]>("/api/v1/insights/reports", { signal });
export const getReport = (request: Request, id: string, signal?: AbortSignal) => request<LearningReport>(`/api/v1/insights/reports/${encodeURIComponent(id)}`, { signal });
export const createReport = (request: Request) => request<LearningReport>("/api/v1/insights/reports", { method: "POST", ...json({}) });
