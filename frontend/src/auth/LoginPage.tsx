import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ErrorState } from "@/components/ui";
import { LocalePicker, useT } from "@/i18n";
import { homeFor, useAuth } from "./AuthContext";

export function LoginPage() {
  const { me, login } = useAuth();
  const t = useT();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from;
  const [key, setKey] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (me) return <Navigate to={from ?? homeFor(me)} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const who = await login(key);
      navigate(from ?? homeFor(who), { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <div className="card-body stack">
          <div className="row"><img src="/favicon.svg" alt="" width={28} height={28} /><h1>{t("PayGuard Console")}</h1></div>
          <LocalePicker />
          <p className="muted" style={{ margin: 0 }}>
            {t("Sign in with your API key. Analysts see the case queue, admins see the full dashboard, merchants see the scoring console.")}
          </p>
          <label className="field">
            <span>{t("API key")}</span>
            <input className="input" type="password" autoComplete="off" spellCheck={false} placeholder="pg_..."
                   value={key} onChange={(e) => setKey(e.target.value)} autoFocus required />
          </label>
          {error != null && <ErrorState error={error} />}
          <button className="btn primary" disabled={busy || key.trim().length < 8}>{busy ? t("Signing in...") : t("Sign in")}</button>
          <p className="small muted" style={{ margin: 0 }}>
            {t("Create keys with {command} or in Admin › API clients.", { command: "payguard create-client --name ops --role admin" })}
          </p>
        </div>
      </form>
    </div>
  );
}
