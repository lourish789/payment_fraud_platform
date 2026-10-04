import { useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { transactions } from "@/api/endpoints";
import { Amount, Async, Badge, Card, Empty, PageHeader, Pagination, RailBadge, Select } from "@/components/ui";
import { msg, useT } from "@/i18n";
import { dateTime, prob } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";
import { codeOptions, decisionOptions, railOptions } from "@/features/options";

const LIMIT = 50;

export function TransactionsPage() {
  const t = useT();
  const navigate = useNavigate();
  const { values, set, offset, setOffset } = useUrlState({ rail: "all", decision: "all", label: "all", q: "" });
  const q = useQuery({
    queryKey: ["transactions", values, offset],
    queryFn: () => transactions.list({ rail: opt(values.rail), decision: opt(values.decision), label: opt(values.label),
                                       q: opt(values.q), limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader title={t("Transactions")} description={t("Every scored payment on every rail, newest first by event time.")} />
      <Card flush title={q.data ? t("Transactions: {n}", { n: q.data.total.toLocaleString() }) : t("Transactions")}
        actions={
          <div className="row">
            <label className="field"><span>{t("ID prefix")}</span>
              <input className="input" placeholder={t("e.g. {example}", { example: "3577" })} defaultValue={values.q}
                     onKeyDown={(e) => e.key === "Enter" && set("q", (e.target as HTMLInputElement).value.trim())} />
            </label>
            <Select label={t("Rail")} value={values.rail} onChange={(v) => set("rail", v)} options={railOptions(t)} />
            <Select label={t("Decision")} value={values.decision} onChange={(v) => set("decision", v)} options={decisionOptions(t)} />
            <Select label={t("Label")} value={values.label} onChange={(v) => set("label", v)}
              options={[...codeOptions(t, ["fraud", "legit"], msg("Any")), { value: "unlabelled", label: t("Unlabelled") }]} />
          </div>
        }>
        <Async query={q}>
          {(page) => page.items.length === 0 ? <Empty>{t("No transactions match these filters.")}</Empty> : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>{t("Transaction")}</th><th>{t("Rail")}</th><th>{t("Event time")}</th><th className="num">{t("Amount")}</th>
                    <th>{t("Decision")}</th><th className="num">{t("P(fraud)")}</th><th>{t("Label")}</th><th>{t("Case")}</th></tr></thead>
                  <tbody>
                    {page.items.map((x) => (
                      <tr key={x.transaction_id} className="clickable" onClick={() => navigate(`/transactions/${encodeURIComponent(x.transaction_id)}`)}>
                        <td className="mono">{x.transaction_id}</td>
                        <td><RailBadge rail={x.rail} /></td>
                        <td className="nowrap small">{dateTime(x.event_time)}</td>
                        <td className="num"><Amount amount={x.amount_local ?? x.amount} currency={x.currency} amountUsd={x.amount} /></td>
                        <td>{x.decision ? <Badge value={x.decision} /> : "-"}</td>
                        <td className="num">{prob(x.fraud_probability)}</td>
                        <td>{x.label === null ? <span className="muted small">-</span> : <Badge value={x.label ? "fraud" : "legit"} />}</td>
                        <td className="mono small">{x.case_id ?? ""}</td>
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
