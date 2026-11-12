import { FormEvent, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BookmarkPlus, Trash2, Zap } from "lucide-react";
import { api, MealTemplate, MealTemplateItem } from "../api";
import { useFetch, todayISO } from "../hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Spinner, Stat } from "../components/ui";

const AXES = { stroke: "#52525b", fontSize: 11, tickLine: false, axisLine: false } as const;
const TOOLTIP = {
  contentStyle: { background: "#18181b", border: "1px solid #3f3f46", borderRadius: 12, fontSize: 12 },
  labelStyle: { color: "#a1a1aa" },
} as const;

const EMPTY_ITEM = { description: "", servings: 1, kcal: "", protein_g: "", carbs_g: "", fat_g: "", fiber_g: "" };
type ItemForm = typeof EMPTY_ITEM;

function TemplateEditor({
  userId,
  existing,
  onSaved,
}: {
  userId: string;
  existing: MealTemplate | null;
  onSaved: () => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [meal, setMeal] = useState(existing?.meal ?? "breakfast");
  const [items, setItems] = useState<ItemForm[]>(
    existing
      ? existing.items.map((i) => ({
          description: i.description,
          servings: i.servings,
          kcal: String(i.kcal),
          protein_g: i.protein_g != null ? String(i.protein_g) : "",
          carbs_g: i.carbs_g != null ? String(i.carbs_g) : "",
          fat_g: i.fat_g != null ? String(i.fat_g) : "",
          fiber_g: i.fiber_g != null ? String(i.fiber_g) : "",
        }))
      : [{ ...EMPTY_ITEM }]
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const total = items.reduce((sum, i) => sum + (Number(i.kcal) || 0) * (Number(i.servings) || 1), 0);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payloadItems: MealTemplateItem[] = items.map((i) => ({
        description: i.description,
        servings: Number(i.servings) || 1,
        kcal: Number(i.kcal),
        protein_g: i.protein_g ? Number(i.protein_g) : null,
        carbs_g: i.carbs_g ? Number(i.carbs_g) : null,
        fat_g: i.fat_g ? Number(i.fat_g) : null,
        fiber_g: i.fiber_g ? Number(i.fiber_g) : null,
      }));
      await api.saveMealTemplate(userId, { name, meal, items: payloadItems });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const set = (index: number, patch: Partial<ItemForm>) =>
    setItems(items.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  return (
    <form onSubmit={save} className="space-y-3 border-t border-zinc-800 pt-4">
      <div className="grid gap-3 sm:grid-cols-[1fr_160px]">
        <Input required placeholder="Template name (Oat breakfast)" value={name} onChange={(e) => setName(e.target.value)} />
        <Select value={meal} onChange={(e) => setMeal(e.target.value)}>
          {["breakfast", "lunch", "dinner", "snack"].map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
      </div>
      {items.map((item, index) => (
        <div key={index} className="grid grid-cols-2 gap-2 rounded-lg bg-zinc-900 p-2 sm:grid-cols-12">
          <Input
            required
            className="col-span-2 sm:col-span-4"
            placeholder="50 g oats"
            value={item.description}
            onChange={(e) => set(index, { description: e.target.value })}
          />
          <Input className="sm:col-span-1" type="number" step="0.5" min={0.25} title="Servings" value={item.servings} onChange={(e) => set(index, { servings: Number(e.target.value) })} />
          <Input className="sm:col-span-2" type="number" min={0} required placeholder="kcal*" value={item.kcal} onChange={(e) => set(index, { kcal: e.target.value })} />
          <Input className="sm:col-span-1" type="number" step="0.1" min={0} placeholder="P" value={item.protein_g} onChange={(e) => set(index, { protein_g: e.target.value })} />
          <Input className="sm:col-span-1" type="number" step="0.1" min={0} placeholder="C" value={item.carbs_g} onChange={(e) => set(index, { carbs_g: e.target.value })} />
          <Input className="sm:col-span-1" type="number" step="0.1" min={0} placeholder="F" value={item.fat_g} onChange={(e) => set(index, { fat_g: e.target.value })} />
          <Input className="sm:col-span-1" type="number" step="0.1" min={0} placeholder="Lif" value={item.fiber_g} onChange={(e) => set(index, { fiber_g: e.target.value })} />
          <button
            type="button"
            className="text-zinc-600 hover:text-red-400 sm:col-span-1"
            onClick={() => setItems(items.filter((_, i) => i !== index))}
            disabled={items.length === 1}
          >
            <Trash2 className="mx-auto h-4 w-4" />
          </button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={() => setItems([...items, { ...EMPTY_ITEM }])}>
          + Add item
        </Button>
        <span className="text-sm text-zinc-400">
          Total: <span className="font-semibold text-lime-400">{total} kcal</span>
        </span>
        <Button type="submit" variant="primary" disabled={busy} className="ml-auto">
          {busy ? "Saving..." : existing ? "Update template" : "Save template"}
        </Button>
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
    </form>
  );
}

function MealTemplates({ userId }: { userId: string }) {
  const templates = useFetch(() => api.mealTemplates(userId), [userId]);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [factors, setFactors] = useState<Record<number, string>>({});
  const [message, setMessage] = useState("");

  const log = async (template: MealTemplate) => {
    const factor = Number(factors[template.id] || "1") || 1;
    setMessage("");
    try {
      const meal = await api.logFromTemplate(userId, template.name, factor);
      setMessage(`Logged "${meal.description}" — ${meal.kcal} kcal`);
      window.dispatchEvent(new Event("meals-changed"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Log failed");
    }
  };

  return (
    <Card
      title="Meal templates"
      action={
        <Button onClick={() => setEditing(editing === "new" ? null : "new")}>
          <BookmarkPlus className="mr-1 inline h-3.5 w-3.5" /> New template
        </Button>
      }
    >
      {message && <p className="mb-3 text-sm text-lime-400">{message}</p>}
      {templates.loading ? (
        <Spinner label="" />
      ) : !templates.data?.length ? (
        <Empty>
          No templates yet. Save your usual breakfast once — logging it later takes one click (or one
          message to the coach: "her zamanki kahvaltıdan").
        </Empty>
      ) : (
        <ul className="space-y-2">
          {templates.data.map((template) => (
            <li key={template.id} className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Zap className="h-4 w-4 text-lime-400" />
                <span className="text-sm font-semibold text-zinc-100">{template.name}</span>
                <Badge>{template.meal}</Badge>
                <span className="text-sm font-semibold text-lime-400">{template.total_kcal} kcal</span>
                <span className="text-xs text-zinc-500">{template.items.length} items</span>
                <div className="ml-auto flex items-center gap-2">
                  <input
                    type="number"
                    step="0.25"
                    min="0.25"
                    max="10"
                    className="w-16 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200"
                    value={factors[template.id] ?? "1"}
                    onChange={(e) => setFactors({ ...factors, [template.id]: e.target.value })}
                    title="Portion factor"
                  />
                  <Button variant="primary" onClick={() => log(template)}>
                    Log
                  </Button>
                  <Button variant="ghost" onClick={() => setEditing(editing === template.id ? null : template.id)}>
                    Edit
                  </Button>
                  <button
                    className="text-zinc-600 hover:text-red-400"
                    onClick={async () => {
                      await api.deleteMealTemplate(userId, template.name);
                      templates.reload();
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <p className="mt-1 text-xs text-zinc-500">
                {template.items.map((i) => `${i.description} (${Math.round(i.kcal * i.servings)} kcal)`).join(" · ")}
              </p>
              {editing === template.id && (
                <TemplateEditor
                  userId={userId}
                  existing={template}
                  onSaved={() => {
                    setEditing(null);
                    templates.reload();
                  }}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      {editing === "new" && (
        <TemplateEditor
          userId={userId}
          existing={null}
          onSaved={() => {
            setEditing(null);
            templates.reload();
          }}
        />
      )}
    </Card>
  );
}

export default function Nutrition({ userId }: { userId: string }) {
  const [day, setDay] = useState(todayISO());
  const meals = useFetch(() => api.meals(userId, day), [userId, day]);
  const intake = useFetch(() => api.intake(userId, 14), [userId]);
  const programs = useFetch(() => api.programs(userId, "active"), [userId]);

  const [form, setForm] = useState({ meal: "breakfast", description: "", kcal: "", protein_g: "", carbs_g: "", fat_g: "", fiber_g: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [planBrief, setPlanBrief] = useState("");
  const [planBusy, setPlanBusy] = useState(false);
  const [planError, setPlanError] = useState("");

  // Template "Log" buttons notify us so the day's list refreshes.
  useMemo(() => {
    const handler = () => {
      meals.reload();
      intake.reload();
    };
    window.addEventListener("meals-changed", handler);
    return () => window.removeEventListener("meals-changed", handler);
  }, [meals, intake]);

  const nutritionProgram = (programs.data ?? []).find((p) => p.kind === "nutrition");
  const target = typeof nutritionProgram?.payload?.target_kcal === "number" ? nutritionProgram.payload.target_kcal : null;

  const intakeRows = useMemo(() => (intake.data ?? []).map((d) => ({ date: d.date.slice(5), kcal: d.kcal })), [intake.data]);

  const sum = (key: "kcal" | "protein_g" | "carbs_g" | "fat_g" | "fiber_g") =>
    meals.data?.reduce((total, m) => total + (m[key] ?? 0), 0) ?? 0;
  const dayTotal = sum("kcal");
  const dayProtein = sum("protein_g");
  const dayCarbs = sum("carbs_g");
  const dayFat = sum("fat_g");
  const dayFiber = sum("fiber_g");

  const generatePlan = async () => {
    setPlanBusy(true);
    setPlanError("");
    try {
      const program = await api.generateProgram(userId, "nutrition", planBrief || undefined);
      await api.activateProgram(userId, program.id);
      setPlanBrief("");
      programs.reload();
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setPlanBusy(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.logMeal(userId, {
        date: day,
        meal: form.meal,
        description: form.description,
        kcal: Number(form.kcal),
        protein_g: form.protein_g ? Number(form.protein_g) : null,
        carbs_g: form.carbs_g ? Number(form.carbs_g) : null,
        fat_g: form.fat_g ? Number(form.fat_g) : null,
        fiber_g: form.fiber_g ? Number(form.fiber_g) : null,
      });
      setForm({ meal: form.meal, description: "", kcal: "", protein_g: "", carbs_g: "", fat_g: "", fiber_g: "" });
      meals.reload();
      intake.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (mealId: number) => {
    await api.deleteMeal(userId, mealId);
    meals.reload();
    intake.reload();
  };

  return (
    <div>
      <PageHeader title="Nutrition" subtitle="Daily totals, meals, templates and your nutrition plan." />

      {/* Daily totals for the selected day, with remaining budget when a plan target exists */}
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat
          label={day === todayISO() ? "Calories today" : `Calories ${day.slice(5)}`}
          value={dayTotal.toLocaleString()}
          unit="kcal"
          delta={target ? `${Math.max(target - dayTotal, 0).toLocaleString()} kcal remaining of ${target.toLocaleString()}` : undefined}
          deltaGood={null}
        />
        <Stat label="Protein" value={dayProtein.toFixed(0)} unit="g" delta={nutritionProgram ? `target ${nutritionProgram.payload.protein_g ?? "—"} g` : undefined} deltaGood={null} />
        <Stat label="Carbs" value={dayCarbs.toFixed(0)} unit="g" delta={nutritionProgram ? `target ${nutritionProgram.payload.carbs_g ?? "—"} g` : undefined} deltaGood={null} />
        <Stat label="Fat" value={dayFat.toFixed(0)} unit="g" delta={nutritionProgram ? `target ${nutritionProgram.payload.fat_g ?? "—"} g` : undefined} deltaGood={null} />
        <Stat label="Fiber" value={dayFiber.toFixed(0)} unit="g" delta="target 25-38 g" deltaGood={dayFiber >= 25} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card
            title="Meals"
            action={
              <div className="flex items-center gap-3">
                <span className="text-sm font-semibold text-lime-400">
                  {dayTotal.toLocaleString()} kcal{dayFiber > 0 && <span className="text-zinc-400"> · {dayFiber.toFixed(0)} g fiber</span>}
                </span>
                <Input type="date" value={day} max={todayISO()} onChange={(e) => setDay(e.target.value)} className="w-auto!" />
              </div>
            }
          >
            {meals.loading ? (
              <Spinner label="" />
            ) : meals.data && meals.data.length > 0 ? (
              <ul className="divide-y divide-zinc-800">
                {meals.data.map((m) => (
                  <li key={m.id} className="flex items-center justify-between gap-2 py-2.5">
                    <div className="min-w-0">
                      <span className="text-sm font-medium text-zinc-200">{m.description}</span>
                      <div className="mt-0.5 flex items-center gap-2 text-xs text-zinc-500">
                        <Badge>{m.meal}</Badge>
                        <Badge tone={m.source === "assistant" ? "amber" : m.source === "template" ? "lime" : "zinc"}>{m.source}</Badge>
                        {m.protein_g != null && <span>P {m.protein_g}g</span>}
                        {m.carbs_g != null && <span>C {m.carbs_g}g</span>}
                        {m.fat_g != null && <span>F {m.fat_g}g</span>}
                        {m.fiber_g != null && <span>Lif {m.fiber_g}g</span>}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <span className="text-sm font-semibold text-zinc-100">{m.kcal}</span>
                      <button onClick={() => remove(m.id)} className="text-zinc-600 transition-colors hover:text-red-400">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No meals logged for {day}.</Empty>
            )}
          </Card>

          <Card title="Add meal">
            <form onSubmit={submit} className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-[140px_1fr_110px]">
                <Select value={form.meal} onChange={(e) => setForm({ ...form, meal: e.target.value })}>
                  {["breakfast", "lunch", "dinner", "snack"].map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </Select>
                <Input
                  required
                  placeholder="What did you eat? (2 eggs + 1 simit)"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                />
                <Input
                  required
                  type="number"
                  min={0}
                  max={20000}
                  placeholder="kcal"
                  value={form.kcal}
                  onChange={(e) => setForm({ ...form, kcal: e.target.value })}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-5">
                <Input type="number" min={0} step="0.1" placeholder="Protein (g)" value={form.protein_g} onChange={(e) => setForm({ ...form, protein_g: e.target.value })} />
                <Input type="number" min={0} step="0.1" placeholder="Carbs (g)" value={form.carbs_g} onChange={(e) => setForm({ ...form, carbs_g: e.target.value })} />
                <Input type="number" min={0} step="0.1" placeholder="Fat (g)" value={form.fat_g} onChange={(e) => setForm({ ...form, fat_g: e.target.value })} />
                <Input type="number" min={0} step="0.1" placeholder="Fiber (g)" value={form.fiber_g} onChange={(e) => setForm({ ...form, fiber_g: e.target.value })} />
                <Button type="submit" variant="primary" disabled={busy}>
                  {busy ? "Saving..." : "Log meal"}
                </Button>
              </div>
              {error && <p className="text-sm text-red-400">{error}</p>}
            </form>
          </Card>

          <MealTemplates userId={userId} />
        </div>

        <div className="space-y-4">
          <Card title="Intake (14 days)">
            {intakeRows.length ? (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={intakeRows}>
                  <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" {...AXES} />
                  <YAxis {...AXES} width={44} />
                  <Tooltip {...TOOLTIP} cursor={{ fill: "#27272a55" }} />
                  <Bar dataKey="kcal" fill="#a3e635" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <Empty>No intake yet.</Empty>
            )}
          </Card>

          <Card title="Nutrition plan">
            {nutritionProgram ? (
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-zinc-100">{nutritionProgram.name}</span>
                  <Badge tone="lime">v{nutritionProgram.version} · active</Badge>
                </div>
                <p className="mt-2 text-sm text-zinc-400">{nutritionProgram.summary}</p>
                <div className="mt-3 grid grid-cols-4 gap-2 text-center">
                  {([
                    ["kcal", target],
                    ["Protein", nutritionProgram.payload.protein_g],
                    ["Carbs", nutritionProgram.payload.carbs_g],
                    ["Fat", nutritionProgram.payload.fat_g],
                  ] as [string, unknown][]).map(([label, value]) => (
                    <div key={label} className="rounded-lg bg-zinc-800/60 p-2">
                      <div className="text-sm font-bold text-zinc-100">{typeof value === "number" ? value : "—"}</div>
                      <div className="text-[10px] uppercase text-zinc-500">{label}</div>
                    </div>
                  ))}
                </div>
                {Array.isArray(nutritionProgram.payload.meals) && (nutritionProgram.payload.meals as { name?: string; items?: string; kcal?: number }[]).length > 0 && (
                  <ul className="mt-3 space-y-1.5">
                    {(nutritionProgram.payload.meals as { name?: string; items?: string; kcal?: number }[]).map((m, i) => (
                      <li key={i} className="flex justify-between gap-3 text-sm">
                        <span className="text-zinc-300">
                          <span className="font-medium">{m.name}</span>
                          {m.items && <span className="text-zinc-500"> — {m.items}</span>}
                        </span>
                        <span className="shrink-0 text-zinc-400">{m.kcal} kcal</span>
                      </li>
                    ))}
                  </ul>
                )}
                {Array.isArray(nutritionProgram.payload.guidelines) && (nutritionProgram.payload.guidelines as string[]).length > 0 && (
                  <ul className="mt-3 list-inside list-disc space-y-1 text-xs text-zinc-500">
                    {(nutritionProgram.payload.guidelines as string[]).map((g, i) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                )}
              </div>
            ) : (
              <Empty>No active nutrition plan. Generate one below — the coach designs daily calorie & macro targets and meal ideas from your data.</Empty>
            )}
            <div className="mt-4 border-t border-zinc-800 pt-4">
              <div className="flex gap-2">
                <Input
                  placeholder="Brief (optional): fat loss, lunch at work, no dairy..."
                  value={planBrief}
                  onChange={(e) => setPlanBrief(e.target.value)}
                />
                <Button variant="primary" onClick={generatePlan} disabled={planBusy} className="shrink-0">
                  {planBusy ? "Designing..." : nutritionProgram ? "Regenerate" : "Generate plan"}
                </Button>
              </div>
              {planError && <p className="mt-2 text-sm text-red-400">{planError}</p>}
              <p className="mt-2 text-xs text-zinc-600">Uses the plan model (~30 s). New plans activate immediately; old versions stay in history.</p>
            </div>
          </Card>
        </div>
      </div>
      {programs.error && <ErrorNote error={programs.error} />}
    </div>
  );
}
