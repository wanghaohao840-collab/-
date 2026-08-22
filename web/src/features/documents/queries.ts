import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { useAuth } from "../../auth/AuthProvider";
import {
  cancelImportTask,
  deleteDocument,
  listDocuments,
  listImports,
  retryFailedImports,
  retryImportTask,
  submitImports,
} from "./api";
import type { ImportBatch, ImportStatus } from "./types";

export const DOCUMENTS_QUERY_KEY = ["documents"] as const;
export const IMPORTS_QUERY_KEY = ["imports", { limit: 20 }] as const;

const ACTIVE_IMPORT_STATUSES: ReadonlySet<ImportStatus> = new Set([
  "queued",
  "running",
  "retry_wait",
]);

export function hasActiveImports(batches: ImportBatch[]): boolean {
  return batches.some((batch) =>
    batch.tasks.some((task) => ACTIVE_IMPORT_STATUSES.has(task.status)),
  );
}

function taskStatuses(batches: ImportBatch[]): Map<string, ImportStatus> {
  return new Map(
    batches.flatMap((batch) =>
      batch.tasks.map((task) => [task.task_id, task.status] as const),
    ),
  );
}

function cacheServerBatch(queryClient: QueryClient, serverBatch: ImportBatch) {
  queryClient.setQueryData<ImportBatch[]>(IMPORTS_QUERY_KEY, (current = []) => {
    const index = current.findIndex(
      (batch) => batch.batch_id === serverBatch.batch_id,
    );
    if (index < 0) {
      return [serverBatch, ...current];
    }
    return current.map((batch, batchIndex) =>
      batchIndex === index ? serverBatch : batch,
    );
  });
}

function consumeInvalidations(
  queryClient: QueryClient,
  keys: ReadonlyArray<readonly unknown[]>,
) {
  void Promise.all(
    keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
  ).catch(() => undefined);
}

export function useDocumentsQuery() {
  const auth = useAuth();
  return useQuery({
    queryKey: DOCUMENTS_QUERY_KEY,
    queryFn: ({ signal }) => listDocuments(auth.request, signal),
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: true,
  });
}

export function useImportsQuery() {
  const auth = useAuth();
  const queryClient = useQueryClient();
  const previousStatusesRef = useRef<Map<string, ImportStatus> | undefined>(
    undefined,
  );
  const query = useQuery({
    queryKey: IMPORTS_QUERY_KEY,
    queryFn: ({ signal }) => listImports(auth.request, signal),
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: true,
    refetchInterval: (currentQuery) =>
      hasActiveImports(currentQuery.state.data ?? []) ? 2000 : false,
  });

  useEffect(() => {
    if (!query.data) {
      return;
    }
    const current = taskStatuses(query.data);
    const previous = previousStatusesRef.current;
    const completed = previous
      ? [...current].some(
          ([taskId, status]) =>
            status === "succeeded" &&
            ACTIVE_IMPORT_STATUSES.has(previous.get(taskId) ?? "succeeded"),
        )
      : false;
    previousStatusesRef.current = current;
    if (completed) {
      void queryClient
        .invalidateQueries({ queryKey: DOCUMENTS_QUERY_KEY })
        .catch(() => undefined);
    }
  }, [query.data, queryClient]);

  return query;
}

export function useDocumentMutations() {
  const auth = useAuth();
  const queryClient = useQueryClient();

  function acceptServerBatch(serverBatch: ImportBatch) {
    cacheServerBatch(queryClient, serverBatch);
    consumeInvalidations(queryClient, [IMPORTS_QUERY_KEY]);
  }

  const submit = useMutation({
    mutationFn: (files: File[]) => submitImports(auth.request, files),
    onSuccess: acceptServerBatch,
  });
  const retryTask = useMutation({
    mutationFn: ({ batchId, taskId }: { batchId: string; taskId: string }) =>
      retryImportTask(auth.request, batchId, taskId),
    onSuccess: acceptServerBatch,
  });
  const retryFailed = useMutation({
    mutationFn: ({ batchId }: { batchId: string }) =>
      retryFailedImports(auth.request, batchId),
    onSuccess: acceptServerBatch,
  });
  const cancelTask = useMutation({
    mutationFn: ({ batchId, taskId }: { batchId: string; taskId: string }) =>
      cancelImportTask(auth.request, batchId, taskId),
    onSuccess: acceptServerBatch,
  });
  const removeDocument = useMutation({
    mutationFn: ({ documentId }: { documentId: string }) =>
      deleteDocument(auth.request, documentId),
    onSuccess: () => {
      consumeInvalidations(queryClient, [DOCUMENTS_QUERY_KEY, IMPORTS_QUERY_KEY]);
    },
    onError: () => {
      consumeInvalidations(queryClient, [DOCUMENTS_QUERY_KEY, IMPORTS_QUERY_KEY]);
    },
  });

  return {
    submit,
    retryTask,
    retryFailed,
    cancelTask,
    removeDocument,
  };
}
