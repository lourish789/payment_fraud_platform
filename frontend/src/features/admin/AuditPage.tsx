import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, Pagination, Select } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";

const LIMIT = 50;

export function AuditPage() {
  const { values, set, offset, setOffset } = useUrlState({ action: "all" });
  const q = useQuery({
    queryKey: ["admin", "audit", values, offset],
    queryFn: () => admin.audit({ action: opt(values.action), limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader title="Audit log" description="Every state-changing operator action, written in the same transaction as the change. Scoring decisions have their own immutable decision records." />
      <Card flush title={q.data ? `${q.data.total.toLocaleString()} entries` : "Entries"}
        actions={<Select label="Action" value={values.action} onChange={(v) => set("action", v)}
          options={[{ value: "all", label: "All" }, { value: "case", label: "Case resolutions" }, { value: "model", label: "Model promotions" }, { value: "client", label: "API clients" }]} />}>
        <Async query={q}>
          {(page) => page.items.length === 0 ? <Empty>No audited actions yet.</Empty> : (
            <>
              <div className="table-wrap"><table className="table">
                <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead>
                <tbody>{page.items.map((a) => (
                  <tr key={a.id}>
                    <td className="small nowrap">{dateTime(a.created_at)}</td>
                    <td>{a.actor_name}<div className="mono small muted">{a.actor_id}</div></td>
                    <td><Badge value={a.action} tone="info" /></td>
                    <td className="mono small">{a.resource}</td>
                    <td className="small">{a.details ? Object.entries(a.details).map(([k, v]) => <div key={k}><span className="muted">{k}:</span> {String(v ?? "-")}</div>) : "-"}</td>
                  </tr>
                ))}</tbody>
              </table></div>
              <Pagination total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}
