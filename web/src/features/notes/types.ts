export type NoteProjectionState = "pending" | "ready" | "failed";
export type NoteSourceKind = "qa_message" | "qa_citation";
export type NoteSourceSelectorKind = "qa_answer" | "qa_citation";

export type NoteSource = {
  id: string | null;
  kind: NoteSourceKind;
  deleted: boolean;
  qa_thread_id: string | null;
  qa_message_id: string | null;
  citation_id: string | null;
  document_id: string | null;
  locator: Record<string, unknown> | null;
  title_snapshot: string | null;
  excerpt_snapshot: string | null;
  created_at: string;
  source_deleted_at: string | null;
};

export type NoteListSource = {
  id: string | null;
  kind: NoteSourceKind;
  deleted: boolean;
  document_id: string | null;
  source_deleted_at: string | null;
};

export type Note = {
  id: string;
  body_markdown: string;
  concept: string | null;
  tags: string[];
  sources: NoteSource[];
  version: number;
  projection_state: NoteProjectionState;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
};

export type NoteListItem = Omit<Note, "sources"> & { sources: NoteListSource[] };
export type NotePage = { items: NoteListItem[]; next_cursor: string | null };

export type NoteFilters = {
  cursor?: string | null;
  limit?: number;
  query?: string;
  tags?: string[];
  source_kind?: NoteSourceKind | "";
  sort?: "updated_desc";
};

export type NoteCreateInput = {
  body_markdown: string;
  concept?: string | null;
  tags?: string[];
  client_request_id: string;
  source?: {
    kind: NoteSourceSelectorKind;
    qa_message_id: string;
    citation_id?: string;
  } | null;
};

export type NoteUpdateInput = {
  body_markdown: string;
  concept?: string | null;
  tags?: string[];
  expected_version: number;
};

export type NoteDeleteInput = { expected_version: number };
export type NoteClearResult = { cleared_count: number };
export type NoteProjectionRetryResult = { requeued_count: number };
export type NoteCapabilities = { enabled: boolean };
