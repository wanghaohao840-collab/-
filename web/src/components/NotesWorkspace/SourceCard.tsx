import { useState } from "react";
import type { NoteSource, NoteListSource } from "../../features/notes/types";
import { sourceLabel } from "./notePresentation";

export function SourceCard({ source }: { source: NoteSource | NoteListSource }) {
  const [feedback, setFeedback] = useState("");
  if (source.deleted) return <li className="notes-source-card"><span className="notes-source__deleted">来源已删除</span><p>原始来源已移除，这篇笔记中的理解仍保留。</p></li>;
  const detail = "locator" in source ? source : null;
  const page = detail?.locator?.page ?? detail?.locator?.page_number;
  async function copy() {
    try { if (!navigator.clipboard) throw new Error(); await navigator.clipboard.writeText(JSON.stringify(detail?.locator)); setFeedback("来源定位已复制"); }
    catch { setFeedback("复制失败，请重试"); }
  }
  return <li className="notes-source-card">
    <div className="notes-source-card__meta"><span>{sourceLabel(source.kind)}</span>{typeof page === "number" ? <span>第 {page} 页</span> : null}</div>
    {detail?.citation_id ? <code className="notes-citation-id">{detail.citation_id}</code> : null}
    <strong>{detail?.title_snapshot || (source.kind === "document_chunk" ? "文档来源" : "问答来源")}</strong>
    {detail?.excerpt_snapshot ? <blockquote>{detail.excerpt_snapshot}</blockquote> : <p className="notes-muted">暂无证据摘要</p>}
    <div className="notes-source-card__actions">
      {detail?.qa_thread_id ? <a href={`/qa?conversation=${encodeURIComponent(detail.qa_thread_id)}`}>查看问答原文 ↗</a> : null}
      {source.document_id ? <a href={`/search?documents=${encodeURIComponent(source.document_id)}`}>检索该文档</a> : null}
      {detail?.locator ? <button type="button" className="notes-source__copy" onClick={() => void copy()}>复制来源定位</button> : null}
    </div>
    {feedback ? <span role="status">{feedback}</span> : null}
  </li>;
}
