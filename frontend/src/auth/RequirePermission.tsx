import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Spinner } from "@/components/ui";
import { useAuth } from "./AuthContext";

/** Route guard. The API enforces roles on every request; this only keeps the UI from offering pages
 * the caller can't use. */
export function RequirePermission({ permission, children }: { permission?: string; children: ReactNode }) {
  const { me, loading, can } = useAuth();
  const location = useLocation();
  if (loading) return <div className="center"><Spinner /></div>;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  if (permission && !can(permission)) {
    return (
      <div className="card"><div className="empty">
        <h2>Not available for your role</h2>
        <p>This page needs the <code>{permission}</code> permission. Your key has role <b>{me.role}</b>.</p>
      </div></div>
    );
  }
  return <>{children}</>;
}
