import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { Button } from "../components/Button/Button";
import { useOverview } from "../features/insights/queries";

const time = (value: string | null) => value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(new Date(value)) : "时间未知";
export function OverviewPage() {
  const query = useOverview();
  if (query.isPending) return <section className="insight-state" role="status">正在加载学习概览…</section>;
  if (query.error || !query.data) return <section className="insight-state" role="alert"><h1>学习概览</h1><p>{query.error instanceof ApiError ? query.error.message : "概览加载失败"}</p><Button hierarchy="secondary" onClick={() => void query.refetch()}>重新加载</Button></section>;
  const { stats, recent_documents: documents, recent_questions: questions } = query.data;
  return <article className="overview-page">
    <header className="insight-heading"><div><h1>学习概览</h1><p>基于当前保留的真实学习记录</p></div><Link className="button button--primary button--md" to="/documents">导入文档</Link></header>
    <section className="metric-grid" aria-label="学习指标">
      <article><strong>{stats.document_count}</strong><span>文档</span></article><article><strong>{stats.completed_question_count}</strong><span>已完成问答</span></article><article><strong>{stats.note_count}</strong><span>学习笔记</span></article><article><strong>{stats.active_days}</strong><span>近 30 天活跃日</span></article>
    </section>
    <div className="overview-columns">
      <section className="insight-panel"><header><h2>最近文档</h2><Link to="/documents">查看全部</Link></header>{documents.length ? <ul className="insight-list">{documents.map((item) => <li key={item.document_id}><Link to={`/qa?documents=${encodeURIComponent(item.document_id)}`}>{item.name}</Link><span>{time(item.loaded_at)}</span></li>)}</ul> : <div className="insight-empty"><p>还没有可学习的文档。</p><Link to="/documents">导入第一篇文档</Link></div>}</section>
      <section className="insight-panel"><header><h2>继续学习</h2><Link to="/qa">进入问答</Link></header>{questions.length ? <ul className="insight-list">{questions.map((item, index) => <li key={`${item.asked_at}-${index}`}><span>{item.question}</span><small>{item.document_names.join("、") || "未关联文档"}</small></li>)}</ul> : <div className="insight-empty"><p>完成一次基于文档的问答后，会在这里继续。</p><Link to="/qa">开始问答</Link></div>}</section>
    </div>
    <Link className="insight-summary-link" to="/insights">查看完整学习洞察与报告 →</Link>
  </article>;
}
