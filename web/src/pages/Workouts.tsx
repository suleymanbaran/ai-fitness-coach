import { FormEvent, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BookmarkPlus, Dumbbell, Trash2, X } from "lucide-react";
import { api, Workout, WorkoutTemplate } from "../api";
import { useFetch, todayISO } from "../hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";

const AXES = { stroke: "#6b7580", fontSize: 11, tickLine: false, axisLine: false } as const;
const TOOLTIP = {
  contentStyle: { background: "#161c24", border: "1px solid #2c3540", borderRadius: 12, fontSize: 12 },
  labelStyle: { color: "#a7b1bd" },
} as const;

function Progression({ userId }: { userId: string }) {
  const names = useFetch(() => api.exerciseNames(userId, 90), [userId]);
  const [selected, setSelected] = useState("");
  const exercise = selected || (names.data?.[0] ?? "");
  const history = useFetch(() => (exercise ? api.exerciseHistory(userId, exercise, 90) : Promise.resolve([])), [userId, exercise]);
  const strength = useFetch(
    () => (exercise ? api.strengthSummary(userId, exercise, 8) : Promise.resolve(null)),
    [userId, exercise]
  );

  const rows = history.data ?? [];
  const chartRows = rows.map((r) => ({ date: r.date.slice(5), "Top weight": r.best_weight_kg, "e1RM": r.est_1rm_kg }));
  const weeklyRows = (strength.data?.weekly ?? []).map((w) => ({ week: w.week_start.slice(5), Volume: w.volume_kg, Sets: w.sets }));
  const latest = rows[rows.length - 1];
  const previous = rows.length > 1 ? rows[rows.length - 2] : null;
  const progress =
    latest && previous && latest.best_weight_kg != null && previous.best_weight_kg != null
      ? latest.best_weight_kg - previous.best_weight_kg
      : null;

  return (
    <Card
      title="Strength progression (90 days)"
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
          <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-sm">
            <span className="text-mist">
              Top weight: <span className="font-semibold text-cloud">{latest?.best_weight_kg ?? "—"} kg</span>
              {progress != null && progress !== 0 && (
                <span className={progress > 0 ? "ml-2 text-lime" : "ml-2 text-ember"}>
                  {progress > 0 ? "+" : ""}
                  {progress} kg vs previous
                </span>
              )}
            </span>
            <span className="text-mist">
              Est. 1RM: <span className="font-semibold text-cloud">{strength.data?.best_e1rm_kg ?? latest?.est_1rm_kg ?? "—"} kg</span>
            </span>
            {strength.data?.pr_weight_kg != null && (
              <span className="text-mist">
                PR: <span className="font-semibold text-amber">{strength.data.pr_weight_kg} kg x {strength.data.pr_reps}</span>
                <span className="text-ash"> ({strength.data.pr_date})</span>
              </span>
            )}
            <span className="text-mist">
              Last volume: <span className="font-semibold text-cloud">{latest ? latest.volume_kg.toLocaleString() : "—"} kg</span>
            </span>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-1 text-xs font-medium text-mist">Top weight & est. 1RM per session</p>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={chartRows}>
                  <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" {...AXES} />
                  <YAxis yAxisId="w" domain={["dataMin - 2.5", "dataMax + 2.5"]} {...AXES} width={40} />
                  <YAxis yAxisId="e" orientation="right" domain={["dataMin - 2.5", "dataMax + 2.5"]} {...AXES} width={40} />
                  <Tooltip {...TOOLTIP} />
                  <Line yAxisId="w" dataKey="Top weight" stroke="#a3e635" strokeWidth={2} dot={{ r: 3 }} unit=" kg" />
                  <Line yAxisId="e" dataKey="e1RM" stroke="#fbbf24" strokeWidth={1.5} dot={false} strokeDasharray="4 4" unit=" kg" />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-mist">Weekly volume & sets (last 8 weeks)</p>
              {weeklyRows.length ? (
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={weeklyRows}>
                    <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="week" {...AXES} />
                    <YAxis yAxisId="v" {...AXES} width={52} />
                    <YAxis yAxisId="s" orientation="right" {...AXES} width={28} />
                    <Tooltip {...TOOLTIP} cursor={{ fill: "rgba(163,230,53,0.05)" }} />
                    <Bar yAxisId="v" dataKey="Volume" fill="#22d3ee" radius={[4, 4, 0, 0]} />
                    <Bar yAxisId="s" dataKey="Sets" fill="#a3e635" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <Empty>No weekly data yet.</Empty>
              )}
            </div>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-edge text-left text-xs font-medium text-mist">
                  <th className="py-1.5 pr-3">Date</th>
                  <th className="py-1.5 pr-3">Session</th>
                  <th className="py-1.5 pr-3">Sets</th>
                  <th className="py-1.5 pr-3 text-right">Top</th>
                  <th className="py-1.5 pr-3 text-right">e1RM</th>
                  <th className="py-1.5 text-right">Volume</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-edge">
                {[...rows].reverse().slice(0, 8).map((r, i) => (
                  <tr key={`${r.date}-${i}`} className="text-cloud">
                    <td className="py-1.5 pr-3">{r.date}</td>
                    <td className="py-1.5 pr-3">{r.session_name}</td>
                    <td className="py-1.5 pr-3 font-mono text-xs text-mist">
                      {r.sets
                        .map((s) => `${s.reps ?? "?"}${s.weight_kg != null ? `@${s.weight_kg}` : ""}${s.rir != null ? ` rir${s.rir}` : s.rpe != null ? ` rpe${s.rpe}` : ""}`)
                        .join(" ")}
                    </td>
                    <td className="py-1.5 pr-3 text-right font-medium">{r.best_weight_kg ?? "—"}</td>
                    <td className="py-1.5 pr-3 text-right text-yellow-300/90">{r.est_1rm_kg ?? "—"}</td>
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

function formatSets(sets: { reps?: number | null; weight_kg?: number | null; rpe?: number | null; rir?: number | null }[]): string {
  return sets
    .map((s) => {
      const parts = [s.reps != null ? `${s.reps} reps` : "? reps"];
      if (s.weight_kg != null) parts.push(`@ ${s.weight_kg} kg`);
      if (s.rpe != null) parts.push(`rpe ${s.rpe}`);
      if (s.rir != null) parts.push(`${s.rir} RIR`);
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

/* ---- structured workout logger ---- */

type SetDraft = { reps: string; weight_kg: string; rir: string };
type ExerciseDraft = { name: string; sets: SetDraft[] };

const EMPTY_SET: SetDraft = { reps: "", weight_kg: "", rir: "" };
const emptyExercise = (): ExerciseDraft => ({ name: "", sets: [{ ...EMPTY_SET }] });

function LogWorkoutCard({
  userId,
  names,
  onLogged,
}: {
  userId: string;
  names: string[];
  onLogged: () => void;
}) {
  const templates = useFetch(() => api.workoutTemplates(userId), [userId]);
  const [date, setDate] = useState(todayISO());
  const [sessionName, setSessionName] = useState("");
  const [duration, setDuration] = useState("");
  const [notes, setNotes] = useState("");
  const [exercises, setExercises] = useState<ExerciseDraft[]>([emptyExercise()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const patchExercise = (i: number, patch: Partial<ExerciseDraft>) =>
    setExercises((list) => list.map((ex, j) => (j === i ? { ...ex, ...patch } : ex)));

  const patchSet = (i: number, j: number, patch: Partial<SetDraft>) =>
    setExercises((list) =>
      list.map((ex, ei) =>
        ei === i ? { ...ex, sets: ex.sets.map((s, sj) => (sj === j ? { ...s, ...patch } : s)) } : ex
      )
    );

  const addSet = (i: number) =>
    setExercises((list) =>
      list.map((ex, ei) => {
        if (ei !== i) return ex;
        const last = ex.sets[ex.sets.length - 1] ?? EMPTY_SET;
        return { ...ex, sets: [...ex.sets, { ...last }] }; // copy the previous set: progressive overload is one edit away
      })
    );

  // Template → structured rows; weights come from the last logged session when available.
  const loadTemplate = async (t: WorkoutTemplate) => {
    const drafts = await Promise.all(
      t.exercises.map(async (e) => {
        let weight: number | null = e.weight_kg ?? null;
        try {
          const hist = await api.exerciseHistory(userId, e.name, 90);
          const last = hist[hist.length - 1];
          if (last?.best_weight_kg != null) weight = last.best_weight_kg;
        } catch {
          /* history lookup is best effort */
        }
        const range = e.reps.match(/(\d+)\s*[-–]\s*(\d+)/);
        const reps = range ? range[2] : (e.reps.match(/\d+/)?.[0] ?? "");
        const rpe = e.rpe_target ? parseFloat(e.rpe_target.split("-").pop() ?? "") : NaN;
        const rir = Number.isFinite(rpe) && rpe > 0 ? String(Math.max(0, Math.round(10 - rpe))) : "";
        const sets: SetDraft[] = Array.from({ length: Math.max(1, e.sets) }, () => ({
          reps,
          weight_kg: weight != null ? String(weight) : "",
          rir,
        }));
        return { name: e.name, sets };
      })
    );
    setExercises(drafts.length ? drafts : [emptyExercise()]);
    setSessionName((current) => current || t.name);
    setError("");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const clean = exercises
      .filter((ex) => ex.name.trim())
      .map((ex) => ({
        name: ex.name.trim().slice(0, 120),
        sets: ex.sets
          .map((s) => ({
            ...(s.reps !== "" ? { reps: Number(s.reps) } : {}),
            ...(s.weight_kg !== "" ? { weight_kg: Number(s.weight_kg) } : {}),
            ...(s.rir !== "" ? { rir: Number(s.rir) } : {}),
          }))
          .filter((s) => Object.keys(s).length > 0),
      }))
      .filter((ex) => ex.sets.length > 0);
    if (!sessionName.trim()) {
      setError("Give the session a name (e.g. Push A).");
      return;
    }
    if (!clean.length) {
      setError("Add at least one exercise with a name and one filled set.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.logWorkout(userId, {
        date,
        session_name: sessionName.trim(),
        exercises: clean,
        duration_min: duration ? Number(duration) : null,
        notes: notes || null,
      });
      setSessionName("");
      setDuration("");
      setNotes("");
      setExercises([emptyExercise()]);
      onLogged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Log a workout">
      {(templates.data?.length ?? 0) > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-mist">Start from template:</span>
          {templates.data!.map((t) => (
            <Button key={t.id} onClick={() => loadTemplate(t)}>
              {t.name}
            </Button>
          ))}
        </div>
      )}
      <datalist id="exercise-names">
        {names.map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[150px_1fr_110px]">
          <Input type="date" value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} />
          <Input required placeholder="Session name (Push A)" value={sessionName} onChange={(e) => setSessionName(e.target.value)} />
          <Input type="number" min={0} max={600} placeholder="Minutes" value={duration} onChange={(e) => setDuration(e.target.value)} />
        </div>

        <div className="space-y-3">
          {exercises.map((ex, i) => (
            <div key={i} className="space-y-2 rounded-xl border border-edge bg-night-3 p-3">
              <div className="flex items-center gap-2">
                <Input
                  list="exercise-names"
                  placeholder="Exercise (Bench press)"
                  value={ex.name}
                  onChange={(e) => patchExercise(i, { name: e.target.value })}
                  className="flex-1"
                />
                <button
                  type="button"
                  className="shrink-0 text-ash hover:text-ember disabled:opacity-30"
                  disabled={exercises.length === 1}
                  onClick={() => setExercises(exercises.filter((_, j) => j !== i))}
                  title="Remove exercise"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              <div className="space-y-1.5">
                {ex.sets.map((s, j) => (
                  <div key={j} className="flex items-center gap-2">
                    <span className="w-9 shrink-0 text-xs tabular-nums text-ash">Set {j + 1}</span>
                    <Input
                      type="number" min={0} max={200} placeholder="reps" title="Reps"
                      value={s.reps} onChange={(e) => patchSet(i, j, { reps: e.target.value })}
                      className="w-20!"
                    />
                    <span className="text-xs text-ash">×</span>
                    <Input
                      type="number" step="0.5" min={0} max={600} placeholder="kg" title="Weight in kg"
                      value={s.weight_kg} onChange={(e) => patchSet(i, j, { weight_kg: e.target.value })}
                      className="w-24!"
                    />
                    <Input
                      type="number" min={0} max={10} placeholder="RIR" title="Reps in reserve (optional): 2 = could have done 2 more"
                      value={s.rir} onChange={(e) => patchSet(i, j, { rir: e.target.value })}
                      className="w-16!"
                    />
                    <button
                      type="button"
                      className="text-ash hover:text-ember disabled:opacity-30"
                      disabled={ex.sets.length === 1}
                      onClick={() => patchExercise(i, { sets: ex.sets.filter((_, k) => k !== j) })}
                      title="Remove set"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
              <Button type="button" variant="ghost" onClick={() => addSet(i)} className="px-2 py-1 text-xs">
                + Add set (copies previous)
              </Button>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="secondary" onClick={() => setExercises([...exercises, emptyExercise()])}>
            + Add exercise
          </Button>
          <span className="text-xs text-ash">RIR = reps in reserve (optional)</span>
          <Button type="submit" variant="primary" disabled={busy} className="ml-auto">
            {busy ? "Saving..." : "Save workout"}
          </Button>
        </div>
        <Input placeholder="Notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
        {error && <p className="text-sm text-ember">{error}</p>}
      </form>
    </Card>
  );
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
        <form onSubmit={save} className="mb-4 space-y-3 rounded-xl border border-edge p-3">
          <Input placeholder="Template name (Push Day)" value={name} onChange={(e) => setName(e.target.value)} />
          <Textarea
            rows={4}
            placeholder={"One exercise per line:\nBench press: 4x6-8 @80 rpe7-8\nIncline dumbbell press: 3x8-10"}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {error && <p className="text-sm text-ember">{error}</p>}
          <Button type="submit" variant="primary">
            Save template
          </Button>
        </form>
      )}
      {!templates.data?.length ? (
        <Empty>
          No templates yet. "Load starter templates" adds Push/Pull/Legs; or create your own. The coach
          on Telegram knows them too — ask "what am I doing on push day?".
        </Empty>
      ) : (
        <ul className="space-y-2">
          {templates.data.map((t) => (
            <li key={t.id} className="flex items-start justify-between gap-2 rounded-lg bg-night-3 p-3">
              <div className="min-w-0">
                <span className="text-sm font-semibold text-cloud">{t.name}</span>
                <p className="mt-0.5 text-xs text-mist">
                  {t.exercises.map((e) => `${e.name} ${e.sets}x${e.reps}`).join(" · ")}
                </p>
              </div>
              <button
                className="shrink-0 text-ash hover:text-ember"
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
    <div className="rounded-xl border border-edge bg-night-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Dumbbell className="h-4 w-4 text-lime" />
          <span className="text-sm font-semibold text-cloud">{workout.session_name}</span>
          {workout.duration_min != null && <Badge>{workout.duration_min} min</Badge>}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-mist">{workout.date}</span>
          <button onClick={remove} className="text-ash transition-colors hover:text-ember">
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>
      <ul className="mt-3 space-y-1.5">
        {workout.exercises.map((exercise, index) => (
          <li key={index} className="flex justify-between gap-4 text-sm">
            <span className="text-cloud">{exercise.name}</span>
            <span className="shrink-0 text-mist">{formatSets(exercise.sets)}</span>
          </li>
        ))}
      </ul>
      {workout.notes && <p className="mt-2 text-xs italic text-mist">{workout.notes}</p>}
    </div>
  );
}

export default function Workouts({ userId }: { userId: string }) {
  const workouts = useFetch(() => api.workouts(userId, 30), [userId]);
  const session = useFetch(() => api.todaySession(userId), [userId]);
  const names = useFetch(() => api.exerciseNames(userId, 365), [userId]);

  if (workouts.loading) return <Spinner />;

  return (
    <div>
      <PageHeader title="Workouts" subtitle="Log sessions here or just text them to the coach on Telegram." />

      {session.data && (
        <Card title="Today's planned session" className="mb-4">
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-cloud">{session.data.text}</pre>
        </Card>
      )}

      <div className="mb-4">
        <Progression userId={userId} />
      </div>

      <div className="mb-4">
        <TemplatesCard userId={userId} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <LogWorkoutCard
          userId={userId}
          names={names.data ?? []}
          onLogged={() => {
            workouts.reload();
            session.reload();
          }}
        />

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
