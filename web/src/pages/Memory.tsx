import { FormEvent, useState } from "react";
import { Pin, Trash2 } from "lucide-react";
import { api, Memory } from "../api";
import { useFetch } from "../hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Spinner } from "../components/ui";

const KINDS: Memory["kind"][] = ["preference", "fact", "constraint"];

function MemoryRow({ userId, memory, onChanged }: { userId: string; memory: Memory; onChanged: () => void }) {
  const [value, setValue] = useState(memory.value);

  const save = async (updates: { value?: string; pinned?: boolean }) => {
    await api.saveMemory(userId, {
      kind: memory.kind,
      key: memory.key,
      value: updates.value ?? value,
      pinned: updates.pinned ?? memory.pinned,
    });
    onChanged();
  };

  return (
    <div className="flex items-start gap-3 py-3">
      <div className="w-44 shrink-0">
        <div className="font-mono text-xs text-cloud">
          {memory.kind}/{memory.key}
        </div>
        <div className="mt-1 flex gap-1.5">
          <Badge tone={memory.source === "user" ? "lime" : "zinc"}>{memory.source}</Badge>
          {memory.pinned && <Badge tone="amber">pinned</Badge>}
        </div>
      </div>
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => value !== memory.value && save({})}
        className="flex-1"
      />
      <button
        title={memory.pinned ? "Unpin" : "Pin (never auto-evicted)"}
        onClick={() => save({ pinned: !memory.pinned })}
        className={`mt-1.5 ${memory.pinned ? "text-amber" : "text-ash hover:text-cloud"}`}
      >
        <Pin className="h-4 w-4" />
      </button>
      <button
        title="Delete"
        onClick={async () => {
          await api.deleteMemory(userId, memory.kind, memory.key);
          onChanged();
        }}
        className="mt-1.5 text-ash transition-colors hover:text-ember"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  );
}

export default function MemoryPage({ userId }: { userId: string }) {
  const memories = useFetch(() => api.memories(userId), [userId]);
  const [form, setForm] = useState({ kind: "preference", key: "", value: "", pinned: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.saveMemory(userId, {
        kind: form.kind,
        key: form.key.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_"),
        value: form.value.trim(),
        pinned: form.pinned,
      });
      setForm({ kind: form.kind, key: "", value: "", pinned: false });
      memories.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  if (memories.loading) return <Spinner />;

  return (
    <div>
      <PageHeader
        title="Coach memory"
        subtitle="Durable facts injected into every conversation — on web and Telegram. 'user' entries are never overwritten by the assistant."
      />

      <Card title={`Stored memories (${memories.data?.length ?? 0})`} className="mb-4">
        {memories.error ? (
          <ErrorNote error={memories.error} />
        ) : memories.data && memories.data.length ? (
          <div className="divide-y divide-edge">
            {memories.data.map((memory) => (
              <MemoryRow key={memory.id} userId={userId} memory={memory} onChanged={memories.reload} />
            ))}
          </div>
        ) : (
          <Empty>Nothing stored yet. Tell the coach "remember that ..." or add an entry below.</Empty>
        )}
      </Card>

      <Card title="Add memory">
        <form onSubmit={submit} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-[160px_200px_1fr]">
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {kind}
                </option>
              ))}
            </Select>
            <Input required placeholder="key (snake_case)" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} />
            <Input required placeholder="Value — one short sentence" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} />
          </div>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-mist">
              <input type="checkbox" checked={form.pinned} onChange={(e) => setForm({ ...form, pinned: e.target.checked })} className="accent-lime" />
              Pinned
            </label>
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Saving..." : "Save memory"}
            </Button>
          </div>
          {error && <p className="text-sm text-ember">{error}</p>}
        </form>
      </Card>
    </div>
  );
}
