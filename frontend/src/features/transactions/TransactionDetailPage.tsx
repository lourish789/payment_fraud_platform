import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { transactions } from "@/api/endpoints";
import { Amount, Async, Badge, Card, Code, KV, PageHeader, RailBadge } from "@/components/ui";
import { useT } from "@/i18n";
import { dateTime } from "@/lib/format";
import { DecisionCard, PayloadCard } from "./parts";

export function TransactionDetailPage() {
  const t = useT();
  const { transactionId = "" } = useParams();
  const q = useQuery({ queryKey: ["transaction", transactionId], queryFn: () => transactions.get(transactionId) });
  return (
    <Async query={q}>
      {(x) => (
        <>
          <PageHeader title={t("Transaction {id}", { id: x.transaction_id })}
            description={<span className="row">
              <RailBadge rail={x.rail} />{x.decision && <Badge value={x.decision.decision} />}
              {x.money && <b><Amount amount={x.money.amount} currency={x.money.currency} amountUsd={x.money.amount_usd} /></b>}
            </span>}
            actions={<>
              {x.case_id && <Link className="btn primary" to={`/cases/${x.case_id}`}>{t("Open case")}</Link>}
              <Link className="btn" to="/transactions">{t("All transactions")}</Link>
            </>} />
          <div className="grid grid-3">
            <div className="span-2"><PayloadCard payload={x.transaction} /></div>
            <div className="stack">
              {x.decision && <DecisionCard d={x.decision} />}
              <Card title={t("Ground truth")}>
                {x.label ? <KV items={[[t("Label"), <Badge value={x.label.is_fraud ? "fraud" : "legit"} />], [t("Source"), <Code value={x.label.source} />], [t("Recorded"), dateTime(x.label.created_at)]]} />
                  : <span className="muted">{t("No label yet. Chargebacks typically arrive weeks later.")}</span>}
              </Card>
            </div>
          </div>
        </>
      )}
    </Async>
  );
}
