import type { AuthContextValue } from "../../auth/AuthProvider";
import type { SearchInput, SearchResponse } from "./types";

export function searchDocuments(request: AuthContextValue["request"], input: SearchInput, signal?: AbortSignal) {
  return request<SearchResponse>("/api/v1/search", {
    method: "POST", signal, cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, query: input.query.trim() }),
  });
}
