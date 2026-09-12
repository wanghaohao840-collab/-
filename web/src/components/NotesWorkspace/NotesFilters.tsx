export type FilterKey = "query" | "tags" | "source_kind";
export function NotesFilters({ query, tags, source, onChange }: { query: string; tags: string; source: string; onChange?: (key: FilterKey, value: string) => void }) {
  return <div className="notes-library-filters" aria-label="笔记筛选">
    <label>搜索笔记<input aria-label="搜索笔记" placeholder="搜索标题、概念、正文" value={query} onChange={(e) => onChange?.("query", e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") onChange?.("query", e.currentTarget.value.trim()); }} /></label>
    <div className="notes-filter-row"><label>标签<input aria-label="按标签筛选" placeholder="全部标签" value={tags} onChange={(e) => onChange?.("tags", e.target.value)} /></label>
      <label>来源<select aria-label="按来源筛选" value={source} onChange={(e) => onChange?.("source_kind", e.target.value)}><option value="">全部来源</option><option value="qa_message">问答回答</option><option value="qa_citation">问答引用</option><option value="document_chunk">文档片段</option></select></label></div>
  </div>;
}
