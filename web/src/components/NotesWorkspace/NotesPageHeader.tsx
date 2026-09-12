import { Button } from "../Button/Button";

export function NotesPageHeader({ onCreate, onReset, onClear, filtered }: { onCreate: () => void; onReset?: () => void; onClear: (trigger: HTMLElement) => void; filtered: boolean }) {
  return <header className="notes-toolbar">
    <div className="notes-toolbar__intro"><span className="notes-eyebrow">从理解到沉淀</span><h1>学习笔记</h1><p>整理理解、来源与概念，形成可持续复习的个人知识。</p></div>
    <div className="notes-toolbar__actions">
      <Button className="notes-create-action" onClick={onCreate}>新建笔记</Button>
      <button type="button" className="notes-text-action" disabled={!filtered} onClick={onReset}>清空筛选</button>
      <details className="notes-more"><summary aria-label="更多笔记操作">更多操作</summary><button type="button" onClick={(event) => { const parent = event.currentTarget.closest("details"); if (parent) parent.open = false; onClear(parent?.querySelector("summary") ?? event.currentTarget); }}>清空笔记</button></details>
    </div>
  </header>;
}
