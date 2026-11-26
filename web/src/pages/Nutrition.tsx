import { FormEvent, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BookmarkPlus, ChevronLeft, ChevronRight, MessageCircle, Plus, Trash2, X, Zap } from "lucide-react";
import { api, Meal, MealTemplate, MealTemplateItem, ResolvedItem } from "../api";
import { useFetch, todayISO } from "../hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Spinner, Stat } from "../components/ui";

const AXES = { stroke: "#6b7580", fontSize: 11, tickLine: false, axisLine: false } as const;
const TOOLTIP = {
  contentStyle: { background: "#161c24", border: "1px solid #2c3540", borderRadius: 12, fontSize: 12 },
  labelStyle: { color: "#a7b1bd" },
} as const;

const EMPTY_ITEM = { name: "", grams: "100", kcal: "", protein_g: "", carbs_g: "", fat_g: "", fiber_g: "" };
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
      ? existing.items.map((i) => {
          const m = i.description.match(GRAMS_RE);
          return {
            name: m ? m[2].trim() : i.description,
            grams: m ? m[1].replace(",", ".") : String(100 * i.servings),
            kcal: String(Math.round(i.kcal * i.servings)),
            protein_g: i.protein_g != null ? String(i.protein_g) : "",
            carbs_g: i.carbs_g != null ? String(i.carbs_g) : "",
            fat_g: i.fat_g != null ? String(i.fat_g) : "",
            fiber_g: i.fiber_g != null ? String(i.fiber_g) : "",
          };
        })
      : [{ ...EMPTY_ITEM }]
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const total = items.reduce((sum, i) => sum + (Number(i.kcal) || 0), 0);

  const set = (index: number, patch: Partial<ItemForm>) =>
    setItems(items.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  const computeAll = async () => {
    const clean = items
      .map((it, i) => ({ i, name: it.name.trim(), grams: Number(it.grams) }))
      .filter((x) => x.name && x.grams > 0);
    if (!clean.length) {
      setError("Add at least one food with a name and grams.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await api.computeMeal(userId, clean.map((x) => ({ name: x.name, grams: x.grams })));
      setItems((rows) =>
        rows.map((it, idx) => {
          const pos = clean.findIndex((c) => c.i === idx);
          if (pos < 0) return it;
          const r = res.items[pos];
          return {
            ...it,
            kcal: String(Math.round(r.kcal)),
            protein_g: r.protein_g == null ? "" : String(r.protein_g),
            carbs_g: r.carbs_g == null ? "" : String(r.carbs_g),
            fat_g: r.fat_g == null ? "" : String(r.fat_g),
            fiber_g: r.fiber_g == null ? "" : String(r.fiber_g),
          };
        })
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not compute nutrition");
    } finally {
      setBusy(false);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (items.some((i) => i.name.trim() && !i.kcal)) {
      setError("Run \"Compute nutrition\" (or type kcal yourself) before saving.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payloadItems: MealTemplateItem[] = items
        .filter((i) => i.name.trim())
        .map((i) => ({
          description: `${Number(i.grams) || 100} g ${i.name.trim()}`,
          servings: 1,
          kcal: Number(i.kcal),
          protein_g: i.protein_g ? Number(i.protein_g) : null,
          carbs_g: i.carbs_g ? Number(i.carbs_g) : null,
          fat_g: i.fat_g ? Number(i.fat_g) : null,
          fiber_g: i.fiber_g ? Number(i.fiber_g) : null,
        }));
      if (!payloadItems.length) {
        setError("Add at least one food.");
        setBusy(false);
        return;
      }
      await api.saveMealTemplate(userId, { name, meal, items: payloadItems });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="space-y-3 border-t border-edge pt-4">
      <div className="grid gap-3 sm:grid-cols-[1fr_160px]">
        <Input required placeholder="Template name (Oats breakfast)" value={name} onChange={(e) => setName(e.target.value)} />
        <Select value={meal} onChange={(e) => setMeal(e.target.value)}>
          {["breakfast", "lunch", "dinner", "snack"].map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
      </div>
      {items.map((item, index) => (
        <div key={index} className="grid grid-cols-2 gap-2 rounded-lg bg-night-2 p-2 sm:grid-cols-12">
          <Input
            required
            className="col-span-2 sm:col-span-3"
            placeholder="Food name (yulaf)"
            value={item.name}
            onChange={(e) => set(index, { name: e.target.value })}
          />
          <Input className="sm:col-span-1" type="number" min={1} max={2000} title="Grams" value={item.grams} onChange={(e) => set(index, { grams: e.target.value })} />
          <Input className="sm:col-span-2" type="number" min={0} placeholder="kcal" title="kcal — computed or typed" value={item.kcal} onChange={(e) => set(index, { kcal: e.target.value })} />
          <Input className="sm:col-span-1" type="number" step="0.1" min={0} placeholder="P" value={item.protein_g} onChange={(e) => set(index, { protein_g: e.target.value })} />
          <Input className="sm:col-span-1" type="number" step="0.1" min={0} placeholder="C" value={item.carbs_g} onChange={(e) => set(index, { carbs_g: e.target.value })} />
          <Input className="sm:col-span-1" type="number" step="0.1" min={0} placeholder="F" value={item.fat_g} onChange={(e) => set(index, { fat_g: e.target.value })} />
          <Input className="sm:col-span-1" type="number" step="0.1" min={0} placeholder="Fiber" value={item.fiber_g} onChange={(e) => set(index, { fiber_g: e.target.value })} />
          <button
            type="button"
            className="text-ash hover:text-ember sm:col-span-1"
            onClick={() => setItems(items.filter((_, i) => i !== index))}
            disabled={items.length === 1}
          >
            <Trash2 className="mx-auto h-4 w-4" />
          </button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="secondary" onClick={() => setItems([...items, { ...EMPTY_ITEM }])}>
          <Plus className="mr-1 inline h-3.5 w-3.5" /> Add food
        </Button>
        <Button type="button" onClick={computeAll} disabled={busy}>
          {busy ? "Computing..." : "Compute nutrition"}
        </Button>
        <span className="text-sm text-mist">
          Total: <span className="font-semibold text-lime">{total} kcal</span>
        </span>
        <Button type="submit" variant="primary" disabled={busy} className="ml-auto">
          {busy ? "Saving..." : existing ? "Update template" : "Save template"}
        </Button>
      </div>
      <p className="text-xs text-ash">
        Type only names and grams — "Compute nutrition" fills estimated kcal and macros;
        correct any value you think is wrong before saving.
      </p>
      {error && <p className="text-sm text-ember">{error}</p>}
    </form>
  );
}

function MealTemplates({ userId, day }: { userId: string; day: string }) {
  const templates = useFetch(() => api.mealTemplates(userId), [userId]);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [factors, setFactors] = useState<Record<number, string>>({});
  const [message, setMessage] = useState("");

  const log = async (template: MealTemplate) => {
    const factor = Number(factors[template.id] || "1") || 1;
    setMessage("");
    try {
      const meal = await api.logFromTemplate(userId, template.name, factor, day);
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
      {message && <p className="mb-3 text-sm text-lime">{message}</p>}
      {templates.loading ? (
        <Spinner label="" />
      ) : !templates.data?.length ? (
        <Empty>
          No templates yet. Save your usual breakfast once — logging it later takes one click (or one
          message to the coach: "my usual breakfast").
        </Empty>
      ) : (
        <ul className="space-y-2">
          {templates.data.map((template) => (
            <li key={template.id} className="rounded-xl border border-edge bg-night-3 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Zap className="h-4 w-4 text-lime" />
                <span className="text-sm font-semibold text-cloud">{template.name}</span>
                <Badge>{template.meal}</Badge>
                <span className="text-sm font-semibold text-lime">{template.total_kcal} kcal</span>
                <span className="text-xs text-mist">{template.items.length} items</span>
                <div className="ml-auto flex items-center gap-2">
                  <input
                    type="number"
                    step="0.25"
                    min="0.25"
                    max="10"
                    className="w-16 rounded border border-edge bg-night-2 px-2 py-1 text-xs text-cloud"
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
                    className="text-ash hover:text-ember"
                    onClick={async () => {
                      await api.deleteMealTemplate(userId, template.name);
                      templates.reload();
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <p className="mt-1 text-xs text-mist">
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

const GRAMS_RE = /^\s*([\d.,]+)\s*(?:g|gr|gram)\b\.?\s+(.+)$/i;

function EditMealForm({ userId, meal, onSaved, onCancel }: {
  userId: string;
  meal: Meal;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [values, setValues] = useState({
    meal: meal.meal,
    description: meal.description,
    kcal: String(meal.kcal),
    protein_g: meal.protein_g == null ? "" : String(meal.protein_g),
    carbs_g: meal.carbs_g == null ? "" : String(meal.carbs_g),
    fat_g: meal.fat_g == null ? "" : String(meal.fat_g),
    fiber_g: meal.fiber_g == null ? "" : String(meal.fiber_g),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.updateMeal(userId, meal.id, {
        meal: values.meal,
        description: values.description.trim(),
        kcal: Number(values.kcal),
        protein_g: values.protein_g === "" ? null : Number(values.protein_g),
        carbs_g: values.carbs_g === "" ? null : Number(values.carbs_g),
        fat_g: values.fat_g === "" ? null : Number(values.fat_g),
        fiber_g: values.fiber_g === "" ? null : Number(values.fiber_g),
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update meal");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="mt-3 space-y-3 rounded-lg border border-edge bg-night-3 p-3">
      <div className="grid gap-2 sm:grid-cols-[140px_1fr_100px]">
        <Select value={values.meal} onChange={(e) => setValues({ ...values, meal: e.target.value })}>
          {["breakfast", "lunch", "dinner", "snack"].map((kind) => <option key={kind} value={kind}>{kind}</option>)}
        </Select>
        <Input required maxLength={200} value={values.description} onChange={(e) => setValues({ ...values, description: e.target.value })} aria-label="Meal description" />
        <Input required type="number" min={0} max={20000} value={values.kcal} onChange={(e) => setValues({ ...values, kcal: e.target.value })} aria-label="Calories (kcal)" />
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {([
          ["protein_g", "Protein (g)"],
          ["carbs_g", "Carbs (g)"],
          ["fat_g", "Fat (g)"],
          ["fiber_g", "Fiber (g)"],
        ] as const).map(([key, label]) => (
          <Input
            key={key}
            type="number"
            min={0}
            step="0.1"
            placeholder={label}
            aria-label={label}
            value={values[key]}
            onChange={(e) => setValues({ ...values, [key]: e.target.value })}
          />
        ))}
      </div>
      <div className="flex items-center justify-end gap-2">
        {error && <span className="mr-auto text-sm text-ember">{error}</span>}
        <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={busy}>{busy ? "Saving..." : "Save changes"}</Button>
      </div>
    </form>
  );
}

/* Name + grams per line; the backend estimates kcal/macros in one batch.
   Saved meal templates load into the same editable rows. */
function AddMealCard({ userId, day, onLogged }: { userId: string; day: string; onLogged: () => void }) {
  const [meal, setMeal] = useState("breakfast");
  const [items, setItems] = useState<{ name: string; grams: string }[]>([{ name: "", grams: "100" }]);
  const [rows, setRows] = useState<ResolvedItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [uncertainty, setUncertainty] = useState("");
  const templates = useFetch(() => api.mealTemplates(userId), [userId]);

  const patch = (index: number, values: Partial<{ name: string; grams: string }>) => {
    setItems((list) => list.map((r, i) => (i === index ? { ...r, ...values } : r)));
    setRows(null);
  };

  const loadTemplate = (t: MealTemplate) => {
    const loaded = t.items.map((it) => {
      const m = it.description.match(GRAMS_RE);
      return m ? { name: m[2].trim(), grams: m[1].replace(",", ".") } : { name: it.description, grams: "100" };
    });
    setItems(loaded.length ? loaded : [{ name: "", grams: "100" }]);
    // Templates already carry nutrition values — show them as pre-computed, editable rows.
    setRows(
      t.items.map((it) => {
        const m = it.description.match(GRAMS_RE);
        return {
          name: m ? m[2].trim() : it.description,
          grams: m ? Number(m[1].replace(",", ".")) : 100,
          kcal: it.kcal,
          protein_g: it.protein_g ?? null,
          carbs_g: it.carbs_g ?? null,
          fat_g: it.fat_g ?? null,
          fiber_g: it.fiber_g ?? null,
          source: "template" as const,
        };
      })
    );
    setError("");
  };

  const clean = items.filter((i) => i.name.trim() && Number(i.grams) > 0);

  const analyze = async () => {
    if (!photo) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(photo.type) || photo.size > 5 * 1024 * 1024) {
      setError("Choose a JPEG, PNG or WebP image up to 5 MB.");
      return;
    }
    setBusy(true);
    setError("");
    setUncertainty("");
    setRows(null);
    try {
      const result = await api.photoMeal(userId, photo);
      setUncertainty(result.uncertainty);
      if (!result.items.length) {
        setError("No recognizable food found. Add foods manually or try another photo.");
        return;
      }
      setItems(result.items.map((item) => ({ name: item.name, grams: String(item.grams) })));
      setRows(result.items.map((item) => ({ ...item, source: "photo" })));
      setPhoto(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Photo analysis failed");
    } finally {
      setBusy(false);
    }
  };

  const compute = async (): Promise<ResolvedItem[] | null> => {
    if (!clean.length) {
      setError("Add at least one food with a name and grams.");
      return null;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.computeMeal(
        userId,
        clean.map((i) => ({ name: i.name.trim(), grams: Number(i.grams) }))
      );
      setRows(result.items);
      return result.items;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not compute nutrition");
      return null;
    } finally {
      setBusy(false);
    }
  };

  const patchRow = (index: number, values: Partial<ResolvedItem>) =>
    setRows((list) => (list ? list.map((r, i) => (i === index ? { ...r, ...values } : r)) : list));

  const totalNutrient = (list: ResolvedItem[], key: "protein_g" | "carbs_g" | "fat_g" | "fiber_g") =>
    list.every((r) => r[key] != null) ? list.reduce((sum, r) => sum + (r[key] ?? 0), 0) : null;
  const totals = {
    kcal: (rows ?? []).reduce((sum, r) => sum + r.kcal, 0),
    protein_g: totalNutrient(rows ?? [], "protein_g"),
    carbs_g: totalNutrient(rows ?? [], "carbs_g"),
    fat_g: totalNutrient(rows ?? [], "fat_g"),
    fiber_g: totalNutrient(rows ?? [], "fiber_g"),
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const finalRows = rows;
    if (!finalRows) {
      setError("Compute nutrition and review the estimates before logging.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.logMeal(userId, {
        date: day,
        meal,
        description: finalRows.map((i) => `${i.grams} g ${i.name}`).join(", "),
        kcal: Math.round(finalRows.reduce((s, r) => s + r.kcal, 0)),
        protein_g: totalNutrient(finalRows, "protein_g"),
        carbs_g: totalNutrient(finalRows, "carbs_g"),
        fat_g: totalNutrient(finalRows, "fat_g"),
        fiber_g: totalNutrient(finalRows, "fiber_g"),
      });
      setRows(null);
      setItems([{ name: "", grams: "100" }]);
      setUncertainty("");
      onLogged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Add meal">
      <form onSubmit={save} className="space-y-3">
        <div className="space-y-2 rounded-lg border border-edge p-3">
          <label className="block text-sm text-mist" htmlFor="food-photo">Food photo (JPEG, PNG or WebP, max 5 MB)</label>
          <input id="food-photo" type="file" accept="image/jpeg,image/png,image/webp" disabled={busy}
            onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} className="text-sm text-mist" />
          <Button type="button" onClick={analyze} disabled={busy || !photo}>Analyze photo</Button>
          <p className="text-xs text-ash">Analyzing sends the image to OpenAI and incurs API charges. This app does not save it; OpenAI may retain data for abuse monitoring. Avoid personal or sensitive details. Estimates are uncertain: review and correct ingredients, portions and values before logging.</p>
        </div>
        {uncertainty && <p className="text-sm text-mist">{uncertainty}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Select value={meal} onChange={(e) => setMeal(e.target.value)} className="w-36">
            {["breakfast", "lunch", "dinner", "snack"].map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
          {(templates.data ?? []).map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => loadTemplate(t)}
              className="rounded-full border border-edge bg-night-3 px-3 py-1 text-xs text-mist transition-colors hover:border-lime/50 hover:text-lime"
            >
              {t.name} · {t.total_kcal} kcal
            </button>
          ))}
        </div>

        <div className="space-y-2">
          {items.map((item, i) => (
            <div key={i} className="flex items-center gap-2">
              <Input
                placeholder="Food name (oats, whey, peanut butter...)"
                value={item.name}
                onChange={(e) => patch(i, { name: e.target.value })}
                className="flex-1"
              />
              <Input
                type="number"
                min="1"
                max="2000"
                step="1"
                value={item.grams}
                onChange={(e) => patch(i, { grams: e.target.value })}
                className="w-24!"
                title="Grams"
              />
              <span className="text-xs text-ash">g</span>
              <button
                type="button"
                className="text-ash hover:text-ember disabled:opacity-30"
                disabled={items.length === 1}
                onClick={() => {
                  setItems(items.filter((_, j) => j !== i));
                  setRows(null);
                }}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setItems([...items, { name: "", grams: "100" }]);
              setRows(null);
            }}
          >
            <Plus className="mr-1 inline h-3.5 w-3.5" /> Add food
          </Button>
          <Button type="button" onClick={compute} disabled={busy || !clean.length}>
            {busy && !rows ? "Computing..." : "Compute nutrition"}
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !clean.length} className="ml-auto">
            {busy ? "Working..." : "Log meal"}
          </Button>
        </div>

        {rows && rows.length > 0 && (
          <div className="rounded-xl border border-edge bg-night-3 p-3">
            <ul className="space-y-1.5 text-sm">
              {rows.map((r, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2">
                  <span className="flex min-w-[140px] flex-1 items-center gap-2 text-mist">
                    {r.grams} g {r.name}
                    <Badge tone={r.source === "fatsecret" ? "lime" : r.source === "template" ? "zinc" : "amber"}>
                      {r.source === "fatsecret" ? "db" : r.source === "template" ? "saved" : "est."}
                    </Badge>
                  </span>
                  <input
                    type="number" step="1" min="0" title="kcal — editable" value={Math.round(r.kcal)}
                    onChange={(e) => patchRow(i, { kcal: Number(e.target.value) })}
                    className="w-16 rounded border border-edge bg-night-2 px-1.5 py-0.5 text-right text-xs text-cloud"
                  />
                  <span className="text-[10px] text-ash">kcal</span>
                  {(["protein_g", "carbs_g", "fat_g", "fiber_g"] as const).map((key) => (
                    <span key={key} className="flex items-center gap-1">
                      <input
                        type="number" step="0.1" min="0" title={key} value={r[key] ?? ""}
                        onChange={(e) => patchRow(i, { [key]: e.target.value === "" ? null : Number(e.target.value) })}
                        className="w-14 rounded border border-edge bg-night-2 px-1.5 py-0.5 text-right text-xs text-cloud"
                      />
                      <span className="text-[10px] text-ash">{key === "protein_g" ? "P" : key === "carbs_g" ? "C" : key === "fat_g" ? "F" : "Fiber"}</span>
                    </span>
                  ))}
                </li>
              ))}
            </ul>
            <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 border-t border-edge pt-2 text-sm">
              <span className="font-grotesk text-lg font-bold text-lime">{Math.round(totals.kcal)} kcal</span>
              <span className="text-mist">P {totals.protein_g?.toFixed(0) ?? "?"} g</span>
              <span className="text-mist">C {totals.carbs_g?.toFixed(0) ?? "?"} g</span>
              <span className="text-mist">F {totals.fat_g?.toFixed(0) ?? "?"} g</span>
              <span className="text-mist">Fiber {totals.fiber_g?.toFixed(0) ?? "?"} g</span>
              <span className="ml-auto text-xs text-ash">values are editable — correct anything that looks wrong</span>
            </div>
          </div>
        )}
        {error && <p className="text-sm text-ember">{error}</p>}
        <p className="text-xs text-ash">
          Changing a food or portion clears its nutrition values: compute again, review and then log.
          Database matches and AI values are estimates, not measurements.
        </p>
      </form>
    </Card>
  );
}

export default function Nutrition({ userId }: { userId: string }) {
  const [day, setDay] = useState(todayISO());
  const [editingMeal, setEditingMeal] = useState<number | null>(null);
  const meals = useFetch(() => api.meals(userId, day), [userId, day]);
  const intake = useFetch(() => api.intake(userId, 14), [userId]);
  const programs = useFetch(() => api.programs(userId, "active"), [userId]);


  // Template "Log" buttons notify us so the day's list refreshes.
  useEffect(() => {
    const handler = () => {
      meals.reload();
      intake.reload();
    };
    window.addEventListener("meals-changed", handler);
    return () => window.removeEventListener("meals-changed", handler);
  }, [userId]);

  const changeDay = (offset: number) => {
    const [year, month, date] = day.split("-").map(Number);
    const next = new Date(year, month - 1, date + offset);
    const nextDay = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
    setEditingMeal(null);
    setDay(nextDay);
  };

  const nutritionProgram = (programs.data ?? []).find((p) => p.kind === "nutrition");
  const target = typeof nutritionProgram?.payload?.target_kcal === "number" ? nutritionProgram.payload.target_kcal : null;
  const fiberTarget = typeof nutritionProgram?.payload?.fiber_g === "number" ? nutritionProgram.payload.fiber_g : null;
  const sodiumTarget = typeof nutritionProgram?.payload?.sodium_mg === "number" ? nutritionProgram.payload.sodium_mg : null;

  const intakeRows = useMemo(() => (intake.data ?? []).map((d) => ({ date: d.date.slice(5), kcal: d.kcal })), [intake.data]);

  const sum = (key: "kcal" | "protein_g" | "carbs_g" | "fat_g" | "fiber_g") =>
    meals.data?.reduce((total, m) => total + (m[key] ?? 0), 0) ?? 0;
  const dailyNutrient = (key: "protein_g" | "carbs_g" | "fat_g" | "fiber_g") =>
    meals.data?.length && meals.data.every((m) => m[key] != null) ? sum(key) : null;
  const dayTotal = sum("kcal");
  const dayProtein = dailyNutrient("protein_g");
  const dayCarbs = dailyNutrient("carbs_g");
  const dayFat = dailyNutrient("fat_g");
  const dayFiber = dailyNutrient("fiber_g");

  const remove = async (mealId: number) => {
    await api.deleteMeal(userId, mealId);
    meals.reload();
    intake.reload();
  };

  return (
    <div>
      <PageHeader title="Nutrition" subtitle="Daily totals, meals, templates and your nutrition plan." />

      {/* Daily totals for the selected day, with remaining budget when a plan target exists */}
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-6">
        <Stat
          label={day === todayISO() ? "Calories today" : `Calories ${day.slice(5)}`}
          value={dayTotal.toLocaleString()}
          unit="kcal"
          delta={target ? `estimated maintenance ≈ ${target.toLocaleString()} kcal` : undefined}
          deltaGood={null}
        />
        <Stat label="Protein" value={dayProtein?.toFixed(0) ?? "—"} unit="g" delta={nutritionProgram ? `target ${nutritionProgram.payload.protein_g ?? "—"} g` : undefined} deltaGood={null} />
        <Stat label="Carbs" value={dayCarbs?.toFixed(0) ?? "—"} unit="g" delta={nutritionProgram ? `target ${nutritionProgram.payload.carbs_g ?? "—"} g` : undefined} deltaGood={null} />
        <Stat label="Fat" value={dayFat?.toFixed(0) ?? "—"} unit="g" delta={nutritionProgram ? `target ${nutritionProgram.payload.fat_g ?? "—"} g` : undefined} deltaGood={null} />
        <Stat label="Fiber" value={dayFiber?.toFixed(0) ?? "—"} unit="g" delta={fiberTarget ? `target ${fiberTarget} g` : undefined} deltaGood={dayFiber == null ? null : dayFiber >= (fiberTarget ?? 25)} />
        <Stat
          label="Sodium"
          value={sodiumTarget ? `≤ ${sodiumTarget.toLocaleString()}` : "—"}
          unit="mg"
          delta="daily cap from your plan"
          deltaGood={null}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card
            title="Meals"
            action={
              <div className="flex items-center gap-3">
                <span className="text-sm font-semibold text-lime">
                  {dayTotal.toLocaleString()} kcal{dayFiber != null && dayFiber > 0 && <span className="text-mist"> · {dayFiber.toFixed(0)} g fiber</span>}
                </span>
                <div className="flex items-center gap-1">
                  <Button type="button" variant="ghost" className="px-2" onClick={() => changeDay(-1)} aria-label="Previous day">
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <Input
                    type="date"
                    value={day}
                    onChange={(e) => {
                      if (e.target.value) {
                        setEditingMeal(null);
                        setDay(e.target.value);
                      }
                    }}
                    className="w-auto!"
                    aria-label="Selected nutrition day"
                  />
                  <Button type="button" variant="ghost" className="px-2" onClick={() => changeDay(1)} aria-label="Next day">
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            }
          >
            {meals.loading ? (
              <Spinner label="" />
            ) : meals.data && meals.data.length > 0 ? (
              <ul className="divide-y divide-edge">
                {meals.data.map((m) => (
                  <li key={m.id} className="py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <span className="text-sm font-medium text-cloud">{m.description}</span>
                        <div className="mt-0.5 flex items-center gap-2 text-xs text-mist">
                          <Badge>{m.meal}</Badge>
                          <Badge tone={m.source === "assistant" ? "amber" : m.source === "template" ? "lime" : "zinc"}>{m.source}</Badge>
                          {m.protein_g != null && <span>P {m.protein_g}g</span>}
                          {m.carbs_g != null && <span>C {m.carbs_g}g</span>}
                          {m.fat_g != null && <span>F {m.fat_g}g</span>}
                          {m.fiber_g != null && <span>Fiber {m.fiber_g}g</span>}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <span className="text-sm font-semibold text-cloud">{m.kcal}</span>
                        {m.fatsecret_entry_id ? (
                          <span className="text-xs text-ash" title="Manage this entry in FatSecret">Manage in FatSecret</span>
                        ) : (
                          <>
                            <Button variant="ghost" className="px-2" onClick={() => setEditingMeal(editingMeal === m.id ? null : m.id)}>
                              {editingMeal === m.id ? "Cancel" : "Edit"}
                            </Button>
                            <button onClick={() => remove(m.id)} className="text-ash transition-colors hover:text-ember" aria-label={`Delete ${m.description}`}>
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                    {editingMeal === m.id && !m.fatsecret_entry_id && (
                      <EditMealForm
                        userId={userId}
                        meal={m}
                        onCancel={() => setEditingMeal(null)}
                        onSaved={() => {
                          setEditingMeal(null);
                          meals.reload();
                          intake.reload();
                        }}
                      />
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No meals logged for {day}.</Empty>
            )}
          </Card>

          <AddMealCard
            userId={userId}
            day={day}
            onLogged={() => {
              meals.reload();
              intake.reload();
            }}
          />

          <MealTemplates userId={userId} day={day} />
        </div>

        <div className="space-y-4">
          <Card title="Intake (14 days)">
            {intakeRows.length ? (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={intakeRows}>
                  <CartesianGrid stroke="#2c3540" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" {...AXES} />
                  <YAxis {...AXES} width={44} />
                  <Tooltip {...TOOLTIP} cursor={{ fill: "rgba(163,230,53,0.05)" }} />
                  <Bar dataKey="kcal" fill="#a3e635" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <Empty>No intake yet.</Empty>
            )}
          </Card>

          <p className="flex items-start gap-2 px-1 text-xs text-ash">
            <MessageCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cyan" />
            {nutritionProgram
              ? "Targets come from your Telegram plan — numbers only, no menus. Ask the coach \"ne yemeliyim?\" when you want meal ideas."
              : "No active plan yet. Use the Plan page to review an energy estimate and create one."}
          </p>
        </div>
      </div>
      {programs.error && <ErrorNote error={programs.error} />}
    </div>
  );
}
