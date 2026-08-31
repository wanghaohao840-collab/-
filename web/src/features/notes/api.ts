import type { AuthContextValue } from "../../auth/AuthProvider";
import type {
  Note, NoteCapabilities, NoteClearResult, NoteCreateInput, NoteDeleteInput,
  NoteFilters, NotePage, NoteProjectionRetryResult, NoteUpdateInput,
} from "./types";

type AuthRequest = AuthContextValue["request"];
const json = (body: unknown) => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const segment = (value: string) => encodeURIComponent(value);

export function noteFiltersQuery(filters: NoteFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.cursor) params.set("cursor", filters.cursor);
  params.set("limit", String(filters.limit ?? 20));
  if (filters.query?.trim()) params.set("query", filters.query.trim());
  const tags = (filters.tags ?? []).map((tag) => tag.trim()).filter(Boolean);
  if (tags.length) params.set("tags", tags.join(","));
  if (filters.source_kind) params.set("source_kind", filters.source_kind);
  params.set("sort", filters.sort ?? "updated_desc");
  return params.toString();
}

export const getNoteCapabilities = (request: AuthRequest, signal?: AbortSignal) =>
  request<NoteCapabilities>("/api/v1/notes/capabilities", { signal });

export const listNotes = (request: AuthRequest, filters: NoteFilters = {}, signal?: AbortSignal) =>
  request<NotePage>(`/api/v1/notes?${noteFiltersQuery(filters)}`, { signal });

export const getNote = (request: AuthRequest, id: string, signal?: AbortSignal) =>
  request<Note>(`/api/v1/notes/${segment(id)}`, { signal });

export const createNote = (request: AuthRequest, input: NoteCreateInput) =>
  request<Note>("/api/v1/notes", { method: "POST", ...json(input) });

export const updateNote = (request: AuthRequest, id: string, input: NoteUpdateInput) =>
  request<Note>(`/api/v1/notes/${segment(id)}`, { method: "PATCH", ...json(input) });

export const deleteNote = (request: AuthRequest, id: string, input: NoteDeleteInput) =>
  request<void>(`/api/v1/notes/${segment(id)}`, { method: "DELETE", ...json(input) });

export const clearNotes = (request: AuthRequest) =>
  request<NoteClearResult>("/api/v1/notes/clear", { method: "POST", ...json({ confirmation: "清空全部笔记" }) });

export const retryNoteProjections = (request: AuthRequest) =>
  request<NoteProjectionRetryResult>("/api/v1/notes/projections/retry", { method: "POST" });
