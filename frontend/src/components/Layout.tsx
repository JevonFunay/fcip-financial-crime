import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../context/AuthContext";
import { Icon, type IconName } from "./Icon";
import { SyntheticNotice } from "./Notices";

const navigation: {
  path: string;
  label: string;
  icon: IconName;
}[] = [
  {
    path: "/",
    label: "Overview",
    icon: "grid",
  },
  {
    path: "/alerts",
    label: "Alert queue",
    icon: "alert",
  },
  {
    path: "/model-flags",
    label: "Model flags",
    icon: "activity",
  },
  {
    path: "/lab",
    label: "Lab model",
    icon: "scan",
  },
  {
    path: "/audit",
    label: "Audit trail",
    icon: "history",
  },
];
const roleNames: Record<string, string> = {
  ROLE_DATA_OPS: "Data Operations",
  ROLE_ANALYST: "Analyst",
  ROLE_TRIAGE: "Triage Analyst",
  ROLE_INVESTIGATOR: "Investigator",
  ROLE_SENIOR_INVESTIGATOR: "Senior Investigator",
  ROLE_MLRO: "MLRO",
  ROLE_AUDITOR: "Internal Auditor",
  ROLE_MANAGEMENT: "Management",
  ROLE_ADMIN: "Administrator",
};

export function Layout() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);
  useEffect(() => {
    function onResize() {
      if (window.innerWidth > 900) setMenuOpen(false);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    sidebar.current?.querySelector<HTMLAnchorElement>("nav a")?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButton.current?.focus();
      }
      if (event.key === "Tab") {
        const items =
          sidebar.current?.querySelectorAll<HTMLElement>("a, button");
        if (!items?.length) return;
        if (event.shiftKey && document.activeElement === items[0]) {
          event.preventDefault();
          items[items.length - 1].focus();
        } else if (
          !event.shiftKey &&
          document.activeElement === items[items.length - 1]
        ) {
          event.preventDefault();
          items[0].focus();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="app app-shell">
      <a className="skip-link" href="#main-content">
        Lewati ke konten
      </a>
      {menuOpen && (
        <button
          className="sidebar-backdrop"
          type="button"
          aria-label="Tutup navigasi"
          onClick={() => {
            setMenuOpen(false);
            menuButton.current?.focus();
          }}
        />
      )}
      <aside
        id="main-navigation"
        ref={sidebar}
        className={`sidebar ${menuOpen ? "is-open" : ""}`}
      >
        <Link to="/" className="brand" aria-label="FCIP Overview">
          <span>
            <strong>FCIP</strong>
            <small>Financial Crime Intelligence</small>
          </span>
        </Link>
        <nav aria-label="Navigasi utama">
          {navigation.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              onClick={() => {
                if (menuOpen) {
                  setMenuOpen(false);
                  document.getElementById("main-content")?.focus();
                }
              }}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <button
          className="mobile-menu-close button-secondary"
          type="button"
          onClick={() => {
            setMenuOpen(false);
            menuButton.current?.focus();
          }}
        >
          <Icon name="close" size={16} />
          Tutup navigasi
        </button>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div className="topbar-context">
            <button
              type="button"
              ref={menuButton}
              className="icon-button mobile-menu-button"
              aria-label="Buka navigasi"
              aria-expanded={menuOpen}
              aria-controls="main-navigation"
              onClick={() => setMenuOpen(!menuOpen)}
            >
              <Icon name="menu" />
            </button>
            <span className="workspace-name">Nusantara Digital Payment</span>
          </div>
          <div className="topbar-user">
            {user && (
              <>
                <div className="user-details" title={user.full_name}>
                  <strong>
                    {roleNames[user.role] ?? user.role.replace("ROLE_", "")}
                  </strong>
                  <span>{user.email}</span>
                </div>
              </>
            )}
            <button
              className="icon-button logout-button"
              type="button"
              onClick={() => void logout()}
              aria-label="Logout"
              title="Logout"
            >
              <Icon name="logout" size={19} />
            </button>
          </div>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
        <footer className="workspace-footer">
          <SyntheticNotice />
        </footer>
      </div>
    </div>
  );
}
