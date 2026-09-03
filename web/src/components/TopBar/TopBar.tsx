import type { MouseEvent } from "react";

type TopBarProps = {
  moreOpen: boolean;
  onOpenMore: (event: MouseEvent<HTMLButtonElement>) => void;
  username: string;
};

export function TopBar({ moreOpen, onOpenMore, username }: TopBarProps) {
  const avatarLabel = username.trim().slice(0, 1) || "研";

  return (
    <header className="topbar">
      <span className="topbar__desktop-title">学习工作台</span>
      <strong className="topbar__mobile-title">知研</strong>
      <div className="topbar__actions">
        <button
          type="button"
          className="topbar__more"
          aria-label="更多操作"
          aria-expanded={moreOpen}
          aria-controls="more-drawer"
          onClick={onOpenMore}
        >
          +
        </button>
        <span className="topbar__avatar" aria-label={`当前账户：${username}`}>
          {avatarLabel}
        </span>
      </div>
    </header>
  );
}
