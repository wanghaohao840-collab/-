import { useState } from "react";
import { Button } from "../Button/Button";
import { MarkdownPreview } from "../MarkdownPreview/MarkdownPreview";

import { NoteMetadataForm } from "./NoteMetadataForm";
import { NoteEditorTabs } from "./NoteEditorTabs";
import { headingTitle, withTitle, noteTime } from "./notePresentation";

export type EditorDraft = { body_markdown: string; concept: string; tags: string[] };
type Props = { updatedAt?: string; draft: EditorDraft; dirty: boolean; saving: boolean; disabled?: boolean; error?: string; onChange: (draft: EditorDraft) => void; onSave: () => void; onDelete?: (trigger: HTMLElement) => void; onOpenSources?: () => void; onBack?: () => void };

export function NoteEditor({ updatedAt, draft, dirty, saving, disabled = false, error, onChange, onSave, onDelete, onOpenSources, onBack }: Props) {
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  const saveButton = <Button className="notes-editor__save" disabled={!dirty || !draft.body_markdown.trim() || draft.body_markdown.length > 20_000 || saving || disabled} loading={saving} onClick={onSave}>保存笔记</Button>;
  return <section className="notes-editor" aria-label="笔记编辑器">
    <header className="notes-editor__heading">{onBack ? <button type="button" className="notes-editor__back" aria-label="返回笔记列表" onClick={onBack}>←</button> : null}<div className="notes-editor__title"><span className="notes-eyebrow">笔记工作区</span><input className="notes-title-input" aria-label="笔记标题" placeholder="为这份理解命名" value={headingTitle(draft.body_markdown)} onChange={(event) => onChange({ ...draft, body_markdown: withTitle(draft.body_markdown, event.target.value) })} /><p>{saving ? "正在保存…" : dirty ? "有未保存更改" : updatedAt ? "已保存" : "尚未保存"}</p></div>{saveButton}</header>
    {draft.body_markdown.length > 20_000 ? <p role="alert" className="notes-error">正文含标题最多 20,000 字，请缩短后保存。</p> : null}
    {error ? <p className="notes-error" role="alert" tabIndex={-1}>{error}</p> : null}
    <NoteEditorTabs mode={mode} onChange={setMode} />
    {mode === "edit" ? <div className="notes-form">
      <NoteMetadataForm draft={draft} onChange={onChange} />
      <label>笔记正文<textarea aria-label="笔记正文" placeholder="写下你的理解、概念关系、关键结论与可复习内容……" value={draft.body_markdown} onChange={(event) => onChange({ ...draft, body_markdown: event.target.value })} rows={12} maxLength={20_000} /></label>
    </div> : <div className="notes-editor__preview"><MarkdownPreview markdown={draft.body_markdown} /></div>}
    <footer className="notes-editor__footer"><p>{dirty ? "尚未保存" : updatedAt ? `最近保存于 ${noteTime(updatedAt)}` : "理解，值得被留下"}</p><div className="notes-editor__actions">{onOpenSources ? <Button hierarchy="secondary" aria-label="来源" onClick={onOpenSources}>查看来源</Button> : null}{onDelete ? <Button hierarchy="danger" disabled={saving} onClick={(event) => onDelete(event.currentTarget)}>删除笔记</Button> : null}</div></footer>
  </section>;
}
