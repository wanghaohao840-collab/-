import { Link } from "react-router-dom";

export function ResearchPageHeader() {
  return <header className="search-heading">
    <div><p className="search-eyebrow">从资料到理解</p><h1>文献检索</h1><p>从自己的资料中寻找可信、可追溯的证据。</p></div>
    <nav className="search-header-links" aria-label="检索相关入口">
      <Link to="/documents">管理文档库 <span aria-hidden="true">↗</span></Link>
      <details className="research-more"><summary>更多操作</summary><div><a href="/legacy/" aria-label="前往旧版">旧版入口</a></div></details>
    </nav>
  </header>;
}
