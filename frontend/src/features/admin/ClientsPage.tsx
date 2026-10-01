import { useState, type FormEvent } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import type { ApiClientCreated, Role } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Async, Badge, Card, ErrorState, PageHeader, Pagination, Select } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";

const LIMIT = 25;

export function ClientsPage() {
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

  return (
    <>
      <PageHeader title="API clients" description="Keys for merchants (scoring), analysts (cases) and admins. Only a SHA-256 of each key is stored; a key is shown once, at creation." />
      <div className="grid grid-3">
        <Card flush className="span-2" title="Clients" actions={<div className="row">
          <Select label="Role" value={values.role} onChange={(v) => set("role", v)} options={["all", "merchant", "analyst", "admin"].map((v) => ({ value: v, label: v }))} />
          <Select label="Status" value={values.active} onChange={(v) => set("active", v)} options={[{ value: "all", label: "All" }, { value: "true", label: "Active" }, { value: "false", label: "Revoked" }]} />
        </div>}>
          {revoke.error && <div style={{ padding: 12 }}><ErrorState error={revoke.error} /></div>}
          <Async query={q}>
            {(page) => (
              <>
                <div className="table-wrap"><table className="table">
                  <thead><tr><th>Name</th><th>Role</th><th>Key</th><th>Status</th><th>Created</th><th /></tr></thead>
                  <tbody>{page.items.map((c) => (
                    <tr key={c.client_id}>
                      <td>{c.name}<div className="mono small muted">{c.client_id}</div></td>
                      <td><Badge value={c.role} tone="info" /></td>
                      <td className="mono small">{c.key_prefix ? `${c.key_prefix}…` : "-"}</td>
                      <td>{c.active ? <Badge value="active" tone="approve" /> : <Badge value="revoked" tone="decline" title={dateTime(c.revoked_at)} />}</td>
                      <td className="small nowrap">{dateTime(c.created_at)}</td>
                      <td className="num">
                        {c.active && c.client_id !== me?.client_id && (
                          <button className="btn small danger" disabled={revoke.isPending}
                            onClick={() => window.confirm(`Revoke ${c.name}? Requests with this key will fail immediately.`) && revoke.mutate(c.client_id)}>Revoke</button>
                        )}
                        {c.client_id === me?.client_id && <span className="small muted">you</span>}
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
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("merchant");
  const [created, setCreated] = useState<ApiClientCreated | null>(null);
  const m = useMutation({
    mutationFn: () => admin.createClient(name.trim(), role),
    onSuccess: (c) => { setCreated(c); setName(""); qc.invalidateQueries({ queryKey: ["admin", "clients"] }); },
  });
  function submit(e: FormEvent) { e.preventDefault(); setCreated(null); m.mutate(); }
  return (
    <Card title="Issue a key">
      <form className="stack" onSubmit={submit}>
        <label className="field"><span>Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} minLength={2} maxLength={100} required placeholder="e.g. checkout-service" /></label>
        <Select label="Role" value={role} onChange={(v) => setRole(v as Role)} options={[{ value: "merchant", label: "merchant: score payments, verify receipts" }, { value: "analyst", label: "analyst: cases, transactions, monitoring" }, { value: "admin", label: "admin: everything" }]} />
        <button className="btn primary" disabled={m.isPending || name.trim().length < 2}>Create key</button>
        {m.error && <ErrorState error={m.error} />}
        {created && (
          <div className="alert-box success stack" style={{ gap: 8 }}>
            <b>Key for {created.name}. Copy it now: it will not be shown again.</b>
            <div className="key-reveal">{created.api_key}</div>
            <button type="button" className="btn small" onClick={() => navigator.clipboard?.writeText(created.api_key)}>Copy</button>
          </div>
        )}
      </form>
    </Card>
  );
}
