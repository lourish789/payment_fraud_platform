import { useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { cases } from "@/api/endpoints";
import { Amount, Async, Badge, Card, Empty, PageHeader, Pagination, RailBadge, Select } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime, pct, usd } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";
import { codeOptions, railOptions } from "@/features/options";

const LIMIT = 25;

export function CaseQueuePage() {
  const t = useT();
  const navigate = useNavigate();
  const { values, set, offset, setOffset } = useUrlState({ status: "open", rail: "all", decision: "all" });
  const q = useQuery({
    queryKey: ["cases", values, offset],
    queryFn: () => cases.list({ status: values.status, rail: opt(values.rail), decision: opt(values.decision), limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });

  return (
    <>
      <PageHeader title={t("Case queue")}
        description={t("Every review or decline on every rail, ordered by expected loss (probability × amount): the most money at risk first.")} />
      <Card flush title={q.data ? t("Cases: {n}", { n: q.data.total.toLocaleString() }) : t("Cases")}
        actions={
          <div className="row">
            <Select label={t("Status")} value={values.status} onChange={(v) => set("status", v)}
              options={[...codeOptions(t, ["open", "resolved"]).slice(1), { value: "all", label: t("All") }]} />
            <Select label={t("Rail")} value={values.rail} onChange={(v) => set("rail", v)} options={railOptions(t)} />
            <Select label={t("Decision")} value={values.decision} onChange={(v) => set("decision", v)}
              options={codeOptions(t, ["review", "decline"])} />
          </div>
        }>
        <Async query={q}>
          {(page) => page.items.length === 0 ? <Empty>{t("No cases match these filters.")}</Empty> : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr>
                    <th>{t("Case")}</th><th>{t("Rail")}</th><th>{t("Decision")}</th><th className="num">{t("Expected loss")}</th>
                    <th className="num">{t("Amount")}</th><th>{t("Agent")}</th><th>{t("Status")}</th><th>{t("Created")}</th>
                  </tr></thead>
                  <tbody>
                    {page.items.map((c) => (
                      <tr key={c.case_id} className="clickable" onClick={() => navigate(`/cases/${c.case_id}`)}>
                        <td><span className="mono">{c.case_id}</span><div className="small muted mono">{c.transaction_id}</div></td>
                        <td><RailBadge rail={c.rail} /></td>
                        <td><Badge value={c.decision} /></td>
                        <td className="num"><b>{usd(c.priority)}</b></td>
                        <td className="num"><Amount amount={c.amount_local ?? c.amount} currency={c.currency} amountUsd={c.amount} /></td>
                        <td>{c.agent ? (
                          c.agent.status === "done"
                            ? <><Badge value={c.agent.recommendation ?? "-"} /> <span className="small muted">{pct(c.agent.confidence, 0)}</span></>
                            : <Badge value={c.agent.status} />
                        ) : <span className="muted small">{t("not run")}</span>}</td>
                        <td>{c.resolution ? <Badge value={c.resolution} /> : <Badge value={c.status} tone="info" />}</td>
                        <td className="nowrap small">{dateTime(c.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}
