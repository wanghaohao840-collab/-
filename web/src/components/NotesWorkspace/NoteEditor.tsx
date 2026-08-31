import { useState } from "react";
import { Button } from "../Button/Button";
import { MarkdownPreview } from "../MarkdownPreview/MarkdownPreview";

export type EditorDraft = { body_markdown: string; concept: string; tags: string[] };
type Props = { draft: EditorDraft; dirty: boolean; saving: boolean; disabled?: boolean; error?: string; onChange: (draft: EditorDraft) => void; onSave: () => void; onDelete?: (trigger: HTMLElement) => void };

export function NoteEditor({ draft, dirty, saving, disabled = false, error, onChange, onSave, onDelete }: Props) {
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  return <section className="notes-editor" aria-label="笔记编辑器">
    <header className="notes-editor__heading"><div><h2>{draft.concept || "新建笔记"}</h2><p>{dirty ? "有未保存更改" : "已保存"}</p></div><div className="notes-editor__actions"><Button hierarchy="secondary" disabled={!dirty || saving || disabled} loading={saving} onClick={onSave}>保存笔记</Button>{onDelete ? <Button hierarchy="danger" disabled={saving} onClick={(event) => onDelete(event.currentTarget)}>删除笔记</Button> : null}</div></header>
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
  </section>;
}
