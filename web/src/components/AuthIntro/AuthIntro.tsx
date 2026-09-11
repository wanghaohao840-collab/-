import knowledgePages from "../../assets/knowledge-pages.png";
import { BrandMark } from "../BrandMark/BrandMark";

export function AuthIntro() {
  return <aside className="auth-intro" aria-label="知研介绍">
    <div className="auth-intro__brand"><BrandMark /><span className="auth-intro__brand-copy"><strong>知研</strong><small>让知识在时间中沉淀</small></span></div>
    <div className="auth-intro__hero">
      <p className="auth-intro__eyebrow">阅读 · 思考 · 连接 · 长期积累</p>
      <h2>让每一次阅读<br />成为长期的知识资产</h2>
      <p>与文档对话，留下有来源的笔记，<br />让学习持续积累。</p>
      <img className="auth-intro__art" src={knowledgePages} alt="" width="1536" height="1024" />
      <div className="auth-intro__caption"><span>知识，是时间的复利。</span><em lang="en">Knowledge grows with time.</em></div>
    </div>
    <p className="auth-intro__mobile-tagline">每一次阅读，都成为更好的自己。</p>
  </aside>;
}
