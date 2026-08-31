import { useEffect, useRef } from "react";
import type { NoteSource, NoteListSource } from "../../features/notes/types";

type Source = NoteSource | NoteListSource;
export function NoteSourcePanel({ sources, onClose, asDialog = false, returnFocusTo }: { sources: Source[]; onClose?: () => void; asDialog?: boolean; returnFocusTo?: HTMLElement | null }) {
  const panel = useRef<HTMLElement>(null);
  useEffect(() => { if (!asDialog) return; const prior = document.body.style.overflow; document.body.style.overflow = "hidden"; panel.current?.querySelector<HTMLElement>("button")?.focus(); const keyDown = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onClose?.(); } }; window.addEventListener("keydown", keyDown); return () => { window.removeEventListener("keydown", keyDown); document.body.style.overflow = prior; returnFocusTo?.focus(); }; }, [asDialog, onClose, returnFocusTo]);
  const content = <>
    <header className="notes-source__heading"><h2>来源</h2>{onClose ? <button type="button" className="notes-icon-button" aria-label="关闭来源" onClick={onClose}>×</button> : null}</header>
    {!sources.length ? <p className="notes-muted">这是一篇手动笔记，暂无来源。</p> : <ol>{sources.map((source, index) => <li key={source.id ?? `${source.kind}-${index}`}>
      {source.deleted ? <p className="notes-source__deleted">来源已删除</p> : <><strong>{"title_snapshot" in source ? source.title_snapshot || "问答来源" : "问答来源"}</strong>{"excerpt_snapshot" in source && source.excerpt_snapshot ? <p>{source.excerpt_snapshot}</p> : null}<small>{source.kind === "qa_citation" ? "问答引用" : "问答回答"}</small></>}
    </li>)}</ol>}
  </>;
  if (!asDialog) return <aside className="notes-source" aria-label="笔记来源">{content}</aside>;
  return <div className="notes-overlay" role="presentation"><button className="notes-overlay__scrim" aria-label="关闭来源遮罩" onClick={onClose} /><section ref={panel} className="notes-sheet" role="dialog" aria-modal="true" aria-label="笔记来源">{content}</section></div>;
}
