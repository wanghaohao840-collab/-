import type { ReactNode } from "react";
import { ApiError } from "../../api/client";
import type { SearchResult } from "../../features/search/types";

export const errorMessage = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback;
export function sourceLocation(result: SearchResult) {
  return [result.page_number != null ? `第 ${result.page_number} 页` : "页码未提供", result.section, `片段 ${result.locator.chunk_index + 1}`].filter(Boolean).join(" · ");
}

export function highlightedExcerpt(text: string, query: string): ReactNode {
  if (!query.trim()) return text;
  const literal = query.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const matches = [...text.matchAll(new RegExp(literal, "giu"))];
  const parts: ReactNode[] = [];
  let offset = 0;
  for (const match of matches) {
    parts.push(text.slice(offset, match.index));
    parts.push(<mark key={match.index}>{match[0]}</mark>);
    offset = match.index + match[0].length;
  }
  parts.push(text.slice(offset));
  return parts;
}

