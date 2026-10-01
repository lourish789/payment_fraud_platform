import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { transactions } from "@/api/endpoints";
import { Async, Badge, Card, KV, PageHeader, RailBadge } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { DecisionCard, PayloadCard } from "./parts";

export function TransactionDetailPage() {
  const { transactionId = "" } = useParams();
  const q = useQuery({ queryKey: ["transaction", transactionId], queryFn: () => transactions.get(transactionId) });
  return (
    <Async query={q}>
      {(t) => (
        <>
          <PageHeader title={`Transaction ${t.transaction_id}`}
            description={<span className="row"><RailBadge rail={t.rail} />{t.decision && <Badge value={t.decision.decision} />}</span>}
            actions={<>
              {t.case_id && <Link className="btn primary" to={`/cases/${t.case_id}`}>Open case</Link>}
              <Link className="btn" to="/transactions">All transactions</Link>
            </>} />
          <div className="grid grid-3">
            <div className="span-2"><PayloadCard payload={t.transaction} /></div>
            <div className="stack">
              {t.decision && <DecisionCard d={t.decision} />}
              <Card title="Ground truth">
                {t.label ? <KV items={[["Label", <Badge value={t.label.is_fraud ? "fraud" : "legit"} />], ["Source", t.label.source], ["Recorded", dateTime(t.label.created_at)]]} />
                  : <span className="muted">No label yet. Chargebacks typically arrive weeks later.</span>}
              </Card>
            </div>
          </div>
        </>
      )}
    </Async>
  );
}
