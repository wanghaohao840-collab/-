import { useState } from "react";
import { Link } from "react-router-dom";
import type { SearchResult } from "../../features/search/types";
import { Button } from "../Button/Button";
import { highlightedExcerpt, sourceLocation } from "./presentation";

export function CitationBadge({ rank }: { rank: number }) {
  return <span className="evidence-citation" aria-label={`本次结果编号 ${rank}`} title="本次检索结果编号">S-{String(rank).padStart(3, "0")}</span>;
}

function EvidenceCard({ result, query, onOpen }: {
  result: SearchResult; query: string; onOpen: (result: SearchResult, editing?: boolean) => void;
}) {
  const [feedback, setFeedback] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(`${result.document_name}\n${sourceLocation(result)}\n${result.excerpt}\n文档 ID：${result.document_id}\n片段 ID：${result.locator.chunk_id}`);
      setFeedback("引用已复制");
    } catch { setFeedback("复制失败，请打开来源详情手动复制。"); }
  }
  return <article className="search-result">
    <div className="search-result-top"><CitationBadge rank={result.rank} /><small>{result.score == null ? "相关度未提供" : `相关度 ${result.score.toFixed(3)}`}</small></div>
    <h2>{result.document_name}</h2><p className="search-location">{sourceLocation(result)}</p>
    <p className="search-excerpt">{highlightedExcerpt(result.excerpt, query)}</p>
    <div className="search-result-bottom"><div className="evidence-primary-actions">
      <Button hierarchy="secondary" onClick={() => onOpen(result)} aria-label={`查看来源 ${result.rank}：${result.document_name}`}>查看来源 <span aria-hidden="true">↗</span></Button>
      <Button hierarchy="ghost" onClick={() => onOpen(result, true)} aria-label={`加入笔记 ${result.rank}：${result.document_name}`}>加入笔记 <span aria-hidden="true">＋</span></Button>
    </div><details className="research-more"><summary aria-label={`证据 ${result.rank} 更多操作`}>更多 ···</summary><div>
      <Link to={`/qa?documents=${encodeURIComponent(result.document_id)}`}>基于此文档问答</Link>
      <button onClick={() => void copy()}>复制引用</button>
    </div></details></div>
    {feedback ? <p className="search-muted" role="status">{feedback}</p> : null}
  </article>;
}

export function EvidenceList({ results, query, onOpen }: {
  results: SearchResult[]; query: string; onOpen: (result: SearchResult, editing?: boolean) => void;
}) {
  return <><div className="evidence-list-heading"><h2>检索证据 <span>{results.length}</span></h2><p>按相关度排序 · 分数不是可信度</p></div>
    <ol className="search-results">{results.map((result) => <li key={`${result.document_id}:${result.locator.chunk_id}:${result.rank}`}><EvidenceCard result={result} query={query} onOpen={onOpen} /></li>)}</ol></>;
}
