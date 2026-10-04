import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { NAV } from "@/app/navigation";
import { ops } from "@/api/endpoints";
import { useAuth } from "@/auth/AuthContext";
import { Badge } from "@/components/ui";
import { LocalePicker, useT } from "@/i18n";

export function AppShell() {
  const { me, logout, can } = useAuth();
  const t = useT();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);
  const ready = useQuery({ queryKey: ["readyz"], queryFn: ops.ready, refetchInterval: 30_000 });

  return (
    <div className="shell">
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label={t("Main navigation")}>
        <div className="brand"><img src="/favicon.svg" alt="" />PayGuard</div>
        {NAV.map((section) => {
          const items = section.items.filter((i) => can(i.permission));
          if (!items.length) return null;
          return (
            <nav key={section.title}>
              <div className="nav-section">{t(section.title)}</div>
              {items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.end} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
                  <span aria-hidden style={{ width: 16, textAlign: "center" }}>{i.icon}</span>
                  {t(i.label)}
                </NavLink>
              ))}
            </nav>
          );
        })}
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="row">
            <button className="btn small menu-btn" onClick={() => setOpen((o) => !o)} aria-label={t("Toggle navigation")}>☰</button>
            {ready.data && (
              <span className="row small muted" title={JSON.stringify(ready.data.checks)}>
                <span className={`dot ${ready.data.ready ? "up" : "down"}`} />
                {ready.data.ready ? t("All systems ready") : t("Degraded")}
                {ready.data.model_version && <span className="mono">· {ready.data.model_version}</span>}
              </span>
            )}
          </div>
          <div className="who">
            <LocalePicker compact />
            <span>{me?.name}</span>
            <Badge value={me?.role ?? ""} tone="info" />
            <button className="btn small" onClick={logout}>{t("Sign out")}</button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
