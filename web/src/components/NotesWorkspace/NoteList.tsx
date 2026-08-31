import { Button } from "../Button/Button";
import type { NoteListItem } from "../../features/notes/types";

type Props = {
  items: NoteListItem[];
  selectedId?: string;
  hasMore: boolean;
  loadingMore: boolean;
  onSelect: (id: string) => void;
  onLoadMore: () => void;
  onCreate: () => void;
};

export function NoteList({ items, selectedId, hasMore, loadingMore, onSelect, onLoadMore, onCreate }: Props) {
  return <aside className="notes-list" aria-label="笔记列表">
    <div className="notes-list__heading"><h2>全部笔记</h2><Button size="sm" onClick={onCreate}>新建笔记</Button></div>
    {!items.length ? <div className="notes-list__empty"><h3>还没有笔记</h3><p>没有符合条件的笔记</p></div> : <ol>
      {items.map((item) => <li key={item.id}>
        <button type="button" className="notes-list__item" aria-current={item.id === selectedId ? "page" : undefined} onClick={() => onSelect(item.id)}>
          <strong>{item.concept || "未命名笔记"}</strong>
          <span>{item.body_markdown.replaceAll("#", "").replaceAll("*", "").replaceAll("`", "").replaceAll(">", "").replaceAll("-", "").replaceAll("[", "").replaceAll("]", "").trim().slice(0, 100) || "空白笔记"}</span>
          <small>{item.tags.length ? item.tags.join(" · ") : "无标签"}</small>
          {item.projection_state === "failed" ? <em>投影失败</em> : null}
        </button>
      </li>)}
    </ol>}
    {hasMore ? <Button hierarchy="secondary" className="notes-list__more" loading={loadingMore} onClick={onLoadMore}>加载更多笔记</Button> : null}
  </aside>;
}
