import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function Layout() {
  const { user, isAuthenticated, logout } = useAuth();

  // 비로그인: 사이드바 없이 풀 너비
  if (!isAuthenticated) {
    return (
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "28px 24px" }}>
        <Outlet />
      </div>
    );
  }

  // 로그인: 사이드바 + 메인
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-logo">TradingBot</div>
        <nav className="sidebar-nav">
          <NavLink to="/" end className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
            대시보드
          </NavLink>
          <NavLink to="/trades" className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
            매매 내역
          </NavLink>
          <NavLink to="/strategies" className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
            전략 관리
          </NavLink>
          <NavLink to="/signals" className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
            매매 신호
          </NavLink>
          <NavLink to="/news" className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
            뉴스
          </NavLink>
          <NavLink to="/profit" className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
            수익률 분석
          </NavLink>
          <NavLink to="/llm" className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
            LLM 관리
          </NavLink>
          <NavLink to="/config" className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
            설정
          </NavLink>
        </nav>
        <div className="sidebar-footer">
          <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>
            {user?.display_name || user?.username}
          </div>
          <button onClick={logout}>로그아웃</button>
        </div>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
