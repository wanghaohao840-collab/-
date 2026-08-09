export function AuthIntro() {
  return (
    <aside className="auth-intro" aria-label="知研介绍">
      <div className="auth-intro__brand">
        <span className="auth-intro__mark" aria-hidden="true">
          知
        </span>
        <span className="auth-intro__brand-copy">
          <strong>知研</strong>
          <small>智能文档学习助手</small>
        </span>
      </div>
      <p className="auth-intro__status">已同步</p>
      <div className="auth-intro__hero">
        <h2>
          让文档知识
          <br />
          真正沉淀为理解
        </h2>
        <p>基于来源问答、学习笔记与长期记忆，构建可追溯的个人知识体系。</p>
        <section className="auth-intro__proof" aria-label="学习数据承诺">
          <h3>来源清晰，学习连续</h3>
          <ul>
            <li>PDF 页码与引用一键追溯</li>
            <li>多文档问答保持 document_id 隔离</li>
            <li>笔记、记忆与洞察形成学习闭环</li>
          </ul>
        </section>
      </div>
      <p className="auth-intro__mobile-tagline">有来源地提问，有节奏地学习。</p>
    </aside>
  );
}
