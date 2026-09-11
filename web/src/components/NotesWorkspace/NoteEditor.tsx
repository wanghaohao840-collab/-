import { useState, useSyncExternalStore } from "react";
import { Button } from "../Button/Button";
import { MarkdownPreview } from "../MarkdownPreview/MarkdownPreview";

export type EditorDraft = { body_markdown: string; concept: string; tags: string[] };
type Props = { draft: EditorDraft; dirty: boolean; saving: boolean; disabled?: boolean; error?: string; onChange: (draft: EditorDraft) => void; onSave: () => void; onDelete?: (trigger: HTMLElement) => void; onOpenSources?: () => void; onBack?: () => void };

const mobileEditorQuery = "(max-width: 767px)";

function mobileEditorSnapshot() {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(mobileEditorQuery).matches
    : false;
}

function subscribeToMobileEditor(change: () => void) {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => undefined;
  const query = window.matchMedia(mobileEditorQuery);
  query.addEventListener("change", change);
  return () => query.removeEventListener("change", change);
}

export function NoteEditor({ draft, dirty, saving, disabled = false, error, onChange, onSave, onDelete, onOpenSources, onBack }: Props) {
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  const mobile = useSyncExternalStore(subscribeToMobileEditor, mobileEditorSnapshot, () => false);
  const saveButton = <Button className="notes-editor__save" disabled={!dirty || !draft.body_markdown.trim() || saving || disabled} loading={saving} onClick={onSave}>保存笔记</Button>;
  return <section className="notes-editor" aria-label="笔记编辑器">
    <header className="notes-editor__heading">{onBack ? <button type="button" className="notes-editor__back" aria-label="返回笔记列表" onClick={onBack}>←</button> : null}<div className="notes-editor__title"><h2>{draft.concept || "新建笔记"}</h2><p>{dirty ? "有未保存更改" : "已保存"}</p></div>{mobile ? saveButton : null}</header>
    {error ? <p className="notes-error" role="alert" tabIndex={-1}>{error}</p> : null}
    <div className="notes-editor__tabs" role="tablist" aria-label="笔记内容模式">
      <button type="button" role="tab" aria-selected={mode === "edit"} onClick={() => setMode("edit")}>编辑</button>
      <button type="button" role="tab" aria-selected={mode === "preview"} onClick={() => setMode("preview")}>预览</button>
    </div>
    {mode === "edit" ? <div className="notes-form">
      <label>概念<input value={draft.concept} maxLength={120} onChange={(event) => onChange({ ...draft, concept: event.target.value })} /></label>
      <label>标签<input aria-label="笔记标签" value={draft.tags.join(", ")} onChange={(event) => onChange({ ...draft, tags: event.target.value.split(",").map((tag) => tag.trim()).filter(Boolean) })} /></label>
      <label>笔记正文<textarea aria-label="笔记正文" value={draft.body_markdown} onChange={(event) => onChange({ ...draft, body_markdown: event.target.value })} rows={18} maxLength={20_000} /></label>
    </div> : <div className="notes-editor__preview"><MarkdownPreview markdown={draft.body_markdown} /></div>}
    <footer className="notes-editor__footer"><p>{dirty ? "尚未保存" : "上次保存"}</p><div className="notes-editor__actions">{onOpenSources ? <Button hierarchy="secondary" aria-label="来源" onClick={onOpenSources}>查看来源</Button> : null}{mobile ? null : saveButton}{onDelete ? <Button hierarchy="danger" disabled={saving} onClick={(event) => onDelete(event.currentTarget)}>删除笔记</Button> : null}</div></footer>
  </section>;
}
