import { FormEvent, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { useFetch, todayISO } from "../hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Spinner } from "../components/ui";

const AXES = { stroke: "#52525b", fontSize: 11, tickLine: false, axisLine: false } as const;
const TOOLTIP = {
  contentStyle: { background: "#18181b", border: "1px solid #3f3f46", borderRadius: 12, fontSize: 12 },
  labelStyle: { color: "#a1a1aa" },
} as const;

const FIELDS = [
  { key: "weight_kg", label: "Weight (kg)" },
  { key: "body_fat_pct", label: "Body fat (%)" },
  { key: "waist_cm", label: "Waist (cm)" },
  { key: "chest_cm", label: "Chest (cm)" },
  { key: "arm_cm", label: "Arm (cm)" },
  { key: "thigh_cm", label: "Thigh (cm)" },
] as const;

export default function Measures({ userId }: { userId: string }) {
  const measurements = useFetch(() => api.measurements(userId, 365), [userId]);
  const [form, setForm] = useState<Record<string, string>>({ date: todayISO(), notes: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = { notes: form.notes || null };
    let anyField = false;
    for (const { key } of FIELDS) {
      if (form[key]) {
        body[key] = Number(form[key]);
        anyField = true;
      }
    }
    if (!anyField && !form.notes) {
      setError("Enter at least one measurement or a note.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.saveMeasurement(userId, form.date, body);
      setForm({ date: todayISO(), notes: "" });
      measurements.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  if (measurements.loading) return <Spinner />;

  const rows = measurements.data ?? [];
  const chartRows = rows
    .filter((m) => m.weight_kg != null || m.body_fat_pct != null)
    .map((m) => ({ date: m.date.slice(5), Weight: m.weight_kg, "Body fat %": m.body_fat_pct }));

  return (
    <div>
      <PageHeader title="Body measures" subtitle="Weight, body fat and tape measures. Fitbit scale weigh-ins sync automatically." />

      {chartRows.length > 0 && (
        <Card title="Weight & body fat (1 year)" className="mb-4">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={chartRows}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" {...AXES} />
              <YAxis yAxisId="w" domain={["dataMin - 2", "dataMax + 2"]} {...AXES} width={40} />
              <YAxis yAxisId="bf" orientation="right" domain={["dataMin - 2", "dataMax + 2"]} {...AXES} width={36} />
              <Tooltip {...TOOLTIP} />
              <Line yAxisId="w" dataKey="Weight" stroke="#a3e635" strokeWidth={2} dot={{ r: 2 }} unit=" kg" />
              <Line yAxisId="bf" dataKey="Body fat %" stroke="#38bdf8" strokeWidth={2} dot={{ r: 2 }} unit=" %" />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Add measurement">
          <form onSubmit={submit} className="space-y-3">
            <Input type="date" value={form.date} max={todayISO()} onChange={(e) => setForm({ ...form, date: e.target.value })} className="sm:w-48" />
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {FIELDS.map(({ key, label }) => (
                <Input
                  key={key}
                  type="number"
                  step="0.1"
                  placeholder={label}
                  value={form[key] ?? ""}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                />
              ))}
            </div>
            <Input placeholder="Notes (optional)" value={form.notes ?? ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            {error && <p className="text-sm text-red-400">{error}</p>}
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Saving..." : "Save measurement"}
            </Button>
          </form>
        </Card>

        <Card title="History">
          {measurements.error ? (
            <ErrorNote error={measurements.error} />
          ) : rows.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 text-left text-xs uppercase tracking-wider text-zinc-500">
                    <th className="py-2 pr-3">Date</th>
                    <th className="py-2 pr-3">Weight</th>
                    <th className="py-2 pr-3">BF %</th>
                    <th className="py-2 pr-3">Waist</th>
                    <th className="py-2">Src</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {[...rows].reverse().map((m) => (
                    <tr key={m.date} className="text-zinc-300">
                      <td className="py-2 pr-3">{m.date}</td>
                      <td className="py-2 pr-3 font-medium">{m.weight_kg ?? "—"}</td>
                      <td className="py-2 pr-3">{m.body_fat_pct ?? "—"}</td>
                      <td className="py-2 pr-3">{m.waist_cm ?? "—"}</td>
                      <td className="py-2">
                        <Badge tone={m.source === "google" ? "lime" : "zinc"}>{m.source}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>No measurements yet. Weigh in above, or sync a Fitbit scale in Settings.</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}
