import { FormEvent, useState } from "react";
import { ExternalLink } from "lucide-react";
import { api, getToken, setToken } from "../api";
import { useFetch } from "../hooks";
import { Badge, Button, Card, ErrorNote, Input, PageHeader, Select, Spinner } from "../components/ui";

const GOALS = ["strength", "muscle_gain", "fat_loss", "endurance", "general_fitness"];
const PROVIDERS: Record<string, { name: string; data: string }> = {
  google: { name: "Google Health (Fitbit)", data: "HRV, resting HR, sleep, weight & body fat (with a Fitbit scale)" },
  fatsecret: { name: "FatSecret", data: "food diary calories; enables coach meals to sync into your diary" },
};

export default function SettingsPage({ userId, onUsersChanged }: { userId: string; onUsersChanged: () => void }) {
  const profile = useFetch(() => api.user(userId), [userId]);
  const integrations = useFetch(() => api.integrations(userId), [userId]);
  const [form, setForm] = useState<{ name: string; age: string; weight: string; goal: string } | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [connectUrls, setConnectUrls] = useState<Record<string, string>>({});

  if (profile.loading) return <Spinner />;
  if (profile.error) return <ErrorNote error={profile.error} />;

  const current = form ?? {
    name: profile.data?.name ?? "",
    age: profile.data?.age != null ? String(profile.data.age) : "",
    weight: profile.data?.weight_kg != null ? String(profile.data.weight_kg) : "",
    goal: profile.data?.goal ?? "general_fitness",
  };

  const saveProfile = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setMessage("");
    try {
      await api.saveUser(userId, {
        name: current.name,
        age: current.age ? Number(current.age) : null,
        weight_kg: current.weight ? Number(current.weight) : null,
        goal: current.goal,
      });
      setForm(null);
      setMessage("Profile saved.");
      profile.reload();
      onUsersChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  const connect = async (provider: string) => {
    setError("");
    try {
      const result = await api.connect(userId, provider);
      setConnectUrls({ ...connectUrls, [provider]: result.authorization_url });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connect failed");
    }
  };

  const syncNow = async () => {
    setMessage("");
    setError("");
    try {
      const results = await api.sync(userId, 14);
      setMessage(results.map((r) => `${r.provider}: ${r.error ?? `${r.days_updated} days updated`}`).join(" · "));
      integrations.reload();
      profile.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    }
  };

  return (
    <div>
      <PageHeader title="Settings" subtitle="Profile, connected data sources and API access." />

      {message && <p className="mb-4 rounded-lg border border-lime-400/30 bg-lime-400/10 px-3 py-2 text-sm text-lime-400">{message}</p>}
      {error && <div className="mb-4"><ErrorNote error={error} /></div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Profile">
          <form onSubmit={saveProfile} className="space-y-3">
            <Input value={current.name} onChange={(e) => setForm({ ...current, name: e.target.value })} placeholder="Name" />
            <div className="grid grid-cols-2 gap-3">
              <Input type="number" min={10} max={110} value={current.age} onChange={(e) => setForm({ ...current, age: e.target.value })} placeholder="Age" />
              <Input type="number" step="0.5" min={20} max={400} value={current.weight} onChange={(e) => setForm({ ...current, weight: e.target.value })} placeholder="Weight (kg)" />
            </div>
            <Select value={current.goal} onChange={(e) => setForm({ ...current, goal: e.target.value })}>
              {GOALS.map((goal) => (
                <option key={goal} value={goal}>
                  {goal.replace("_", " ")}
                </option>
              ))}
            </Select>
            <Button type="submit" variant="primary">
              Save profile
            </Button>
          </form>
        </Card>

        <Card title="Connected apps">
          {integrations.data?.map((status) => (
            <div key={status.provider} className="border-b border-zinc-800/60 py-3 last:border-0">
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm font-semibold text-zinc-100">{PROVIDERS[status.provider]?.name ?? status.provider}</span>
                  <p className="mt-0.5 text-xs text-zinc-500">{PROVIDERS[status.provider]?.data}</p>
                </div>
                <Badge tone={status.connected ? "lime" : status.configured ? "amber" : "zinc"}>
                  {status.connected ? "connected" : status.configured ? "not connected" : "not configured"}
                </Badge>
              </div>
              {status.last_error && <p className="mt-2 text-xs text-red-400">{status.last_error}</p>}
              <div className="mt-2 flex flex-wrap gap-2">
                {status.configured && !status.connected &&
                  (connectUrls[status.provider] ? (
                    <a
                      href={connectUrls[status.provider]}
                      className="inline-flex items-center gap-1 rounded-lg bg-lime-400 px-3 py-1.5 text-sm font-semibold text-zinc-950 hover:bg-lime-300"
                    >
                      Approve access <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  ) : (
                    <Button onClick={() => connect(status.provider)}>Connect</Button>
                  ))}
                {status.connected && (
                  <>
                    <Button onClick={syncNow}>Sync now (14 days)</Button>
                    <Button
                      variant="danger"
                      onClick={async () => {
                        await api.disconnect(userId, status.provider);
                        integrations.reload();
                      }}
                    >
                      Disconnect
                    </Button>
                  </>
                )}
              </div>
              {status.connected && status.last_synced_at && (
                <p className="mt-2 text-xs text-zinc-600">Last sync: {status.last_synced_at.slice(0, 16).replace("T", " ")}</p>
              )}
            </div>
          ))}
          <p className="mt-3 text-xs text-zinc-600">
            Google Health stays in Testing mode: refresh tokens expire every 7 days until Google verifies the app, so reconnect weekly if sync fails.
          </p>
        </Card>

        <Card title="API access">
          <p className="text-sm text-zinc-400">
            {getToken() ? (
              <>
                Signed in with a stored token.{" "}
                <button
                  className="text-red-400 underline-offset-2 hover:underline"
                  onClick={() => {
                    setToken("");
                    window.location.reload();
                  }}
                >
                  Sign out
                </button>
              </>
            ) : (
              "Auth is disabled on this server (or no token stored). If the server enables AUTH_ENABLED, a sign-in screen will appear."
            )}
          </p>
        </Card>

        <Card title="Telegram">
          <p className="text-sm leading-relaxed text-zinc-400">
            Chat lives on Telegram. Configure <code className="rounded bg-zinc-800 px-1 text-xs">TELEGRAM_BOT_TOKEN</code>,{" "}
            <code className="rounded bg-zinc-800 px-1 text-xs">TELEGRAM_ALLOWED_CHAT_ID</code> and{" "}
            <code className="rounded bg-zinc-800 px-1 text-xs">TELEGRAM_API_TOKEN</code> in <code className="rounded bg-zinc-800 px-1 text-xs">.env</code>, then run{" "}
            <code className="rounded bg-zinc-800 px-1 text-xs">python -m scripts.telegram_bot</code>.
          </p>
        </Card>
      </div>
    </div>
  );
}
