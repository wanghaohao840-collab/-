import { useEffect, useRef } from "react";
import { Button } from "../Button/Button";

export function NoteClearDialog({ pending, onClose, onConfirm, returnFocusTo }: { pending: boolean; onClose: () => void; onConfirm: () => void; returnFocusTo?: HTMLElement | null }) {
  const dialog = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => { cancel.current?.focus(); const prior = document.body.style.overflow; document.body.style.overflow = "hidden"; return () => { document.body.style.overflow = prior; returnFocusTo?.focus(); }; }, [returnFocusTo]);
  function keyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !pending) { event.preventDefault(); onClose(); }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not([disabled])') ?? []);
    if (focusable.length < 2) return;
    if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable.at(-1)?.focus(); }
    if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0]?.focus(); }
  }
  return <div className="notes-overlay"><button className="notes-overlay__scrim" aria-label="关闭清空确认遮罩" onClick={() => !pending && onClose()} /><div ref={dialog} className="notes-dialog" role="dialog" aria-modal="true" aria-label="清空全部笔记" onKeyDown={keyDown}><h2>清空全部笔记</h2><p>这会删除当前账号的所有笔记，但不会删除文档。</p><div className="notes-dialog__actions"><button ref={cancel} type="button" disabled={pending} onClick={onClose}>取消</button><Button hierarchy="danger" loading={pending} onClick={onConfirm}>确认清空</Button></div></div></div>;
}
