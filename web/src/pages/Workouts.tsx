import { FormEvent, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BookmarkPlus, Dumbbell, Trash2 } from "lucide-react";
import { api, Workout, WorkoutTemplate } from "../api";
import { useFetch, todayISO } from "../hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";

const AXES = { stroke: "#52525b", fontSize: 11, tickLine: false, axisLine: false } as const;
const TOOLTIP = {
  contentStyle: { background: "#18181b", border: "1px solid #3f3f46", borderRadius: 12, fontSize: 12 },
  labelStyle: { color: "#a1a1aa" },
} as const;

function Progression({ userId }: { userId: string }) {
  const names = useFetch(() => api.exerciseNames(userId, 90), [userId]);
  const [selected, setSelected] = useState("");
  const exercise = selected || (names.data?.[0] ?? "");
  const history = useFetch(() => (exercise ? api.exerciseHistory(userId, exercise, 90) : Promise.resolve([])), [userId, exercise]);

  const rows = history.data ?? [];
  const chartRows = rows.map((r) => ({ date: r.date.slice(5), "Top weight": r.best_weight_kg, Volume: r.volume_kg }));
  const latest = rows[rows.length - 1];
  const previous = rows.length > 1 ? rows[rows.length - 2] : null;
  const progress =
    latest && previous && latest.best_weight_kg != null && previous.best_weight_kg != null
      ? latest.best_weight_kg - previous.best_weight_kg
      : null;

  return (
    <Card
      title="Exercise progression (90 days)"
      action={
        names.data && names.data.length > 0 ? (
          <Select value={exercise} onChange={(e) => setSelected(e.target.value)} className="w-auto! min-w-44">
            {names.data.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        ) : undefined
      }
    >
      {!names.data || names.data.length === 0 ? (
        <Empty>Log workouts to see per-exercise weight progression.</Empty>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap gap-4 text-sm">
            <span className="text-zinc-400">
              Top weight: <span className="font-semibold text-zinc-100">{latest?.best_weight_kg ?? "—"} kg</span>
              {progress != null && progress !== 0 && (
                <span className={progress > 0 ? "ml-2 text-lime-400" : "ml-2 text-red-400"}>
                  {progress > 0 ? "+" : ""}
                  {progress} kg vs previous
                </span>
              )}
            </span>
            <span className="text-zinc-400">
              Last session volume: <span className="font-semibold text-zinc-100">{latest ? latest.volume_kg.toLocaleString() : "—"} kg</span>
            </span>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chartRows}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" {...AXES} />
              <YAxis yAxisId="w" domain={["dataMin - 2.5", "dataMax + 2.5"]} {...AXES} width={40} />
              <YAxis yAxisId="v" orientation="right" {...AXES} width={48} hide />
              <Tooltip {...TOOLTIP} />
              <Line yAxisId="w" dataKey="Top weight" stroke="#a3e635" strokeWidth={2} dot={{ r: 3 }} unit=" kg" />
              <Line yAxisId="v" dataKey="Volume" stroke="#38bdf8" strokeWidth={1.5} dot={false} strokeDasharray="4 4" unit=" kg" />
            </LineChart>
          </ResponsiveContainer>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-800 text-left text-xs uppercase tracking-wider text-zinc-500">
                  <th className="py-1.5 pr-3">Date</th>
                  <th className="py-1.5 pr-3">Session</th>
                  <th className="py-1.5 pr-3">Sets</th>
                  <th className="py-1.5 pr-3 text-right">Top</th>
                  <th className="py-1.5 text-right">Volume</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {[...rows].reverse().slice(0, 8).map((r, i) => (
                  <tr key={`${r.date}-${i}`} className="text-zinc-300">
                    <td className="py-1.5 pr-3">{r.date}</td>
                    <td className="py-1.5 pr-3">{r.session_name}</td>
                    <td className="py-1.5 pr-3 font-mono text-xs text-zinc-400">
                      {r.sets.map((s) => `${s.reps ?? "?"}${s.weight_kg != null ? `@${s.weight_kg}` : ""}`).join(" ")}
                    </td>
                    <td className="py-1.5 pr-3 text-right font-medium">{r.best_weight_kg ?? "—"}</td>
                    <td className="py-1.5 text-right">{r.volume_kg.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

/** "Bench press: 3x8 @80 rpe8" -> structured exercise. Weight/RPE apply to every set on the line. */
export function parseExerciseLine(line: string) {
  const [rawName, ...rest] = line.split(":");
  const setsPart = rest.join(":");
  const weight = setsPart.match(/@\s*(\d+(?:\.\d+)?)/);
  const rpe = setsPart.match(/rpe\s*(\d+(?:\.\d+)?)/i);
  const sets: { reps?: number; weight_kg?: number; rpe?: number }[] = [];
  for (const match of setsPart.matchAll(/(\d+)\s*[x×]\s*(\d+)/g)) {
    const one: { reps?: number; weight_kg?: number; rpe?: number } = { reps: Number(match[2]) };
    if (weight) one.weight_kg = Number(weight[1]);
    if (rpe) one.rpe = Number(rpe[1]);
    for (let i = 0; i < Number(match[1]); i++) sets.push({ ...one });
  }
  return { name: rawName.trim().slice(0, 120), sets: sets.length ? sets : [{}] };
}

function formatSets(sets: { reps?: number | null; weight_kg?: number | null; rpe?: number | null }[]): string {
  return sets
    .map((s) => {
      const parts = [s.reps != null ? `${s.reps} reps` : "? reps"];
      if (s.weight_kg != null) parts.push(`@ ${s.weight_kg} kg`);
      if (s.rpe != null) parts.push(`rpe ${s.rpe}`);
      return parts.join(", ");
    })
    .join(" · ");
}

/** "Bench press: 4x6-8 @80 rpe7-8" -> template exercise (reps keep their range). */
function parseTemplateLine(line: string) {
  const idx = line.indexOf(":");
  const name = (idx === -1 ? line : line.slice(0, idx)).trim();
  const rest = idx === -1 ? "" : line.slice(idx + 1);
  const m = rest.match(/(\d+)\s*[x×]\s*(\d+(?:\s*[-–]\s*\d+)?)/);
  const w = rest.match(/@\s*(\d+(?:\.\d+)?)/);
  const rpe = rest.match(/rpe\s*([\d.]+(?:\s*[-–]\s*[\d.]+)?)/i);
  return {
    name: name.slice(0, 120),
    sets: m ? Number(m[1]) : 3,
    reps: m ? m[2].replace(/\s/g, "") : "8",
    weight_kg: w ? Number(w[1]) : null,
    rpe_target: rpe ? rpe[1].replace(/\s/g, "") : "",
  };
}

function templateLine(e: { name: string; sets: number; reps: string; weight_kg?: number | null; rpe_target?: string }): string {
  return `${e.name}: ${e.sets}x${e.reps}${e.weight_kg ? ` @${e.weight_kg}` : ""}${e.rpe_target ? ` rpe${e.rpe_target}` : ""}`;
}

function TemplatesCard({ userId }: { userId: string }) {
  const templates = useFetch(() => api.workoutTemplates(userId), [userId]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  const seed = async () => {
    await api.seedWorkoutTemplates(userId);
    templates.reload();
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    const lines = text.split("\n").filter((l) => l.trim());
    if (!name.trim() || !lines.length) {
      setError("Name and at least one exercise line required.");
      return;
    }
    try {
      await api.saveWorkoutTemplate(userId, { name: name.trim(), exercises: lines.map(parseTemplateLine) });
      setName("");
      setText("");
      setCreating(false);
      templates.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  return (
    <Card
      title="Templates"
      action={
        <div className="flex gap-2">
          <Button onClick={seed}>Load starter templates</Button>
          <Button onClick={() => setCreating(!creating)}>
            <BookmarkPlus className="mr-1 inline h-3.5 w-3.5" />
            New
          </Button>
        </div>
      }
    >
      {creating && (
        <form onSubmit={save} className="mb-4 space-y-3 rounded-xl border border-zinc-800 p-3">
          <Input placeholder="Template name (Push Day)" value={name} onChange={(e) => setName(e.target.value)} />
          <Textarea
            rows={4}
            placeholder={"One exercise per line:\nBench press: 4x6-8 @80 rpe7-8\nIncline dumbbell press: 3x8-10"}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {error && <p className="text-sm text-red-400">{error}</p>}
          <Button type="submit" variant="primary">
            Save template
          </Button>
        </form>
      )}
      {!templates.data?.length ? (
        <Empty>
          No templates yet. "Load starter templates" adds Push/Pull/Legs; or create your own. The coach
          on Telegram knows them too — ask "push day'de ne yapacağım?".
        </Empty>
      ) : (
        <ul className="space-y-2">
          {templates.data.map((t) => (
            <li key={t.id} className="flex items-start justify-between gap-2 rounded-lg bg-zinc-900/40 p-3">
              <div className="min-w-0">
                <span className="text-sm font-semibold text-zinc-100">{t.name}</span>
                <p className="mt-0.5 text-xs text-zinc-500">
                  {t.exercises.map((e) => `${e.name} ${e.sets}x${e.reps}`).join(" · ")}
                </p>
              </div>
              <button
                className="shrink-0 text-zinc-600 hover:text-red-400"
                onClick={async () => {
                  await api.deleteWorkoutTemplate(userId, t.name);
                  templates.reload();
                }}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function WorkoutCard({ userId, workout, onDeleted }: { userId: string; workout: Workout; onDeleted: () => void }) {
  const remove = async () => {
    await api.deleteWorkout(userId, workout.id);
    onDeleted();
  };
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Dumbbell className="h-4 w-4 text-lime-400" />
          <span className="text-sm font-semibold text-zinc-100">{workout.session_name}</span>
          {workout.duration_min != null && <Badge>{workout.duration_min} min</Badge>}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-zinc-500">{workout.date}</span>
          <button onClick={remove} className="text-zinc-600 transition-colors hover:text-red-400">
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>
      <ul className="mt-3 space-y-1.5">
        {workout.exercises.map((exercise, index) => (
          <li key={index} className="flex justify-between gap-4 text-sm">
            <span className="text-zinc-300">{exercise.name}</span>
            <span className="shrink-0 text-zinc-500">{formatSets(exercise.sets)}</span>
          </li>
        ))}
      </ul>
      {workout.notes && <p className="mt-2 text-xs italic text-zinc-500">{workout.notes}</p>}
    </div>
  );
}

export default function Workouts({ userId }: { userId: string }) {
  const workouts = useFetch(() => api.workouts(userId, 30), [userId]);
  const session = useFetch(() => api.todaySession(userId), [userId]);
  const templates = useFetch(() => api.workoutTemplates(userId), [userId]);
  const [form, setForm] = useState({ date: todayISO(), session_name: "", exercises: "", duration_min: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Prefill the log form from a template; weights come from the last logged session when
  // available (progressive overload), otherwise from the template default.
  const loadTemplate = async (t: WorkoutTemplate) => {
    const lines = await Promise.all(
      t.exercises.map(async (e) => {
        let weight = e.weight_kg ?? null;
        try {
          const hist = await api.exerciseHistory(userId, e.name, 90);
          const last = hist[hist.length - 1];
          if (last?.best_weight_kg != null) weight = last.best_weight_kg;
        } catch {
          /* history lookup is best effort */
        }
        return templateLine({ ...e, weight_kg: weight });
      })
    );
    setForm((f) => ({ ...f, session_name: f.session_name || t.name, exercises: lines.join("\n") }));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const lines = form.exercises.split("\n").filter((line) => line.trim());
    if (!lines.length) {
      setError("Add at least one exercise line.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.logWorkout(userId, {
        date: form.date,
        session_name: form.session_name,
        exercises: lines.map(parseExerciseLine),
        duration_min: form.duration_min ? Number(form.duration_min) : null,
        notes: form.notes || null,
      });
      setForm({ date: todayISO(), session_name: "", exercises: "", duration_min: "", notes: "" });
      workouts.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  if (workouts.loading) return <Spinner />;

  return (
    <div>
      <PageHeader title="Workouts" subtitle="Log sessions here or just text them to the coach on Telegram." />

      {session.data && (
        <Card title="Today's planned session" className="mb-4">
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-zinc-300">{session.data.text}</pre>
        </Card>
      )}

      <div className="mb-4">
        <Progression userId={userId} />
      </div>

      <div className="mb-4">
        <TemplatesCard userId={userId} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Log a workout">
          {(templates.data?.length ?? 0) > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-zinc-500">Start from template:</span>
              {templates.data!.map((t) => (
                <Button key={t.id} onClick={() => loadTemplate(t)}>
                  {t.name}
                </Button>
              ))}
            </div>
          )}
          <form onSubmit={submit} className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-[150px_1fr_110px]">
              <Input type="date" value={form.date} max={todayISO()} onChange={(e) => setForm({ ...form, date: e.target.value })} />
              <Input required placeholder="Session name (Lower A)" value={form.session_name} onChange={(e) => setForm({ ...form, session_name: e.target.value })} />
              <Input type="number" min={0} max={600} placeholder="Minutes" value={form.duration_min} onChange={(e) => setForm({ ...form, duration_min: e.target.value })} />
            </div>
            <Textarea
              required
              rows={4}
              placeholder={"One exercise per line:\nLeg press: 4x10 @120 rpe7\nLeg curl: 3x12"}
              value={form.exercises}
              onChange={(e) => setForm({ ...form, exercises: e.target.value })}
            />
            <Input placeholder="Notes (optional)" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            {error && <p className="text-sm text-red-400">{error}</p>}
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Saving..." : "Save workout"}
            </Button>
          </form>
        </Card>

        <Card title="Last 30 days">
          {workouts.error ? (
            <ErrorNote error={workouts.error} />
          ) : workouts.data && workouts.data.length ? (
            <div className="space-y-3">
              {[...workouts.data].reverse().map((workout) => (
                <WorkoutCard key={workout.id} userId={userId} workout={workout} onDeleted={workouts.reload} />
              ))}
            </div>
          ) : (
            <Empty>No workouts logged yet.</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}
