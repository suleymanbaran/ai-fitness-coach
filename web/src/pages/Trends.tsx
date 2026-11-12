import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { useFetch } from "../hooks";
import { Card, ErrorNote, PageHeader, Spinner } from "../components/ui";

const AXES = { stroke: "#52525b", fontSize: 11, tickLine: false, axisLine: false } as const;
const TOOLTIP = {
  contentStyle: { background: "#18181b", border: "1px solid #3f3f46", borderRadius: 12, fontSize: 12 },
  labelStyle: { color: "#a1a1aa" },
} as const;

export default function Trends({ userId }: { userId: string }) {
  const [days, setDays] = useState(30);
  const metrics = useFetch(() => api.days(userId, days), [userId, days]);
  const measures = useFetch(() => api.measurements(userId, days), [userId, days]);
  const intake = useFetch(() => api.intake(userId, days), [userId, days]);
  const workouts = useFetch(() => api.workouts(userId, days), [userId, days]);

  const metricRows = useMemo(
    () =>
      (metrics.data ?? []).map((d) => ({
        date: d.date.slice(5),
        HRV: d.hrv_ms,
        "Resting HR": d.resting_hr_bpm,
        "Sleep (h)": d.sleep_minutes != null ? +(d.sleep_minutes / 60).toFixed(1) : null,
        "Sleep score": d.sleep_score,
        Steps: d.steps,
        "Burned kcal": d.active_kcal,
      })),
    [metrics.data]
  );

  const weightRows = useMemo(
    () =>
      (measures.data ?? [])
        .filter((m) => m.weight_kg != null || m.body_fat_pct != null)
        .map((m) => ({ date: m.date.slice(5), Weight: m.weight_kg, "Body fat %": m.body_fat_pct })),
    [measures.data]
  );

  const intakeRows = useMemo(() => (intake.data ?? []).map((d) => ({ date: d.date.slice(5), kcal: d.kcal })), [intake.data]);

  const workoutRows = useMemo(() => {
    const byWeek = new Map<string, number>();
    for (const w of workouts.data ?? []) {
      const weekStart = new Date(w.date);
      weekStart.setDate(weekStart.getDate() - ((weekStart.getDay() + 6) % 7)); // Monday
      const key = weekStart.toISOString().slice(5, 10);
      byWeek.set(key, (byWeek.get(key) ?? 0) + 1);
    }
    return [...byWeek.entries()].sort().map(([date, count]) => ({ date, sessions: count }));
  }, [workouts.data]);

  if (metrics.loading) return <Spinner />;
  if (metrics.error) return <ErrorNote error={metrics.error} />;

  return (
    <div>
      <PageHeader title="Trends" subtitle="Everything the coach sees, over time.">
        <div className="flex gap-1 rounded-lg border border-zinc-800 bg-zinc-900 p-1">
          {[14, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-md px-3 py-1 text-xs font-medium ${
                days === d ? "bg-lime-400 text-zinc-950" : "text-zinc-400 hover:text-zinc-100"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </PageHeader>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Weight & body fat">
          {weightRows.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={weightRows}>
                <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" {...AXES} />
                <YAxis yAxisId="w" domain={["dataMin - 2", "dataMax + 2"]} {...AXES} width={40} />
                <YAxis yAxisId="bf" orientation="right" domain={["dataMin - 2", "dataMax + 2"]} {...AXES} width={36} />
                <Tooltip {...TOOLTIP} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line yAxisId="w" dataKey="Weight" stroke="#a3e635" strokeWidth={2} dot={false} unit=" kg" />
                <Line yAxisId="bf" dataKey="Body fat %" stroke="#38bdf8" strokeWidth={2} dot={false} unit=" %" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-10 text-center text-sm text-zinc-500">Log measurements (or sync a Fitbit scale) to see weight trends.</p>
          )}
        </Card>

        <Card title="HRV & resting heart rate">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={metricRows}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" {...AXES} />
              <YAxis {...AXES} width={36} />
              <Tooltip {...TOOLTIP} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line dataKey="HRV" stroke="#a3e635" strokeWidth={2} dot={false} connectNulls />
              <Line dataKey="Resting HR" stroke="#f87171" strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Sleep">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={metricRows}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" {...AXES} />
              <YAxis {...AXES} width={36} />
              <Tooltip {...TOOLTIP} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line dataKey="Sleep (h)" stroke="#818cf8" strokeWidth={2} dot={false} connectNulls />
              <Line dataKey="Sleep score" stroke="#38bdf8" strokeWidth={1.5} dot={false} connectNulls strokeDasharray="4 4" />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Steps & energy burned">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={metricRows}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" {...AXES} />
              <YAxis yAxisId="s" {...AXES} width={48} />
              <YAxis yAxisId="k" orientation="right" {...AXES} width={40} />
              <Tooltip {...TOOLTIP} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line yAxisId="s" dataKey="Steps" stroke="#a3e635" strokeWidth={2} dot={false} connectNulls />
              <Line yAxisId="k" dataKey="Burned kcal" stroke="#fb923c" strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card title="Calorie intake">
          {intakeRows.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={intakeRows}>
                <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" {...AXES} />
                <YAxis {...AXES} width={44} />
                <Tooltip {...TOOLTIP} cursor={{ fill: "#27272a55" }} />
                <Bar dataKey="kcal" fill="#a3e635" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-10 text-center text-sm text-zinc-500">No intake logged in this window.</p>
          )}
        </Card>

        <Card title="Workouts per week" className="lg:col-span-2">
          {workoutRows.length ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={workoutRows}>
                <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" {...AXES} />
                <YAxis allowDecimals={false} {...AXES} width={30} />
                <Tooltip {...TOOLTIP} cursor={{ fill: "#27272a55" }} />
                <Bar dataKey="sessions" fill="#38bdf8" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-10 text-center text-sm text-zinc-500">No workouts logged in this window.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
