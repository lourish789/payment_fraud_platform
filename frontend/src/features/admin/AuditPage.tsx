import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { admin } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, Pagination, Select } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";

const LIMIT = 50;

export function AuditPage() {
  const t = useT();
  const { values, set, offset, setOffset } = useUrlState({ action: "all" });
  const q = useQuery({
    queryKey: ["admin", "audit", values, offset],
    queryFn: () => admin.audit({ action: opt(values.action), limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader title={t("Audit log")} description={t("Every state-changing operator action, written in the same transaction as the change. Scoring decisions have their own immutable decision records.")} />
      <Card flush title={q.data ? t("Entries: {n}", { n: q.data.total.toLocaleString() }) : t("Entries")}
        actions={<Select label={t("Action")} value={values.action} onChange={(v) => set("action", v)}
          options={[{ value: "all", label: t("All") }, { value: "case", label: t("Case resolutions") }, { value: "model", label: t("Model promotions") }, { value: "client", label: t("API clients") }]} />}>
        <Async query={q}>
          {(page) => page.items.length === 0 ? <Empty>{t("No audited actions yet.")}</Empty> : (
            <>
              <div className="table-wrap"><table className="table">
                <thead><tr><th>{t("When")}</th><th>{t("Actor")}</th><th>{t("Action")}</th><th>{t("Resource")}</th><th>{t("Details")}</th></tr></thead>
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
