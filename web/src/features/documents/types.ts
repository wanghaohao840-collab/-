export type Document = {
  document_id: string;
  name: string;
  file_suffix: string;
  size_bytes: number | null;
  loaded_at: string | null;
  status: "ready";
};

export type DocumentListResponse = {
  items: Document[];
};

export type ImportStatus =
  | "queued"
  | "running"
  | "retry_wait"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ImportStage =
  | "queued"
  | "staged"
  | "parsing"
  | "chunking"
  | "embedding"
  | "persisting"
  | "committing"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ImportCounts = {
  total: number;
  queued: number;
  running: number;
  retry_wait: number;
  succeeded: number;
  failed: number;
  cancelled: number;
};

export type ImportTask = {
  task_id: string;
  document_id: string;
  original_name: string;
  file_suffix: string;
  size_bytes: number;
  status: ImportStatus;
  stage: ImportStage;
  progress: number;
  error_code: string | null;
  error_summary: string | null;
  cancel_requested_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
};

export type ImportBatch = {
  batch_id: string;
  created_at: string;
  updated_at: string;
  counts: ImportCounts;
  tasks: ImportTask[];
};
