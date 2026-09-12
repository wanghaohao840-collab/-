import type { SearchInput } from "../../features/search/types";
import { Button } from "../Button/Button";

export function ResearchCommandBar({ query, onQuery, limit, onLimit, valid, loading, onSubmit }: {
  query: string; onQuery: (value: string) => void; limit: SearchInput["limit"]; onLimit: (value: SearchInput["limit"]) => void;
  valid: boolean; loading: boolean; onSubmit: () => void;
}) {
  return <form className="search-form" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
    <p className="research-kicker">EVIDENCE WORKSPACE <span aria-hidden="true">/</span> 证据工作台</p>
    <label htmlFor="search-query">你想从这些资料中找到什么？</label>
    <textarea id="search-query" value={query} rows={3} maxLength={1000} onChange={(event) => onQuery(event.target.value)} placeholder="描述你正在寻找的观点、概念、证据或问题……" />
    <div className="search-form-footer">
      <span className="retrieval-mode"><span aria-hidden="true">◎</span> 文档检索</span>
      <label htmlFor="search-limit">结果 <select id="search-limit" value={limit} onChange={(event) => onLimit(Number(event.target.value) as SearchInput["limit"])}><option value={5}>5 条</option><option value={10}>10 条</option><option value={20}>20 条</option></select></label>
      <span className="query-count">{query.trim().length}/1000</span>
      <Button type="submit" disabled={!valid} loading={loading}>检索证据 <span aria-hidden="true">→</span></Button>
    </div>
  </form>;
}
