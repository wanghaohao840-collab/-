import type { AuthContextValue } from "../../auth/AuthProvider";
import type {
  QaCapabilities, QaConversation, QaConversationPage, QaDeletion, QaJob,
  QaMessage, QaMessagePage, QaMode,
} from "./types";

type AuthRequest = AuthContextValue["request"];
const json = (body: unknown) => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getQaCapabilities = (request: AuthRequest, signal?: AbortSignal) =>
  request<QaCapabilities>("/api/v1/qa/capabilities", { signal });
export const listQaConversations = (request: AuthRequest, signal?: AbortSignal) =>
  request<QaConversationPage>("/api/v1/qa/conversations?limit=100", { signal });
export const getQaConversation = (request: AuthRequest, id: string, signal?: AbortSignal) =>
  request<QaConversation>(`/api/v1/qa/conversations/${id}`, { signal });
export const listQaMessages = (request: AuthRequest, id: string, signal?: AbortSignal) =>
  request<QaMessagePage>(`/api/v1/qa/conversations/${id}/messages?limit=200`, { signal });
export const createQaConversation = (request: AuthRequest, documentIds: string[]) =>
  request<QaConversation>("/api/v1/qa/conversations", { method: "POST", ...json({ document_ids: documentIds }) });
export const askQaQuestion = (request: AuthRequest, id: string, question: string, mode: QaMode, clientRequestId: string) =>
  request<QaMessage>(`/api/v1/qa/conversations/${id}/messages`, { method: "POST", ...json({ question, mode, client_request_id: clientRequestId }) });
export const getQaMessage = (request: AuthRequest, id: string, signal?: AbortSignal) =>
  request<QaMessage>(`/api/v1/qa/messages/${id}`, { signal });
export const retryQaMessage = (request: AuthRequest, id: string, clientRequestId: string) =>
  request<QaMessage>(`/api/v1/qa/messages/${id}/retry`, { method: "POST", ...json({ client_request_id: clientRequestId }) });
export const startQaSummary = (request: AuthRequest, id: string, instruction: string, clientRequestId: string) =>
  request<QaJob>(`/api/v1/qa/conversations/${id}/summary-jobs`, { method: "POST", ...json({ instruction: instruction || null, client_request_id: clientRequestId }) });
export const getQaJob = (request: AuthRequest, id: string, signal?: AbortSignal) =>
  request<QaJob>(`/api/v1/qa/jobs/${id}`, { signal });
export const cancelQaJob = (request: AuthRequest, id: string) =>
  request<QaJob>(`/api/v1/qa/jobs/${id}/cancel`, { method: "POST" });
export const deleteQaConversation = (request: AuthRequest, id: string) =>
  request<QaDeletion>(`/api/v1/qa/conversations/${id}`, { method: "DELETE" });
export const getQaDeletion = (request: AuthRequest, id: string, signal?: AbortSignal) =>
  request<QaDeletion>(`/api/v1/qa/deletions/${id}`, { signal });
