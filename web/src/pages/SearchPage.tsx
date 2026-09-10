import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Button } from "../components/Button/Button";
import { createNote } from "../features/notes/api";
import { useSearchDocumentsQuery, useSearchMutation } from "../features/search/queries";
import type { SearchInput, SearchResult } from "../features/search/types";

const errorMessage = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback;
export function sourceLocation(result: SearchResult) {
  return [result.page_number != null ? `第 ${result.page_number} 页` : "页码未提供", result.section, `片段 ${result.locator.chunk_index + 1}`].filter(Boolean).join(" · ");
}

export function highlightedExcerpt(text: string, query: string): ReactNode {
  if (!query.trim()) return text;
  const literal = query.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const matches = [...text.matchAll(new RegExp(literal, "giu"))];
  const parts: ReactNode[] = [];
  let offset = 0;
  for (const match of matches) {
    parts.push(text.slice(offset, match.index));
    parts.push(<mark key={match.index}>{match[0]}</mark>);
    offset = match.index + match[0].length;
  }
  parts.push(text.slice(offset));
  return parts;
}

function ResultDetail({ result, onClose }: { result: SearchResult; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(true);
  const auth = useAuth();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [feedback, setFeedback] = useState("");
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(result.excerpt);
  const dirty = editing && body !== result.excerpt;
  const attempt = useRef<{ body: string; id: string } | null>(null);
  const save = useMutation({ gcTime: 0, retry: false, mutationFn: (input: Parameters<typeof createNote>[1]) => createNote(auth.request, input) });
  useEffect(() => {
    alive.current = true;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const element = dialog.current;
    element?.showModal();
    return () => { alive.current = false; element?.close(); document.body.style.overflow = previousOverflow; trigger?.focus(); };
  }, []);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(`${result.document_name}\n${sourceLocation(result)}\n${result.excerpt}\n文档 ID：${result.document_id}\n片段 ID：${result.locator.chunk_id}`);
      if (alive.current) setFeedback("引用已复制");
    } catch { if (alive.current) setFeedback("无法访问剪贴板，请手动选择并复制上方内容。"); }
  }
  async function saveNote() {
    if (!body.trim() || save.isPending) return;
    if (!attempt.current || attempt.current.body !== body) attempt.current = { body, id: crypto.randomUUID() };
    try {
      const note = await save.mutateAsync({ body_markdown: body, client_request_id: attempt.current.id, source: { kind: "document_chunk", locator: result.locator } });
      if (!alive.current) return;
      await client.invalidateQueries({ queryKey: ["notes"] });
      if (alive.current) navigate(`/notes?note=${encodeURIComponent(note.id)}`);
    } catch { /* Render the safe error; retain the draft and request ID for retry. */ }
  }
  function close() {
    if (save.isPending) return;
    if (dirty && !window.confirm("笔记尚未保存，确定关闭吗？")) return;
    onClose();
  }
  function openQa() {
    if (dirty && !window.confirm("笔记尚未保存，确定进入问答吗？")) return;
    navigate(`/qa?documents=${encodeURIComponent(result.document_id)}`);
  }
  const staleSource = save.error instanceof ApiError && ["NOTE_SOURCE_NOT_FOUND", "NOTE_SOURCE_DELETING"].includes(save.error.code);
  return <dialog ref={dialog} className="search-detail" aria-labelledby="search-detail-title" onCancel={(event) => { event.preventDefault(); close(); }}>
    <header><h2 id="search-detail-title">来源详情</h2><Button hierarchy="ghost" onClick={close} disabled={save.isPending} aria-label="关闭来源详情">关闭</Button></header>
    <h3>{result.document_name}</h3><p className="search-location">{sourceLocation(result)}</p>
    <p className="search-full-excerpt">{result.excerpt}</p>
    <p className="search-muted">显示检索摘录，不代表全文；相关度仅用于排序，不代表答案可信度。</p>
    <div className="search-actions"><Button hierarchy="secondary" onClick={() => void copy()}>复制引用</Button><Button hierarchy="secondary" disabled={save.isPending} onClick={openQa}>基于此文档问答</Button><Button disabled={editing} onClick={() => setEditing(true)}>保存为笔记</Button></div>
    {feedback ? <p role="status">{feedback}</p> : null}
    {editing ? <form className="search-note" onSubmit={(event) => { event.preventDefault(); void saveNote(); }}>
      <label htmlFor="search-note-body">笔记正文（保存前可编辑）</label>
      <textarea id="search-note-body" value={body} maxLength={20000} rows={7} disabled={save.isPending} onChange={(event) => { setBody(event.target.value); save.reset(); }} />
      <p>保存时重新核验文档来源，摘录不会被当作来源凭据。</p>
      {save.error ? <p role="alert">{staleSource ? "来源已变化或正在删除。请保留正文，关闭详情后重新检索。" : errorMessage(save.error, "笔记保存失败，请重试。")}</p> : null}
      <Button type="submit" loading={save.isPending} disabled={!body.trim() || staleSource}>保存并打开笔记</Button>
    </form> : null}
  </dialog>;
}

export function SearchPage() {
  const auth = useAuth();
  // A fresh session remounts every private draft and detail, including same-user login.
  return auth.status === "authenticated" ? <SearchWorkspace key={`${auth.username}:${auth.csrfToken}`} /> : null;
}

function SearchWorkspace() {
  const documents = useSearchDocumentsQuery();
  const [chosen, setChosen] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [limit, setLimit] = useState<SearchInput["limit"]>(10);
  const [detail, setDetail] = useState<SearchResult | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const items = documents.data?.items ?? [];
  const missing = chosen.some((id) => !items.some((item) => item.document_id === id));
  const scopeStamp = items.map((item) => `${item.document_id}:${item.loaded_at}`).sort().join(",");
  const fingerprint = JSON.stringify([query, chosen.slice().sort(), limit, scopeStamp, Boolean(documents.error)]);
  const search = useSearchMutation(fingerprint);
  const valid = chosen.length >= 1 && chosen.length <= 10 && query.trim().length >= 1 && query.trim().length <= 1000 && !missing && !documents.error && !documents.isPending;
  // Only a current response can open a detail or source action.
  const activeDetail = search.data?.results.find((item) => item === detail);
  const hasResults = Boolean(search.data?.results.length);
  function toggle(id: string) { setChosen((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); }
  return <article className="search-page">
    <header className="search-heading"><div><p className="search-eyebrow">从资料到理解</p><h1>文献检索</h1><p>检索已导入文档中的证据，连接问答与学习笔记。</p></div><nav className="search-header-links" aria-label="检索相关入口"><Link to="/documents">管理文档库</Link><a href="/legacy/" aria-label="前往旧版">旧版入口</a></nav></header>
    <div className="search-layout">
      <aside className="search-scope" aria-labelledby="search-scope-heading">
        <h2 id="search-scope-heading">检索范围 <span>{chosen.length}/10</span></h2><p>请明确选择 1–10 份文档。当前不搜索互联网。</p>
        <label htmlFor="search-document-filter">筛选文档</label><input id="search-document-filter" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="按文件名称查找" />
        {documents.isPending ? <p role="status">正在加载文档…</p> : documents.error ? <div role="alert"><p>文档库暂不可用，无法确认检索范围。</p><Button hierarchy="secondary" onClick={() => void documents.refetch()}>重新加载文档</Button></div> : !items.length ? <p>还没有可检索的文档，请先到文档库导入。</p> : <fieldset><legend className="sr-only">选择检索文档</legend><div className="search-document-list">{items.filter((item) => item.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase())).map((item) => <label key={item.document_id} className="search-document"><input type="checkbox" checked={chosen.includes(item.document_id)} disabled={!chosen.includes(item.document_id) && chosen.length >= 10} onChange={() => toggle(item.document_id)} /><span>{item.name}</span></label>)}</div></fieldset>}
        {chosen.length ? <Button hierarchy="ghost" onClick={() => setChosen([])}>清除选择</Button> : null}
        {missing ? <p role="alert">所选文档已不在文档库中，请重新选择。</p> : null}
      </aside>
      <section className="search-content" aria-label="文档检索">
        <form className="search-form" onSubmit={(event) => { event.preventDefault(); if (valid && !search.isPending) { setDetail(null); setSubmitted(true); search.run({ query, document_ids: chosen, limit }); } }}>
          <label htmlFor="search-query">想在资料中找到什么？</label><textarea id="search-query" value={query} rows={3} maxLength={1000} onChange={(event) => setQuery(event.target.value)} placeholder="输入关键词或描述你要寻找的内容" />
          <div className="search-form-footer"><label htmlFor="search-limit">结果数量 <select id="search-limit" value={limit} onChange={(event) => setLimit(Number(event.target.value) as SearchInput["limit"])}><option value={5}>5 条</option><option value={10}>10 条</option><option value={20}>20 条</option></select></label><span>{query.trim().length}/1000</span><Button type="submit" disabled={!valid} loading={search.isPending}>检索文档</Button></div>
        </form>
        <div className="search-status" role="status" aria-live="polite">{search.isPending ? "正在检索所选文档…" : search.error ? "检索未完成，请按提示重试。" : search.data ? `返回 ${search.data.result_count} 条相关片段` : submitted ? "检索条件已变化，请重新检索。" : "选择文档并输入内容，开始检索。"}</div>
        {search.error ? <div className="search-error" role="alert"><p>{errorMessage(search.error, "检索暂不可用，请稍后重试。")}</p>{search.error instanceof ApiError && search.error.retryable ? <p>可稍后点击“检索文档”重试，不会自动重复请求。</p> : <p>请检查检索范围后重新提交。</p>}</div> : null}
        {search.data && !hasResults ? <div className="search-empty"><h2>未找到相关片段</h2><p>试试更具体的关键词，或调整所选文档范围。</p></div> : null}
        {hasResults ? <><p className="search-muted">按相关度排序 · 分数不是可信度</p><ol className="search-results">{search.data!.results.map((result) => <li key={`${result.document_id}:${result.locator.chunk_id}:${result.rank}`}><article className="search-result"><div className="search-result-top"><h2>{result.document_name}</h2><span>#{result.rank}</span></div><p className="search-location">{sourceLocation(result)}</p><p className="search-excerpt">{highlightedExcerpt(result.excerpt, query)}</p><div className="search-result-bottom"><small>{result.score == null ? "相关度未提供" : `相关度 ${result.score.toFixed(3)}`}</small><Button hierarchy="secondary" onClick={() => setDetail(result)} aria-label={`查看来源 ${result.rank}：${result.document_name}`}>查看来源</Button></div></article></li>)}</ol></> : null}
      </section>
    </div>
    {activeDetail ? <ResultDetail key={`${search.data!.request_id}:${activeDetail.rank}`} result={activeDetail} onClose={() => setDetail(null)} /> : null}
  </article>;
}
