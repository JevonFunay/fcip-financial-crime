import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../context/AuthContext";
import { Icon, type IconName } from "./Icon";
import { SyntheticNotice } from "./Notices";

const navigation: {
  path: string;
  label: string;
  icon: IconName;
  caption: string;
}[] = [
  {
    path: "/",
    label: "Overview",
    icon: "grid",
    caption: "Ringkasan & operasi data",
  },
  {
    path: "/alerts",
    label: "Alert queue",
    icon: "alert",
    caption: "Tinjauan & investigasi",
  },
  {
    path: "/model-flags",
    label: "Model flags",
    icon: "activity",
    caption: "Observasi AML & Fraud",
  },
  {
    path: "/audit",
    label: "Audit trail",
    icon: "history",
    caption: "Jejak aktivitas",
  },
];
const roleNames: Record<string, string> = {
  ROLE_DATA_OPS: "Data Operations",
  ROLE_ANALYST: "Analyst",
  ROLE_TRIAGE: "Triage Analyst",
  ROLE_INVESTIGATOR: "Investigator",
};

export function Layout() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const current = navigation.find((item) =>
    item.path === "/" ? pathname === "/" : pathname.startsWith(item.path),
  );
  const title =
    current?.label ??
    (pathname.startsWith("/cases") ? "Case detail" : "Alert detail");

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
          <span className="brand-mark">
            <Icon name="shield" size={25} />
          </span>
          <span>
            <strong>
              FCIP<span className="brand-dot">.</span>
            </strong>
            <small>Financial Crime Intelligence</small>
          </span>
        </Link>
        <div className="workspace-label">
          <span className="workspace-avatar">ND</span>
          <div>
            <strong>Nusantara Digital Payment</strong>
            <small>Capstone workspace</small>
          </div>
        </div>
        <p className="nav-group-label">MONITORING</p>
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
              <Icon name="chevron" size={14} className="nav-chevron" />
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="sidebar-foot-icon">
            <Icon name="shield" />
          </span>
          <strong>Keputusan oleh manusia</strong>
          <p>
            Sinyal mendukung investigasi. Tinjauan dan keputusan tetap pada
            analis.
          </p>
          <span className="sandbox-tag">
            <span />
            Synthetic sandbox
          </span>
        </div>
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
            <span className="breadcrumb">
              Workspace <Icon name="chevron" size={13} />
              <strong>{title}</strong>
            </span>
          </div>
          <div className="topbar-user">
            {user && (
              <>
                <span className="user-avatar">
                  {user.full_name
                    .split(" ")
                    .filter(Boolean)
                    .slice(0, 2)
                    .map((name) => name[0])
                    .join("")}
                </span>
                <div className="user-details">
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
          <SyntheticNotice />
          <Outlet />
        </main>
        <footer className="workspace-footer">
          <span>FCIP · Nusantara Digital Payment</span>
          <span>Advisory only · Tinjauan oleh manusia</span>
        </footer>
      </div>
    </div>
  );
}
