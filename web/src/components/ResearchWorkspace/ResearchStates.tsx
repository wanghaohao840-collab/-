import type { SearchResponse } from "../../features/search/types";

const suggestions = ["总结这些文档的核心观点", "这些资料对 RAG 的定义有什么差异？", "找到有关向量数据库的论述", "哪些内容提到了 GraphRAG？"];

export function EmptyResearchState({ onSuggestion }: { onSuggestion: (value: string) => void }) {
  return <div className="research-idle">
    <svg viewBox="0 0 240 120" className="research-fragments" fill="none" aria-hidden="true">
      <g stroke="currentColor" strokeWidth="1.5"><path d="M28 26h52v70H28zM100 12h52v70h-52zM172 34h40v58h-40zM38 40h30M38 49h23M38 58h30M110 27h30M110 36h23M110 45h30M182 48h20M182 57h20" /><path d="m80 66 20-18m52 12 20 9M54 96v15h139V92" strokeDasharray="3 4" /><circle cx="122" cy="111" r="5" /></g>
    </svg>
    <h2>找到的不只是答案，<br />而是可以回到原文的证据。</h2>
    <p>选择资料并描述你正在寻找的内容，<br />系统会定位相关片段、页码与来源。</p>
    <div className="research-suggestions" aria-label="查询建议">{suggestions.map((suggestion) => <button key={suggestion} onClick={() => { onSuggestion(suggestion); document.getElementById("search-query")?.focus(); }}>{suggestion}<span aria-hidden="true"> ↗</span></button>)}</div>
  </div>;
}

export function RetrievalProgress({ loading, error, data, submitted, selectedCount }: {
  loading: boolean; error: boolean; data?: SearchResponse; submitted: boolean; selectedCount: number;
}) {
  return <div className={`search-status${loading ? " is-loading" : ""}`} role="status" aria-live="polite">
    {loading ? <><span className="retrieval-dot" aria-hidden="true" /><span>正在检索所选文档…</span><span className="retrieval-scope">{selectedCount} 份资料</span></> : error ? "检索未完成，请按提示重试。" : data ? <><span>检索 {data.document_ids.length} 份资料</span><span aria-hidden="true">→</span><span>返回 {data.result_count} 条相关片段</span></> : submitted ? "检索条件已变化，请重新检索。" : <span className="sr-only">选择文档并输入内容，开始检索。</span>}
  </div>;
}

export function KnowledgeContext() {
  return <aside className="research-context" aria-label="关联知识"><span className="research-kicker">KNOWLEDGE CONTEXT</span><div><h2>暂无关联知识</h2><p>将证据整理为笔记，让来源与理解一起沉淀。</p></div></aside>;
}
