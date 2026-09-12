import type { EditorDraft } from "./NoteEditor";
export function NoteMetadataForm({ draft, onChange }: { draft: EditorDraft; onChange: (draft: EditorDraft) => void }) {
  return <div className="notes-metadata">
    <label>概念<input placeholder="记录核心概念" value={draft.concept} maxLength={120} onChange={(e) => onChange({ ...draft, concept: e.target.value })} /></label>
    <label>标签<input aria-label="笔记标签" placeholder="添加标签，以逗号分隔" value={draft.tags.join(", ")} onChange={(e) => onChange({ ...draft, tags: e.target.value.split(",").map((tag) => tag.trim()).filter(Boolean) })} /></label>
  </div>;
}
