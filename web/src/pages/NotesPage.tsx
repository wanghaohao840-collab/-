import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { Button } from "../components/Button/Button";
import { NotesWorkspace, type NoteSaveInput } from "../components/NotesWorkspace/NotesWorkspace";
import { useNoteMutations, useNoteQuery, useNotesCapabilities, useNotesQuery } from "../features/notes/queries";
import type { NoteFilters, NoteListItem, NotePrefillSource, NoteSourceKind } from "../features/notes/types";

const allowed = new Set(["note", "query", "tags", "source_kind", "qa_message_id", "citation_id"]);
const listSourceKinds = new Set(["qa_message", "qa_citation", "document_chunk"]);
const safeError = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

export function notesListFilters(params: URLSearchParams): NoteFilters {
  const raw = params.get("source_kind") ?? "";
  const prefill = validatedPrefillSource(params);
  return { query: params.get("query") ?? "", tags: (params.get("tags") ?? "").split(",").map((tag) => tag.trim()).filter(Boolean), source_kind: listSourceKinds.has(raw) && !prefill ? raw as NoteSourceKind : "", limit: 20 };
}

export function validatedPrefillSource(params: URLSearchParams): NotePrefillSource | null {
  const kind = params.get("source_kind");
  const qaMessageId = params.get("qa_message_id")?.trim() ?? "";
  const citationId = params.get("citation_id")?.trim() ?? "";
  if (kind === "qa_answer" && qaMessageId) return { kind, qa_message_id: qaMessageId };
  if (kind === "qa_citation" && qaMessageId && citationId) return { kind, qa_message_id: qaMessageId, citation_id: citationId };
  return null;
}

export function historyRestoreDelta(previousIndex: unknown, currentIndex: unknown): number {
  return typeof previousIndex === "number" && typeof currentIndex === "number" ? previousIndex - currentIndex : 0;
}

export function NotesPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const capabilities = useNotesCapabilities();
  const enabled = capabilities.data?.enabled !== false;
  const prefillSource = useMemo(() => validatedPrefillSource(params), [params]);
  const selectedId = params.get("note") ?? (prefillSource ? "new" : undefined);
  const [queryInput, setQueryInput] = useState(params.get("query") ?? "");
  const [tagInput, setTagInput] = useState(params.get("tags") ?? "");
  const [dirty, setDirty] = useState(false);
  const rawSourceKind = params.get("source_kind") ?? "";
  const prefillSourceKind = prefillSource?.kind ?? "";
  const listSourceKind = listSourceKinds.has(rawSourceKind) && !prefillSourceKind ? rawSourceKind : "";
  const filters = useMemo(() => notesListFilters(params), [params]);
  const notes = useNotesQuery(filters, enabled);
  const selected = useNoteQuery(selectedId && selectedId !== "new" ? selectedId : undefined, enabled);
  const mutations = useNoteMutations();
  const confirmDiscardIfDirty = useCallback(() => { if (!dirty) return true; if (!window.confirm("当前笔记有未保存更改，确定离开吗？")) return false; setDirty(false); return true; }, [dirty]);

  useEffect(() => { setQueryInput(params.get("query") ?? ""); setTagInput(params.get("tags") ?? ""); }, [params]);
  useEffect(() => {
    const next = new URLSearchParams();
    for (const [key, value] of params) if (allowed.has(key) && value) {
      if (key === "source_kind" && !listSourceKinds.has(value) && value !== prefillSourceKind) continue;
      if (key === "qa_message_id" && !prefillSource) continue;
      if (key === "citation_id" && (!prefillSource || prefillSource.kind === "qa_answer")) continue;
      next.set(key, value);
    }
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }, [params, prefillSourceKind, setParams]);
  useEffect(() => { const onClick = (event: MouseEvent) => { if (!dirty || event.defaultPrevented) return; const target = event.target as HTMLElement | null; const link = target?.closest<HTMLAnchorElement>("a[href]"); if (!link || link.target === "_blank" || link.origin !== window.location.origin) return; const nextPath = `${link.pathname}${link.search}${link.hash}`; const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`; if (nextPath === currentPath) return; if (!confirmDiscardIfDirty()) { event.preventDefault(); event.stopPropagation(); } }; document.addEventListener("click", onClick, true); return () => document.removeEventListener("click", onClick, true); }, [confirmDiscardIfDirty, dirty]);
  const popstateRestoring = useRef(false);
  useEffect(() => { if (!dirty) return; const previousIndex = window.history.state?.idx; const onPopState = () => { if (popstateRestoring.current) { popstateRestoring.current = false; return; } if (confirmDiscardIfDirty()) return; const currentIndex = window.history.state?.idx; const delta = historyRestoreDelta(previousIndex, currentIndex); popstateRestoring.current = true; if (delta) window.history.go(delta); else window.history.forward(); }; window.addEventListener("popstate", onPopState); return () => window.removeEventListener("popstate", onPopState); }, [confirmDiscardIfDirty, dirty]);

  function updateFilter(key: "query" | "tags" | "source_kind", value: string) {
    if (!confirmDiscardIfDirty()) { setQueryInput(params.get("query") ?? ""); setTagInput(params.get("tags") ?? ""); return false; }
    const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); if (key === "source_kind") { next.delete("qa_message_id"); next.delete("citation_id"); } next.delete("note"); setParams(next); return true;
  }
  function filterChange(key: "query" | "tags" | "source_kind", value: string) {
    if (updateFilter(key, value)) { if (key === "query") setQueryInput(value); if (key === "tags") setTagInput(value); }
  }
  function select(id: string) { if (!confirmDiscardIfDirty()) return false; const next = new URLSearchParams(params); next.set("note", id); setParams(next); return true; }
  function create() { if (!confirmDiscardIfDirty()) return false; const next = new URLSearchParams(params); next.set("note", "new"); setParams(next); return true; }
  function save(input: NoteSaveInput) {
    if (selectedId && selectedId !== "new" && selected.data) return mutations.update.mutateAsync({ id: selectedId, input: { body_markdown: input.body_markdown, concept: input.concept, tags: input.tags, expected_version: input.expected_version ?? selected.data.version } });
    return mutations.create.mutateAsync({ body_markdown: input.body_markdown, concept: input.concept, tags: input.tags, client_request_id: input.client_request_id ?? crypto.randomUUID(), source: prefillSource });
  }
  function commitSelection(id: string) { const next = new URLSearchParams(params); next.set("note", id); setParams(next); }
  function remove() { if (selectedId && selected.data) void mutations.remove.mutateAsync({ id: selectedId, input: { expected_version: selected.data.version } }).then(() => { const next = new URLSearchParams(params); next.delete("note"); setParams(next); }).catch(() => undefined); }
  function clear() { return mutations.clear.mutateAsync().then((result) => { const next = new URLSearchParams(params); next.delete("note"); setParams(next); return result; }); }
  function back() { if (!confirmDiscardIfDirty()) return false; const next = new URLSearchParams(params); next.delete("note"); setParams(next); return true; }
  function openQa() { if (!confirmDiscardIfDirty()) return false; navigate("/qa"); return true; }

  if (capabilities.isPending) return <div className="notes-state" role="status">正在加载笔记…</div>;
  if (capabilities.error) return <section className="notes-state" role="alert"><h1>学习笔记</h1><p>{safeError(capabilities.error, "笔记状态加载失败")}</p></section>;
  if (!enabled) return <section className="notes-state"><h1>学习笔记正在迁移</h1><p>此部署尚未启用新版笔记路由。历史数据不会被修改；启用后可继续使用持久笔记。</p></section>;
  if (notes.error && !notes.data) return <section className="notes-state" role="alert"><p>{safeError(notes.error, "笔记加载失败")}</p><Button hierarchy="secondary" onClick={() => void notes.refetch()}>重新加载</Button></section>;
  return <article className="notes-page">
    <NotesWorkspace items={notes.items as NoteListItem[]} selectedId={selectedId} selectedNote={selected.data} selectedLoading={selected.isPending} selectedError={selected.error} hasMore={Boolean(notes.hasNextPage)} loadingMore={notes.isFetchingNextPage} saving={mutations.create.isPending || mutations.update.isPending} clearing={mutations.clear.isPending} actionError={mutations.remove.error ?? mutations.clear.error ?? mutations.retryProjection.error} onSelect={select} onSave={save} onCommitted={commitSelection} onCreate={create} onDelete={remove} onClear={clear} onRetryProjection={() => mutations.retryProjection.mutateAsync()} onLoadMore={() => void notes.fetchNextPage()} onReload={async () => (await selected.refetch()).data} onBack={back} onDirtyChange={setDirty} onOpenQa={openQa} queryValue={queryInput} tagsValue={tagInput} sourceValue={listSourceKind} onFilterChange={filterChange} />
  </article>;
}
