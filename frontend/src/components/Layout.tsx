import { Link, NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../context/AuthContext";

export function Layout() {
  const { user, logout } = useAuth();

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          FCIP
        </Link>
        <nav>
          <NavLink to="/" end>
            Overview
          </NavLink>
          <NavLink to="/alerts">Alert queue</NavLink>
        </nav>
        <div className="topbar-user">
          {user && (
            <span className="muted">
              {user.email} · {user.role.replace("ROLE_", "")}
            </span>
          )}
          <button type="button" onClick={() => void logout()}>
            Logout
          </button>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
