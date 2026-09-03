import type { ApiRequestOptions } from "../../api/client";
import type { DocumentListResponse, ImportBatch } from "./types";

export type AuthRequest = <T>(
  input: string,
  options?: ApiRequestOptions,
) => Promise<T>;

function segment(value: string): string {
  return encodeURIComponent(value);
}

export function listDocuments(
  request: AuthRequest,
  signal?: AbortSignal,
): Promise<DocumentListResponse> {
  return request<DocumentListResponse>("/api/v1/documents", { signal });
}

export function listImports(
  request: AuthRequest,
  signal?: AbortSignal,
): Promise<ImportBatch[]> {
  return request<ImportBatch[]>("/api/v1/imports?limit=20", { signal });
}

export function deleteDocument(
  request: AuthRequest,
  documentId: string,
): Promise<void> {
  return request<void>(`/api/v1/documents/${segment(documentId)}`, {
    method: "DELETE",
  });
}

export function submitImports(
  request: AuthRequest,
  files: File[],
): Promise<ImportBatch> {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  return request<ImportBatch>("/api/v1/imports", { method: "POST", body });
}

export function retryImportTask(
  request: AuthRequest,
  batchId: string,
  taskId: string,
): Promise<ImportBatch> {
  return request<ImportBatch>(
    `/api/v1/imports/${segment(batchId)}/tasks/${segment(taskId)}/retry`,
    { method: "POST" },
  );
}

export function retryFailedImports(
  request: AuthRequest,
  batchId: string,
): Promise<ImportBatch> {
  return request<ImportBatch>(
    `/api/v1/imports/${segment(batchId)}/retry-failed`,
    { method: "POST" },
  );
}

export function cancelImportTask(
  request: AuthRequest,
  batchId: string,
  taskId: string,
): Promise<ImportBatch> {
  return request<ImportBatch>(
    `/api/v1/imports/${segment(batchId)}/tasks/${segment(taskId)}/cancel`,
    { method: "POST" },
  );
}
