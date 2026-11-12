import { useState } from "react";
import { HeartPulse, Timer, Trash2 } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { todayISO, useFetch } from "../hooks";
import { Badge, Button, CHART, Card, Empty, ErrorNote, Input, PageHeader, Spinner, Stat } from "../components/ui";

function clock(mins: number): string {
  const m = Math.floor(mins);
  const s = Math.round((mins - m) * 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function LogRunForm({ userId, onLogged }: { userId: string; onLogged: () => void }) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(todayISO());
  const [distance, setDistance] = useState("5");
  const [duration, setDuration] = useState("");
  const [hr, setHr] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.logRun(userId, {
        date,
        distance_km: Number(distance),
        duration_min: Number(duration),
        avg_hr_bpm: hr ? Number(hr) : null,
        notes: notes || null,
      });
      setDuration("");
      setHr("");
      setNotes("");
      setOpen(false);
      onLogged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the run");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Log a run"
      action={
        <Button variant={open ? "secondary" : "primary"} onClick={() => setOpen((v) => !v)}>
          {open ? "Close" : "Add run"}
        </Button>
      }
    >
      {open ? (
        <form onSubmit={submit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <Input type="date" value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} />
            <Input type="number" step="0.1" min="0.1" required placeholder="km*" value={distance} onChange={(e) => setDistance(e.target.value)} />
            <Input type="number" step="0.5" min="0.5" required placeholder="minutes*" value={duration} onChange={(e) => setDuration(e.target.value)} />
            <Input type="number" min="30" max="230" placeholder="avg HR" value={hr} onChange={(e) => setHr(e.target.value)} />
            <Input placeholder="Notes (route, feel)" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          {error && <p className="text-sm text-ember">{error}</p>}
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? "Saving..." : "Save run"}
          </Button>
        </form>
      ) : (
        <p className="text-sm text-mist">
          Or just tell the coach: "dün 5 km koştum, 27 dakika, nabız 152" — on Telegram or the chat, it
          lands here automatically.
        </p>
      )}
    </Card>
  );
}

export default function Cardio({ userId }: { userId: string }) {
  const runs = useFetch(() => api.runs(userId, 90), [userId]);
  const list = runs.data ?? [];

  const chartData = list.map((r) => ({
    date: r.date.slice(5),
    Pace: r.pace_min_per_km,
    "Avg HR": r.avg_hr_bpm,
  }));

  const last = list[list.length - 1] ?? null;
  const best5k = list.length ? Math.min(...list.map((r) => r.est_5k_min)) : null;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 29);
  const cutoffISO = cutoff.toISOString().slice(0, 10);
  const recent = list.filter((r) => r.date >= cutoffISO);
  const km30 = recent.reduce((s, r) => s + r.distance_km, 0);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Cardio capacity"
        subtitle="Pace and heart-rate trends — same pace at a lower HR means your aerobic base is growing."
      />
      {runs.error && <ErrorNote error={runs.error} />}
      {runs.loading && <Spinner />}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Best 5k estimate" value={best5k != null ? clock(best5k) : "—"} delta="Riegel formula, any distance" />
        <Stat label="Last run pace" value={last ? `${clock(last.pace_min_per_km)}/km` : "—"} delta={last ? `${last.distance_km} km on ${last.date}` : undefined} />
        <Stat label="Last avg HR" value={last?.avg_hr_bpm ? `${last.avg_hr_bpm} bpm` : "—"} delta="lower at same pace = fitter" />
        <Stat label="Distance, 30 days" value={`${km30.toFixed(1)} km`} delta={`${recent.length} runs`} />
      </div>

      <Card title="Pace and heart rate">
        {list.length === 0 ? (
          <Empty>
            No runs yet. Log one below or tell the coach "5 km koştum 28 dakikada" on Telegram.
          </Empty>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
              <CartesianGrid {...CHART.grid} />
              <XAxis dataKey="date" {...CHART.axes} />
              <YAxis
                yAxisId="p"
                {...CHART.axes}
                reversed
                domain={["auto", "auto"]}
                tickFormatter={(v: number) => clock(v)}
                width={52}
              />
              <YAxis yAxisId="h" orientation="right" {...CHART.axes} domain={["auto", "auto"]} width={40} />
              <Tooltip
                {...CHART.tooltip}
                formatter={(value, name) => (name === "Pace" ? [`${clock(Number(value))}/km`, name] : [`${value} bpm`, name])}
              />
              <Line yAxisId="p" dataKey="Pace" stroke={CHART.colors.lime} strokeWidth={2} dot={{ r: 2.5, fill: CHART.colors.lime }} connectNulls name="Pace" />
              <Line yAxisId="h" dataKey="Avg HR" stroke={CHART.colors.magenta} strokeWidth={1.5} strokeDasharray="4 4" dot={false} connectNulls name="Avg HR" />
            </LineChart>
          </ResponsiveContainer>
        )}
        <p className="mt-2 flex items-center gap-1.5 text-xs text-ash">
          <Timer className="h-3.5 w-3.5" /> Pace axis is inverted — the higher the line, the faster you ran.
        </p>
      </Card>

      <LogRunForm userId={userId} onLogged={runs.reload} />

      <Card title="Run history (90 days)">
        {list.length === 0 ? (
          <Empty>Nothing logged yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-ash">
                  <th className="py-1.5 pr-3 font-medium">Date</th>
                  <th className="py-1.5 pr-3 font-medium">Distance</th>
                  <th className="py-1.5 pr-3 font-medium">Time</th>
                  <th className="py-1.5 pr-3 font-medium">Pace</th>
                  <th className="py-1.5 pr-3 font-medium">Avg HR</th>
                  <th className="py-1.5 pr-3 font-medium">5k est.</th>
                  <th className="py-1.5 pr-3 font-medium">Notes</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {[...list].reverse().map((r) => (
                  <tr key={r.id} className="border-t border-edge">
                    <td className="py-1.5 pr-3 text-mist">
                      {r.date}{" "}
                      <Badge tone={r.source === "google" ? "lime" : r.source === "assistant" ? "amber" : "zinc"}>
                        {r.source === "google" ? "Fitbit" : r.source === "assistant" ? "coach" : "manual"}
                      </Badge>
                    </td>
                    <td className="py-1.5 pr-3">{r.distance_km} km</td>
                    <td className="py-1.5 pr-3">{clock(r.duration_min)}</td>
                    <td className="py-1.5 pr-3 text-lime">{clock(r.pace_min_per_km)}/km</td>
                    <td className="py-1.5 pr-3">{r.avg_hr_bpm ?? "—"}</td>
                    <td className="py-1.5 pr-3 text-cyan">{clock(r.est_5k_min)}</td>
                    <td className="max-w-[180px] truncate py-1.5 pr-3 text-ash">{r.notes}</td>
                    <td className="py-1.5 text-right">
                      <button
                        className="text-ash hover:text-ember"
                        onClick={async () => {
                          await api.deleteRun(userId, r.id);
                          runs.reload();
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="flex items-center gap-1.5 text-xs text-ash">
        <HeartPulse className="h-3.5 w-3.5" /> The coach sees these runs too — ask "kardiyovasküler
        gelişimim nasıl?" on Telegram for a pace-vs-HR analysis.
      </p>
    </div>
  );
}
