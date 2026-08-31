import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { Note, NoteListItem } from "../../features/notes/types";
import { NoteClearDialog } from "./NoteClearDialog";
import { NoteEditor, type EditorDraft } from "./NoteEditor";
import { NoteList } from "./NoteList";
import { NoteSourcePanel } from "./NoteSourcePanel";
import "./notes-workspace.css";

export type NoteSaveInput = { body_markdown: string; concept: string | null; tags: string[]; expected_version?: number; client_request_id?: string };
type Props = {
  items: NoteListItem[]; selectedNote?: Note; selectedId?: string; hasMore?: boolean; loadingMore?: boolean; saving?: boolean; actionError?: unknown;
  onSelect: (id: string) => void; onSave: (input: NoteSaveInput) => Promise<unknown> | unknown; onCreate: () => void; onDelete: () => void; onClear: () => Promise<unknown> | unknown; onRetryProjection: () => void; onLoadMore?: () => void; onReload?: () => void; onBack?: () => void; clearing?: boolean;
};

const emptyDraft: EditorDraft = { body_markdown: "", concept: "", tags: [] };
const message = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback;

export function NotesWorkspace({ items, selectedNote, selectedId, hasMore = false, loadingMore = false, saving = false, actionError, onSelect, onSave, onCreate, onDelete, onClear, onRetryProjection, onLoadMore = () => undefined, onReload, onBack, clearing = false }: Props) {
  const [draft, setDraft] = useState<EditorDraft>(selectedNote ? { body_markdown: selectedNote.body_markdown, concept: selectedNote.concept ?? "", tags: selectedNote.tags } : emptyDraft);
  const [baseVersion, setBaseVersion] = useState<number | undefined>(selectedNote?.version);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [saveError, setSaveError] = useState<string>();
  const sourceTrigger = useRef<HTMLButtonElement>(null);
  const [listView, setListView] = useState(!selectedId);
  const dirty = useMemo(() => draft.body_markdown !== (selectedNote?.body_markdown ?? "") || draft.concept !== (selectedNote?.concept ?? "") || draft.tags.join(",") !== (selectedNote?.tags ?? []).join(","), [draft, selectedNote]);

  useEffect(() => { setDraft(selectedNote ? { body_markdown: selectedNote.body_markdown, concept: selectedNote.concept ?? "", tags: selectedNote.tags } : emptyDraft); setBaseVersion(selectedNote?.version); setListView(!selectedId); setSaveError(undefined); }, [selectedId]);
  useEffect(() => { if (selectedNote && selectedNote.version !== baseVersion && !dirty) { setDraft({ body_markdown: selectedNote.body_markdown, concept: selectedNote.concept ?? "", tags: selectedNote.tags }); setBaseVersion(selectedNote.version); } }, [baseVersion, dirty, selectedNote]);
  useEffect(() => { const handler = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } }; window.addEventListener("beforeunload", handler); return () => window.removeEventListener("beforeunload", handler); }, [dirty]);

  async function save() {
    setSaveError(undefined);
    try { await onSave({ body_markdown: draft.body_markdown, concept: draft.concept.trim() || null, tags: draft.tags, ...(selectedNote ? { expected_version: baseVersion } : { client_request_id: crypto.randomUUID() }) }); }
    catch (error) { if (error instanceof ApiError && error.code === "NOTE_VERSION_CONFLICT") setConflictOpen(true); else setSaveError(message(error, "保存失败，请重试")); }
  }
  function select(id: string) { if (dirty && !window.confirm("当前笔记有未保存更改，确定离开吗？")) return; setListView(false); onSelect(id); }
  const sources = selectedNote?.sources ?? [];
  return <article className={`notes-workspace ${listView ? "notes-workspace--list" : "notes-workspace--editor"}`}>
    <header className="notes-toolbar"><div><h1>学习笔记</h1><p>记录、整理并回顾你的学习过程</p></div><div className="notes-toolbar__actions"><button type="button" className="notes-mobile-action" onClick={() => setFiltersOpen(true)}>筛选</button><button ref={sourceTrigger} type="button" className="notes-mobile-action" onClick={() => setSourceOpen(true)} disabled={!selectedNote}>来源</button><button type="button" className="notes-clear-action" onClick={() => setClearOpen(true)}>清空笔记</button></div></header>
    {actionError ? <p className="notes-error" role="alert">{message(actionError, "笔记操作失败，请重试")}</p> : null}
    <div className="notes-grid"><NoteList items={items} selectedId={selectedId} hasMore={hasMore} loadingMore={loadingMore} onSelect={select} onLoadMore={onLoadMore} onCreate={() => { if (!dirty || window.confirm("当前笔记有未保存更改，确定新建吗？")) { setListView(false); onCreate(); } }} />
      {selectedId === "new" || selectedNote ? <NoteEditor draft={draft} dirty={dirty} saving={saving} error={saveError} onChange={setDraft} onSave={save} onDelete={selectedNote ? () => setDeleteOpen(true) : undefined} /> : <section className="notes-welcome"><h2>选择一篇笔记</h2><p>从列表中打开笔记，或创建一篇新的学习笔记。</p></section>}
      <NoteSourcePanel sources={sources} />
    </div>
    {selectedNote?.projection_state === "failed" ? <p className="notes-projection-error" role="status">记忆投影失败，不影响笔记保存。<button type="button" onClick={onRetryProjection}>重试投影</button></p> : null}
    {sourceOpen ? <NoteSourcePanel sources={sources} asDialog returnFocusTo={sourceTrigger.current} onClose={() => setSourceOpen(false)} /> : null}
    {filtersOpen ? <div className="notes-overlay"><button className="notes-overlay__scrim" aria-label="关闭筛选遮罩" onClick={() => setFiltersOpen(false)} /><section className="notes-sheet" role="dialog" aria-modal="true" aria-label="筛选笔记"><h2>筛选笔记</h2><p>使用上方搜索和标签筛选笔记。</p><button type="button" className="notes-sheet__close" onClick={() => setFiltersOpen(false)}>完成</button></section></div> : null}
    {clearOpen ? <NoteClearDialog pending={clearing} onClose={() => setClearOpen(false)} onConfirm={async () => { try { await onClear(); setClearOpen(false); } catch { /* error remains in the page and the dialog stays recoverable */ } }} /> : null}
    {deleteOpen ? <div className="notes-overlay"><button className="notes-overlay__scrim" aria-label="关闭删除确认遮罩" onClick={() => !saving && setDeleteOpen(false)} /><section className="notes-dialog" role="dialog" aria-modal="true" aria-label="删除笔记"><h2>删除笔记</h2><p>确认删除这篇笔记？此操作不可撤销。</p><div className="notes-dialog__actions"><button type="button" disabled={saving} onClick={() => setDeleteOpen(false)}>取消</button><button type="button" disabled={saving} onClick={() => { setDeleteOpen(false); onDelete(); }}>确认删除</button></div></section></div> : null}
    {conflictOpen ? <div className="notes-overlay"><button className="notes-overlay__scrim" aria-label="关闭冲突遮罩" onClick={() => setConflictOpen(false)} /><section className="notes-dialog" role="dialog" aria-modal="true" aria-label="笔记已在其他窗口更新"><h2>笔记已在其他窗口更新</h2><p>本地草稿未被覆盖。请选择如何继续。</p><div className="notes-dialog__actions"><button type="button" onClick={() => { void navigator.clipboard?.writeText(draft.body_markdown); }}>复制本地草稿</button><button type="button" onClick={() => { setConflictOpen(false); onReload?.(); }}>重新加载服务端版本</button><button type="button" onClick={() => setConflictOpen(false)}>取消并继续查看本地草稿</button></div></section></div> : null}
    {selectedId && onBack ? <button type="button" className="notes-mobile-back" onClick={() => { if (!dirty || window.confirm("当前笔记有未保存更改，确定返回吗？")) onBack(); }}>返回笔记列表</button> : null}
  </article>;
}
