import { useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { transactions } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, Pagination, RailBadge, Select } from "@/components/ui";
import { dateTime, money, prob } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";
import { DECISION_OPTIONS, RAIL_OPTIONS } from "@/features/options";

const LIMIT = 50;

export function TransactionsPage() {
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
      <PageHeader title="Transactions" description="Every scored payment on every rail, newest first by event time." />
      <Card flush title={q.data ? `${q.data.total.toLocaleString()} transactions` : "Transactions"}
        actions={
          <div className="row">
            <label className="field"><span>ID prefix</span>
              <input className="input" placeholder="e.g. 3577" defaultValue={values.q}
                     onKeyDown={(e) => e.key === "Enter" && set("q", (e.target as HTMLInputElement).value.trim())} />
            </label>
            <Select label="Rail" value={values.rail} onChange={(v) => set("rail", v)} options={RAIL_OPTIONS} />
            <Select label="Decision" value={values.decision} onChange={(v) => set("decision", v)} options={DECISION_OPTIONS} />
            <Select label="Label" value={values.label} onChange={(v) => set("label", v)}
              options={[{ value: "all", label: "Any" }, { value: "fraud", label: "Fraud" }, { value: "legit", label: "Legit" }, { value: "unlabelled", label: "Unlabelled" }]} />
          </div>
        }>
        <Async query={q}>
          {(page) => page.items.length === 0 ? <Empty>No transactions match these filters.</Empty> : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Transaction</th><th>Rail</th><th>Event time</th><th className="num">Amount</th>
                    <th>Decision</th><th className="num">P(fraud)</th><th>Label</th><th>Case</th></tr></thead>
                  <tbody>
                    {page.items.map((t) => (
                      <tr key={t.transaction_id} className="clickable" onClick={() => navigate(`/transactions/${encodeURIComponent(t.transaction_id)}`)}>
                        <td className="mono">{t.transaction_id}</td>
                        <td><RailBadge rail={t.rail} /></td>
                        <td className="nowrap small">{dateTime(t.event_time)}</td>
                        <td className="num">{money(t.amount, t.currency ?? "USD")}</td>
                        <td>{t.decision ? <Badge value={t.decision} /> : "-"}</td>
                        <td className="num">{prob(t.fraud_probability)}</td>
                        <td>{t.label === null ? <span className="muted small">-</span> : <Badge value={t.label ? "fraud" : "legit"} />}</td>
                        <td className="mono small">{t.case_id ?? ""}</td>
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
