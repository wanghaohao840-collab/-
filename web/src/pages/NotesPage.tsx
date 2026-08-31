import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { Button } from "../components/Button/Button";
import { NotesWorkspace, type NoteSaveInput } from "../components/NotesWorkspace/NotesWorkspace";
import { useNoteMutations, useNoteQuery, useNotesCapabilities, useNotesQuery } from "../features/notes/queries";
import type { NoteFilters, NoteListItem, NoteSourceKind } from "../features/notes/types";

const allowed = new Set(["note", "query", "tags", "source_kind", "qa_message_id", "citation_id"]);
const safeError = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

export function NotesPage() {
  const [params, setParams] = useSearchParams();
  const capabilities = useNotesCapabilities();
  const enabled = capabilities.data?.enabled !== false;
  const selectedId = params.get("note") ?? undefined;
  const [queryInput, setQueryInput] = useState(params.get("query") ?? "");
  const [tagInput, setTagInput] = useState(params.get("tags") ?? "");
  const filters = useMemo<NoteFilters>(() => ({ query: params.get("query") ?? "", tags: (params.get("tags") ?? "").split(",").map((tag) => tag.trim()).filter(Boolean), source_kind: (params.get("source_kind") ?? "") as NoteSourceKind | "", limit: 20 }), [params]);
  const notes = useNotesQuery(filters, enabled);
  const selected = useNoteQuery(selectedId && selectedId !== "new" ? selectedId : undefined, enabled);
  const mutations = useNoteMutations();

  useEffect(() => { setQueryInput(params.get("query") ?? ""); setTagInput(params.get("tags") ?? ""); }, [params]);
  useEffect(() => {
    const next = new URLSearchParams();
    for (const [key, value] of params) if (allowed.has(key) && value) next.set(key, value);
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }, [params, setParams]);

  function updateFilter(key: "query" | "tags" | "source_kind", value: string) {
    const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); next.delete("note"); setParams(next);
  }
  function select(id: string) { const next = new URLSearchParams(params); next.set("note", id); setParams(next); }
  function create() { const next = new URLSearchParams(params); next.set("note", "new"); setParams(next); }
  function save(input: NoteSaveInput) {
    if (selectedId && selectedId !== "new" && selected.data) return mutations.update.mutateAsync({ id: selectedId, input: { body_markdown: input.body_markdown, concept: input.concept, tags: input.tags, expected_version: input.expected_version ?? selected.data.version } });
    const sourceKind = params.get("source_kind");
    return mutations.create.mutateAsync({ body_markdown: input.body_markdown, concept: input.concept, tags: input.tags, client_request_id: input.client_request_id ?? crypto.randomUUID(), source: sourceKind === "qa_answer" || sourceKind === "qa_citation" ? { kind: sourceKind, qa_message_id: params.get("qa_message_id") ?? "" , ...(sourceKind === "qa_citation" && params.get("citation_id") ? { citation_id: params.get("citation_id")! } : {}) } : null }).then((note) => { select(note.id); return note; });
  }
  function remove() { if (selectedId && selected.data) void mutations.remove.mutateAsync({ id: selectedId, input: { expected_version: selected.data.version } }).then(() => { const next = new URLSearchParams(params); next.delete("note"); setParams(next); }).catch(() => undefined); }
  function clear() { return mutations.clear.mutateAsync(); }
  function back() { const next = new URLSearchParams(params); next.delete("note"); setParams(next); }

  if (capabilities.isPending) return <div className="notes-state" role="status">正在加载笔记…</div>;
  if (capabilities.error) return <section className="notes-state" role="alert"><h1>学习笔记</h1><p>{safeError(capabilities.error, "笔记状态加载失败")}</p></section>;
  if (!enabled) return <section className="notes-state"><h1>学习笔记正在迁移</h1><p>此部署尚未启用新版笔记路由。历史数据不会被修改；启用后可继续使用持久笔记。</p></section>;
  if (notes.error && !notes.data) return <section className="notes-state" role="alert"><p>{safeError(notes.error, "笔记加载失败")}</p><Button hierarchy="secondary" onClick={() => void notes.refetch()}>重新加载</Button></section>;
  return <article className="notes-page">
    <div className="notes-filters" aria-label="笔记筛选"><label>搜索笔记<input aria-label="搜索笔记" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") updateFilter("query", queryInput.trim()); }} placeholder="搜索正文或概念" /></label><label>标签<input aria-label="按标签筛选" value={tagInput} onChange={(event) => setTagInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") updateFilter("tags", tagInput.trim()); }} placeholder="多个标签用逗号分隔" /></label><label>来源<select aria-label="按来源筛选" value={filters.source_kind ?? ""} onChange={(event) => updateFilter("source_kind", event.target.value)}><option value="">全部来源</option><option value="qa_message">问答回答</option><option value="qa_citation">问答引用</option></select></label></div>
    <NotesWorkspace items={notes.items as NoteListItem[]} selectedId={selectedId} selectedNote={selected.data} hasMore={Boolean(notes.hasNextPage)} loadingMore={notes.isFetchingNextPage} saving={mutations.create.isPending || mutations.update.isPending} clearing={mutations.clear.isPending} actionError={mutations.remove.error ?? mutations.clear.error} onSelect={select} onSave={save} onCreate={create} onDelete={remove} onClear={clear} onRetryProjection={() => void mutations.retryProjection.mutateAsync()} onLoadMore={() => void notes.fetchNextPage()} onReload={() => void selected.refetch()} onBack={back} />
  </article>;
}
