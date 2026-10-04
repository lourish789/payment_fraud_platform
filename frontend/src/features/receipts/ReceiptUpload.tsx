import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { receipts } from "@/api/endpoints";
import type { ReceiptResult } from "@/api/types";
import { Badge, Code, ErrorState, Json, KV } from "@/components/ui";
import { useT } from "@/i18n";

const MAX_BYTES = 8 * 1024 * 1024;

export function ReceiptUpload({ caseId, defaultReference = "", onDone, compact }: {
  caseId?: string; defaultReference?: string; onDone?: (r: ReceiptResult) => void; compact?: boolean;
}) {
  const t = useT();
  const [file, setFile] = useState<File | null>(null);
  const [reference, setReference] = useState(defaultReference);
  const [localError, setLocalError] = useState<string | null>(null);
  const m = useMutation({ mutationFn: () => receipts.verify(file!, reference || undefined, caseId), onSuccess: onDone });

  function submit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    if (!file) return;
    if (file.size > MAX_BYTES) return setLocalError(t("File is larger than 8 MB."));
    m.mutate();
  }

  return (
    <form className="stack" onSubmit={submit}>
      <label className="field"><span>{t("Receipt image (PNG, JPEG or WebP, max 8 MB)")}</span>
        <input className="input" style={{ paddingTop: 5 }} type="file" accept="image/png,image/jpeg,image/webp"
               onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <label className="field"><span>{t("Claimed transaction reference")}</span>
        <input className="input mono" value={reference} onChange={(e) => setReference(e.target.value)} placeholder={t("optional")} />
      </label>
      <button className="btn primary" disabled={!file || m.isPending}>{m.isPending ? t("Verifying (OCR)...") : t("Verify receipt")}</button>
      {localError && <div className="alert-box error">{localError}</div>}
      {m.error && <ErrorState error={m.error} />}
      {m.data && <ReceiptResultView r={m.data} compact={compact} />}
    </form>
  );
}

export function ReceiptResultView({ r, compact }: { r: ReceiptResult; compact?: boolean }) {
  const t = useT();
  const show = (v: unknown) => (typeof v === "boolean" ? (v ? t("yes") : t("no")) : typeof v === "string" ? <Code value={v} /> : JSON.stringify(v));
  return (
    <div className="stack">
      <div className="row"><b>{t("Verdict")}</b> <Badge value={r.verdict} />{r.receipt_id && <span className="mono small muted">{r.receipt_id}</span>}</div>
      {r.note && <div className="small muted">{r.note}</div>}
      <KV items={Object.entries(r.checks).map(([k, v]) => [<Code value={k} />, show(v)])} />
      {!compact && <><h3>{t("Extracted by OCR")}</h3><Json value={r.extracted} /></>}
    </div>
  );
}
