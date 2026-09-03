import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { Note, NoteListItem } from "../../features/notes/types";
import { Button } from "../Button/Button";
import { NoteClearDialog } from "./NoteClearDialog";
import { NoteEditor, type EditorDraft } from "./NoteEditor";
import { NoteList } from "./NoteList";
import { NoteSourcePanel } from "./NoteSourcePanel";
import "./notes-workspace.css";

export type NoteSaveInput = { body_markdown: string; concept: string | null; tags: string[]; expected_version?: number; client_request_id?: string };
type Props = {
  items: NoteListItem[]; selectedNote?: Note; selectedId?: string; selectedLoading?: boolean; selectedError?: unknown; hasMore?: boolean; loadingMore?: boolean; saving?: boolean; actionError?: unknown;
  onSelect: (id: string) => boolean | void; onSave: (input: NoteSaveInput) => Promise<Note | undefined> | Note | undefined; onCommitted?: (id: string) => void; onCreate: () => boolean | void; onDelete: () => void; onClear: () => Promise<unknown> | unknown; onRetryProjection: () => Promise<unknown> | unknown; onLoadMore?: () => void; onReload?: () => Promise<Note | undefined> | Note | undefined; onBack?: () => boolean | void; clearing?: boolean; onOpenQa?: () => boolean | void;
  queryValue?: string; tagsValue?: string; sourceValue?: string; onFilterChange?: (key: "query" | "tags" | "source_kind", value: string) => void; onDirtyChange?: (dirty: boolean) => void;
};

const emptyDraft: EditorDraft = { body_markdown: "", concept: "", tags: [] };
const message = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback;

function NotesOverlay({ label, onClose, returnFocusTo, children }: { label: string; onClose: () => void; returnFocusTo?: HTMLElement | null; children: React.ReactNode }) {
  const panel = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => { const prior = document.body.style.overflow; document.body.style.overflow = "hidden"; panel.current?.querySelector<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled])')?.focus(); const keyDown = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; } if (event.key !== "Tab") return; const focusable = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href]') ?? []); const first = focusable[0]; const last = focusable.at(-1); if (!first || !last) return; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }; window.addEventListener("keydown", keyDown); return () => { window.removeEventListener("keydown", keyDown); document.body.style.overflow = prior; returnFocusTo?.focus(); }; }, [returnFocusTo]);
  return <div className="notes-overlay"><button className="notes-overlay__scrim" aria-label={`关闭${label}遮罩`} onClick={onClose} /><section ref={panel} className="notes-dialog" role="dialog" aria-modal="true" aria-label={label}>{children}</section></div>;
}

export function NotesWorkspace({ items, selectedNote, selectedId, selectedLoading = false, selectedError, hasMore = false, loadingMore = false, saving = false, actionError, onSelect, onSave, onCommitted, onCreate, onDelete, onClear, onRetryProjection, onLoadMore = () => undefined, onReload, onBack, clearing = false, onOpenQa = () => undefined, queryValue = "", tagsValue = "", sourceValue = "", onFilterChange, onDirtyChange }: Props) {
  const [draft, setDraft] = useState<EditorDraft>(selectedNote ? { body_markdown: selectedNote.body_markdown, concept: selectedNote.concept ?? "", tags: selectedNote.tags } : emptyDraft);
  const [baseVersion, setBaseVersion] = useState<number | undefined>(selectedNote?.version);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [saveError, setSaveError] = useState<string>();
  const sourceTrigger = useRef<HTMLButtonElement>(null);
  const filterTrigger = useRef<HTMLButtonElement>(null);
  const clearTrigger = useRef<HTMLButtonElement>(null);
  const overlayReturn = useRef<HTMLElement | null>(null);
  const baseline = useRef<EditorDraft | null>(selectedNote ? { body_markdown: selectedNote.body_markdown, concept: selectedNote.concept ?? "", tags: selectedNote.tags } : selectedId === "new" ? emptyDraft : null);
  const [hydratedId, setHydratedId] = useState<string | undefined>(selectedNote?.id ?? (selectedId === "new" ? "new" : undefined));
  const [listView, setListView] = useState(!selectedId);
  const [copyFeedback, setCopyFeedback] = useState("");
  const activeId = selectedId ?? selectedNote?.id;
  const previousActiveId = useRef(activeId);
  const dirty = useMemo(() => baseline.current ? draft.body_markdown !== baseline.current.body_markdown || draft.concept !== baseline.current.concept || draft.tags.join(",") !== baseline.current.tags.join(",") : false, [draft]);

  useEffect(() => { if (previousActiveId.current === activeId) return; previousActiveId.current = activeId; setHydratedId(activeId === "new" ? "new" : undefined); setDraft(emptyDraft); baseline.current = activeId === "new" ? emptyDraft : null; setBaseVersion(undefined); setListView(!activeId); setSaveError(undefined); }, [activeId]);
  useEffect(() => { if (!selectedNote || selectedNote.id !== activeId) return; const serverDraft = { body_markdown: selectedNote.body_markdown, concept: selectedNote.concept ?? "", tags: selectedNote.tags }; if (!hydratedId || hydratedId !== selectedNote.id) { setDraft(serverDraft); baseline.current = serverDraft; setBaseVersion(selectedNote.version); setHydratedId(selectedNote.id); return; } if (!dirty && selectedNote.version !== baseVersion) { setDraft(serverDraft); baseline.current = serverDraft; setBaseVersion(selectedNote.version); } }, [activeId, baseVersion, dirty, hydratedId, selectedNote]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { const handler = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } }; window.addEventListener("beforeunload", handler); return () => window.removeEventListener("beforeunload", handler); }, [dirty]);

  async function save() {
    setSaveError(undefined);
    try { overlayReturn.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; const wasNew = !selectedNote; const saved = await onSave({ body_markdown: draft.body_markdown, concept: draft.concept.trim() || null, tags: draft.tags, ...(selectedNote ? { expected_version: baseVersion } : { client_request_id: crypto.randomUUID() }) }); if (saved) { const next = { body_markdown: saved.body_markdown, concept: saved.concept ?? "", tags: saved.tags }; setDraft(next); baseline.current = next; setBaseVersion(saved.version); setHydratedId(saved.id); if (wasNew) onCommitted?.(saved.id); } }
    catch (error) { if (error instanceof ApiError && error.code === "NOTE_VERSION_CONFLICT") setConflictOpen(true); else setSaveError(message(error, "保存失败，请重试")); }
  }
  function select(id: string) { if (onSelect(id) !== false) setListView(false); }
  const sources = selectedNote?.sources ?? [];
  const detailUnavailable = Boolean(selectedId && selectedId !== "new" && selectedError && hydratedId === selectedId);
  const detailWaiting = Boolean(selectedId && selectedId !== "new" && selectedLoading && hydratedId !== selectedId);
  async function copyDraft() { try { await navigator.clipboard?.writeText(draft.body_markdown); setCopyFeedback("本地草稿已复制"); } catch { setCopyFeedback("复制失败，请手动选择草稿"); } }
  async function reloadConflict() { const server = await onReload?.(); if (server) { const next = { body_markdown: server.body_markdown, concept: server.concept ?? "", tags: server.tags }; setDraft(next); baseline.current = next; setBaseVersion(server.version); setHydratedId(server.id); setConflictOpen(false); } }
  return <article className={`notes-workspace ${listView ? "notes-workspace--list" : "notes-workspace--editor"}`}>
    <header className="notes-toolbar"><div className="notes-toolbar__intro"><h1>学习笔记</h1><p>整理理解、来源与概念，形成可持续复习的个人知识。</p></div><div className="notes-toolbar__actions"><Button className="notes-create-action" onClick={() => { if (onCreate() !== false) setListView(false); }}>新建笔记</Button><button ref={filterTrigger} type="button" className="notes-mobile-action" onClick={() => setFiltersOpen(true)}>筛选</button><button ref={sourceTrigger} type="button" className="notes-mobile-action notes-mobile-action--source" onClick={() => setSourceOpen(true)} disabled={!selectedNote}>来源</button><button ref={clearTrigger} type="button" className="notes-clear-action" onClick={() => setClearOpen(true)}>清空笔记</button></div><div className="notes-toolbar__filters" aria-label="笔记筛选"><label>搜索笔记<input aria-label="搜索笔记" value={queryValue} onChange={(event) => onFilterChange?.("query", event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onFilterChange?.("query", event.currentTarget.value.trim()); }} placeholder="搜索标题、正文或概念" /></label><label className="notes-toolbar__compact-filter">标签<input aria-label="按标签筛选" value={tagsValue} onChange={(event) => onFilterChange?.("tags", event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onFilterChange?.("tags", event.currentTarget.value.trim()); }} placeholder="标签" /></label><label className="notes-toolbar__compact-filter">来源<select aria-label="按来源筛选" value={sourceValue} onChange={(event) => onFilterChange?.("source_kind", event.target.value)}><option value="">全部来源</option><option value="qa_message">问答回答</option><option value="qa_citation">问答引用</option></select></label></div></header>
    {detailWaiting ? <p className="notes-loading" role="status">正在加载笔记详情…</p> : null}
    {detailUnavailable ? <p className="notes-error" role="alert">服务端笔记已删除或不可用，本地草稿仍保留。<button type="button" onClick={copyDraft}>复制本地草稿</button>{copyFeedback ? <span>{copyFeedback}</span> : null}</p> : null}
    {actionError ? <p className="notes-error" role="alert">{message(actionError, "笔记操作失败，请重试")}</p> : null}
    <div className="notes-grid"><NoteList items={items} selectedId={selectedId} hasMore={hasMore} loadingMore={loadingMore} onSelect={select} onLoadMore={onLoadMore} onOpenQa={() => onOpenQa()} onCreate={() => { if (onCreate() !== false) setListView(false); }} />
      {activeId === "new" || hydratedId === activeId || detailUnavailable ? <NoteEditor draft={draft} dirty={dirty} saving={saving} disabled={detailUnavailable} error={saveError} onChange={setDraft} onSave={save} onOpenSources={selectedNote ? () => setSourceOpen(true) : undefined} onBack={selectedId && onBack ? () => { if (onBack() !== false) setListView(true); } : undefined} onDelete={selectedNote ? (trigger) => { overlayReturn.current = trigger; setDeleteOpen(true); } : undefined} /> : <section className="notes-welcome"><h2>选择一篇笔记</h2><p>从列表中打开笔记，或创建一篇新的学习笔记。</p></section>}
      <NoteSourcePanel sources={sources} />
    </div>
    {selectedNote?.projection_state === "pending" ? <p className="notes-projection-status" role="status">正在同步到学习记忆，笔记仍可编辑。</p> : null}
    {selectedNote?.projection_state === "failed" ? <p className="notes-projection-error" role="status">记忆投影失败，不影响笔记保存。<button type="button" onClick={() => { void Promise.resolve(onRetryProjection()).catch((error) => setSaveError(message(error, "投影重试失败，请重试"))); }}>重试投影</button></p> : null}
    {sourceOpen ? <NoteSourcePanel sources={sources} asDialog returnFocusTo={sourceTrigger.current} onClose={() => setSourceOpen(false)} /> : null}
    {filtersOpen ? <NotesOverlay label="筛选笔记" returnFocusTo={filterTrigger.current} onClose={() => setFiltersOpen(false)}><h2>筛选笔记</h2><label>搜索笔记<input aria-label="筛选中的搜索笔记" value={queryValue} onChange={(event) => onFilterChange?.("query", event.target.value)} /></label><label>标签<input aria-label="筛选中的标签" value={tagsValue} onChange={(event) => onFilterChange?.("tags", event.target.value)} /></label><label>来源<select aria-label="筛选中的来源" value={sourceValue} onChange={(event) => onFilterChange?.("source_kind", event.target.value)}><option value="">全部来源</option><option value="qa_message">问答回答</option><option value="qa_citation">问答引用</option></select></label><button type="button" className="notes-sheet__close" onClick={() => setFiltersOpen(false)}>完成</button></NotesOverlay> : null}
    {clearOpen ? <NoteClearDialog pending={clearing} returnFocusTo={clearTrigger.current} onClose={() => setClearOpen(false)} onConfirm={async () => { try { await onClear(); setClearOpen(false); } catch { /* error remains in the page and the dialog stays recoverable */ } }} /> : null}
    {deleteOpen ? <NotesOverlay label="删除笔记" returnFocusTo={overlayReturn.current} onClose={() => !saving && setDeleteOpen(false)}><h2>删除笔记</h2><p>确认删除这篇笔记？此操作不可撤销。</p><div className="notes-dialog__actions"><button type="button" disabled={saving} onClick={() => setDeleteOpen(false)}>取消</button><button type="button" disabled={saving} onClick={() => { setDeleteOpen(false); onDelete(); }}>确认删除</button></div></NotesOverlay> : null}
    {conflictOpen ? <NotesOverlay label="笔记已在其他窗口更新" returnFocusTo={overlayReturn.current} onClose={() => setConflictOpen(false)}><h2>笔记已在其他窗口更新</h2><p>本地草稿未被覆盖。请选择如何继续。</p>{copyFeedback ? <p role="status">{copyFeedback}</p> : null}<div className="notes-dialog__actions"><button type="button" onClick={copyDraft}>复制本地草稿</button><button type="button" onClick={() => void reloadConflict().catch((error) => setSaveError(message(error, "重新加载失败，请重试")))}>重新加载服务端版本</button><button type="button" onClick={() => setConflictOpen(false)}>取消并继续查看本地草稿</button></div></NotesOverlay> : null}
  </article>;
}
