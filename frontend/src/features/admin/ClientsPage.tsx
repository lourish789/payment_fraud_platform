import { useState, type FormEvent } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import type { ApiClientCreated, Role } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Async, Badge, Card, ErrorState, PageHeader, Pagination, Select } from "@/components/ui";
import { LOCALES, useI18n, useT } from "@/i18n";
import { dateTime } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";
import { codeOptions } from "@/features/options";

const LIMIT = 25;

export function ClientsPage() {
  const t = useT();
  const qc = useQueryClient();
  const { me } = useAuth();
  const { values, set, offset, setOffset } = useUrlState({ role: "all", active: "all" });
  const q = useQuery({
    queryKey: ["admin", "clients", values, offset],
    queryFn: () => admin.clients({ role: opt(values.role), active: values.active === "all" ? undefined : values.active === "true", limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  const revoke = useMutation({
    mutationFn: admin.revokeClient,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "clients"] }),
  });
  const localeName = (code: string | null) => LOCALES.find((l) => l.code === code)?.name ?? "-";

  return (
    <>
      <PageHeader title={t("API clients")} description={t("Keys for merchants (scoring), analysts (cases) and admins. Only a SHA-256 of each key is stored; a key is shown once, at creation.")} />
      <div className="grid grid-3">
        <Card flush className="span-2" title={t("Clients")} actions={<div className="row">
          <Select label={t("Role")} value={values.role} onChange={(v) => set("role", v)} options={codeOptions(t, ["merchant", "analyst", "admin"])} />
          <Select label={t("Status")} value={values.active} onChange={(v) => set("active", v)} options={[{ value: "all", label: t("All") }, { value: "true", label: t("Active") }, { value: "false", label: t("Revoked") }]} />
        </div>}>
          {revoke.error && <div style={{ padding: 12 }}><ErrorState error={revoke.error} /></div>}
          <Async query={q}>
            {(page) => (
              <>
                <div className="table-wrap"><table className="table">
                  <thead><tr><th>{t("Name")}</th><th>{t("Role")}</th><th>{t("Key")}</th><th>{t("Language / currency")}</th><th>{t("Status")}</th><th>{t("Created")}</th><th /></tr></thead>
                  <tbody>{page.items.map((c) => (
                    <tr key={c.client_id}>
                      <td>{c.name}<div className="mono small muted">{c.client_id}</div></td>
                      <td><Badge value={c.role} tone="info" /></td>
                      <td className="mono small">{c.key_prefix ? `${c.key_prefix}…` : "-"}</td>
                      <td className="small">{localeName(c.preferences?.locale ?? null)} · {c.preferences?.currency ?? "-"}</td>
                      <td>{c.active ? <Badge value="active" tone="approve" /> : <Badge value="revoked" tone="decline" title={dateTime(c.revoked_at)} />}</td>
                      <td className="small nowrap">{dateTime(c.created_at)}</td>
                      <td className="num">
                        {c.active && c.client_id !== me?.client_id && (
                          <button className="btn small danger" disabled={revoke.isPending}
                            onClick={() => window.confirm(t("Revoke {name}? Requests with this key will fail immediately.", { name: c.name })) && revoke.mutate(c.client_id)}>{t("Revoke")}</button>
                        )}
                        {c.client_id === me?.client_id && <span className="small muted">{t("you")}</span>}
                      </td>
                    </tr>
                  ))}</tbody>
                </table></div>
                <Pagination total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
              </>
            )}
          </Async>
        </Card>
        <CreateClient />
      </div>
    </>
  );
}

function CreateClient() {
  const { t, rates } = useI18n();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("merchant");
  const [locale, setLocale] = useState("");
  const [currency, setCurrency] = useState("");
  const [created, setCreated] = useState<ApiClientCreated | null>(null);
  const m = useMutation({
    mutationFn: () => admin.createClient(name.trim(), role, { locale: locale || null, currency: currency || null }),
    onSuccess: (c) => { setCreated(c); setName(""); qc.invalidateQueries({ queryKey: ["admin", "clients"] }); },
  });
  function submit(e: FormEvent) { e.preventDefault(); setCreated(null); m.mutate(); }
  const notSet = t("Not set (follow the browser)");
  return (
    <Card title={t("Issue a key")}>
      <form className="stack" onSubmit={submit}>
        <label className="field"><span>{t("Name")}</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} minLength={2} maxLength={100} required placeholder={t("e.g. {example}", { example: "checkout-service" })} /></label>
        <Select label={t("Role")} value={role} onChange={(v) => setRole(v as Role)} options={[
          { value: "merchant", label: t("merchant: score payments, verify receipts") },
          { value: "analyst", label: t("analyst: cases, transactions, monitoring") },
          { value: "admin", label: t("admin: everything") },
        ]} />
        <Select label={t("Language")} value={locale} onChange={setLocale}
          options={[{ value: "", label: notSet }, ...LOCALES.map((l) => ({ value: l.code, label: l.name }))]} />
        <Select label={t("Show amounts in")} value={currency} onChange={setCurrency}
          options={[{ value: "", label: notSet }, ...rates.display.map((c) => ({ value: c, label: c }))]} />
        <button className="btn primary" disabled={m.isPending || name.trim().length < 2}>{t("Create key")}</button>
        {m.error && <ErrorState error={m.error} />}
        {created && (
          <div className="alert-box success stack" style={{ gap: 8 }}>
            <b>{t("Key for {name}. Copy it now: it will not be shown again.", { name: created.name })}</b>
            <div className="key-reveal">{created.api_key}</div>
            <button type="button" className="btn small" onClick={() => navigator.clipboard?.writeText(created.api_key)}>{t("Copy")}</button>
          </div>
        )}
      </form>
    </Card>
  );
}
