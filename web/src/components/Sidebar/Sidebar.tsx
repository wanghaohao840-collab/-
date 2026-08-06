import { NavLink } from "react-router-dom";

import { Button } from "../Button/Button";
import { navigationItems } from "../../layout/navigation";

type SidebarProps = {
  isLogoutPending: boolean;
  onLogout: () => void;
};

export function Sidebar({ isLogoutPending, onLogout }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="应用侧栏">
      <div className="sidebar__brand">
        <span className="brand-mark" aria-hidden="true">知</span>
        <span className="sidebar__brand-copy">
          <strong>知研</strong>
          <small>智能文档学习助手</small>
        </span>
      </div>
      <nav className="sidebar__nav" aria-label="主导航">
        {navigationItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end
            aria-label={item.label}
            className={({ isActive }) =>
              `sidebar__link${isActive ? " sidebar__link--active" : ""}`
            }
          >
            <span className="nav-icon" aria-hidden="true" />
            <span className="sidebar__label">{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <Button
        className="sidebar__logout"
        hierarchy="ghost"
        loading={isLogoutPending}
        aria-label="退出登录"
        onClick={onLogout}
      >
        <span className="nav-icon" aria-hidden="true" />
        <span className="sidebar__label">退出登录</span>
      </Button>
    </aside>
  );
}
