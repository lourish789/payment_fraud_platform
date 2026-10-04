import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { investigations } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, Pagination, Select } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime, pct } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";
import { codeOptions } from "@/features/options";

const LIMIT = 25;

export function InvestigationsPage() {
  const t = useT();
  const { values, set, offset, setOffset } = useUrlState({ status: "all", recommendation: "all" });
  const q = useQuery({
    queryKey: ["investigations", values, offset],
    queryFn: () => investigations.list({ status: opt(values.status), recommendation: opt(values.recommendation), limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });
  return (
    <>
      <PageHeader title={t("Investigations")} description={t("Agent reports across all cases. Each one cites the tool calls its evidence came from.")} />
      <Card flush title={q.data ? t("Investigations: {n}", { n: q.data.total.toLocaleString() }) : t("Investigations")}
        actions={<div className="row">
          <Select label={t("Status")} value={values.status} onChange={(v) => set("status", v)}
            options={codeOptions(t, ["queued", "running", "done", "failed"])} />
          <Select label={t("Recommendation")} value={values.recommendation} onChange={(v) => set("recommendation", v)}
            options={codeOptions(t, ["fraud", "legit", "escalate"])} />
        </div>}>
        <Async query={q}>
          {(page) => page.items.length === 0 ? <Empty>{t("No investigations yet.")}</Empty> : (
            <>
              <div className="table-wrap"><table className="table">
                <thead><tr><th>{t("Case")}</th><th>{t("Status")}</th><th>{t("Recommendation")}</th><th className="num">{t("Confidence")}</th><th>{t("Summary")}</th>
                  <th>{t("Provider")}</th><th className="num">{t("Tokens")}</th><th>{t("Finished")}</th></tr></thead>
                <tbody>
                  {page.items.map((i) => (
                    <tr key={i.investigation_id}>
                      <td><Link className="mono" to={`/cases/${i.case_id}`}>{i.case_id}</Link></td>
                      <td><Badge value={i.status} /></td>
                      <td>{i.recommendation ? <Badge value={i.recommendation} /> : "-"}</td>
                      <td className="num">{pct(i.confidence, 0)}</td>
                      <td className="small" style={{ maxWidth: 420 }}>{i.report?.summary ?? i.error ?? "-"}</td>
                      <td className="small">{i.provider ?? "-"}</td>
                      <td className="num small">{(i.tokens.input + i.tokens.output).toLocaleString()}</td>
                      <td className="small nowrap">{dateTime(i.finished_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
              <Pagination total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}
