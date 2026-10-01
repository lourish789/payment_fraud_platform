import { useQuery } from "@tanstack/react-query";
import { monitoring } from "@/api/endpoints";
import { HorizontalBars } from "@/components/charts";
import { Async, Badge, Card, Empty, KV, PageHeader, Stat } from "@/components/ui";
import { dateTime, int, num, pct } from "@/lib/format";

export function MonitoringPage() {
  const q = useQuery({ queryKey: ["drift"], queryFn: monitoring.drift, refetchInterval: 60_000 });
  return (
    <>
      <PageHeader title="Model monitoring"
        description="Population Stability Index of live serving-time features and scores against the model's validation month. Labels arrive weeks late; drift is the early warning." />
      <Async query={q}>
        {(d) => d.status === "insufficient_data" ? (
          <Card><Empty>Not enough decisions by the current champion yet ({int(d.n)} of 500 needed).</Empty></Card>
        ) : (
          <div className="stack">
            <div className="grid grid-4">
              <Stat label="Status" value={<Badge value={d.status} />} sub={`warn > ${d.thresholds?.warn}, alert > ${d.thresholds?.alert}`} />
              <Stat label="Score PSI" value={num(d.score_psi, 4)} sub={`over ${int(d.n)} decisions`} />
              <Stat label="Flag rate" value={pct(d.flag_rate, 2)} />
              <Stat label="Live precision (flagged)" value={pct(d.live_precision_flagged)} sub={`${int(d.flagged_labelled)} flagged with labels`} />
            </div>
            <div className="grid grid-3">
              <Card className="span-2" title="Feature drift (PSI)" subtitle="Most important features, worst first">
                <HorizontalBars data={Object.entries(d.features_psi ?? {}).slice(0, 25).map(([name, value]) => ({ name, value }))}
                                color="review" format={(v) => num(v, 3)} />
              </Card>
              <Card title="Details">
                <KV items={[
                  ["Model", <span className="mono">{d.model_version}</span>],
                  ["Window", d.window ? `${dateTime(d.window.from)} to ${dateTime(d.window.to)}` : "-"],
                  ["Drifted features", d.drifted_features?.length ? d.drifted_features.map((f) => <span key={f} className="chip mono">{f}</span>) : "none"],
                ]} />
              </Card>
            </div>
          </div>
        )}
      </Async>
    </>
  );
}
