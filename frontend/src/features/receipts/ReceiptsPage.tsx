import { useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { receipts } from "@/api/endpoints";
import { Async, Badge, Card, Empty, PageHeader, Pagination, Select } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { opt, useUrlState } from "@/lib/useUrlState";
import { ReceiptResultView, ReceiptUpload } from "./ReceiptUpload";

const LIMIT = 25;
const VERDICTS = ["all", "verified", "mismatch", "not_found", "suspected_tampering", "unreadable"];

export function ReceiptsPage() {
  const { values, set, offset, setOffset } = useUrlState({ verdict: "all" });
  const [selected, setSelected] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["receipts", values, offset], queryFn: () => receipts.list({ verdict: opt(values.verdict), limit: LIMIT, offset }), placeholderData: keepPreviousData });
  const detail = useQuery({ queryKey: ["receipt", selected], queryFn: () => receipts.get(selected!), enabled: !!selected });

  return (
    <>
      <PageHeader title="Receipts" description="Proof-of-payment screenshots checked by OCR against the ledger, with image forensics as the fallback." />
      <div className="grid grid-3">
        <Card flush className="span-2" title="Verified receipts"
          actions={<Select label="Verdict" value={values.verdict} onChange={(v) => set("verdict", v)} options={VERDICTS.map((v) => ({ value: v, label: v === "all" ? "All" : v.replace(/_/g, " ") }))} />}>
          <Async query={q}>
            {(page) => page.items.length === 0 ? <Empty>No receipts yet.</Empty> : (
              <>
                <div className="table-wrap"><table className="table">
                  <thead><tr><th>Receipt</th><th>Verdict</th><th>Claimed reference</th><th>Case</th><th>Uploaded</th></tr></thead>
                  <tbody>
                    {page.items.map((r) => (
                      <tr key={r.receipt_id} className="clickable" onClick={() => setSelected(r.receipt_id)}>
                        <td className="mono">{r.receipt_id}</td><td><Badge value={r.verdict} /></td>
                        <td className="mono">{r.claimed_reference ?? "-"}</td>
                        <td>{r.case_id ? <Link to={`/cases/${r.case_id}`} onClick={(e) => e.stopPropagation()} className="mono">{r.case_id}</Link> : "-"}</td>
                        <td className="small nowrap">{dateTime(r.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table></div>
                <Pagination total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
              </>
            )}
          </Async>
        </Card>
        <div className="stack">
          {selected && <Card title="Receipt detail"><Async query={detail}>{(d) => <ReceiptResultView r={d.result} />}</Async></Card>}
          <Card title="Verify a receipt"><ReceiptUpload onDone={() => q.refetch()} /></Card>
        </div>
      </div>
    </>
  );
}

/** Merchant view: upload only (merchants cannot list other receipts). */
export function VerifyReceiptPage() {
  return (
    <>
      <PageHeader title="Verify a receipt" description="Upload a customer's proof-of-payment screenshot. We read it, match it against the ledger and check the image for edits." />
      <div style={{ maxWidth: 640 }}><Card><ReceiptUpload /></Card></div>
    </>
  );
}
