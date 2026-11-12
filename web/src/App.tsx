import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ApiError, UserSummary, api, getToken, setToken } from "./api";
import Layout from "./components/Layout";
import { Button, Card, Input } from "./components/ui";
import Today from "./pages/Today";
import Trends from "./pages/Trends";
import Nutrition from "./pages/Nutrition";
import Workouts from "./pages/Workouts";
import Measures from "./pages/Measures";
import MemoryPage from "./pages/Memory";
import Cost from "./pages/Cost";
import SettingsPage from "./pages/Settings";

const USER_KEY = "coach_user";

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  const submit = async () => {
    setToken(value.trim());
    try {
      await api.users();
      onSignedIn();
    } catch (err) {
      setToken("");
      setError(err instanceof Error ? err.message : "Sign-in failed");
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card title="Sign in" className="w-full max-w-sm">
        <p className="mb-4 text-sm text-zinc-500">
          Auth is enabled on this server. Paste the access token issued by{" "}
          <code className="rounded bg-zinc-800 px-1">python -m scripts.issue_token</code>.
        </p>
        <Input type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Access token" />
        {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
        <Button variant="primary" className="mt-4 w-full" onClick={submit} disabled={!value.trim()}>
          Sign in
        </Button>
      </Card>
    </div>
  );
}

export default function App() {
  const [users, setUsers] = useState<UserSummary[] | null>(null);
  const [userId, setUserId] = useState<string>(() => localStorage.getItem(USER_KEY) ?? "");
  const [needsAuth, setNeedsAuth] = useState(false);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const loadUsers = useCallback(async () => {
    try {
      const list = await api.users();
      setNeedsAuth(false);
      setError("");
      setUsers(list);
      setUserId((current) => {
        const valid = list.some((u) => u.user_id === current) ? current : (list[0]?.user_id ?? "");
        localStorage.setItem(USER_KEY, valid);
        return valid;
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setNeedsAuth(true);
      } else {
        setError(err instanceof Error ? err.message : "Cannot reach the API at this origin");
      }
    }
  }, []);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers, reloadKey]);

  if (needsAuth) {
    return <SignIn onSignedIn={() => setReloadKey((k) => k + 1)} />;
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Card title="Cannot reach the coach API" className="max-w-md">
          <p className="text-sm text-zinc-400">{error}</p>
          <p className="mt-2 text-sm text-zinc-500">
            Start the API (<code className="rounded bg-zinc-800 px-1">uvicorn app.main:app</code>) and the dev server (
            <code className="rounded bg-zinc-800 px-1">npm run dev</code> inside <code>web/</code>).
          </p>
          <Button className="mt-4" onClick={() => setReloadKey((k) => k + 1)}>
            Retry
          </Button>
          {getToken() && (
            <Button
              variant="ghost"
              className="mt-4 ml-2"
              onClick={() => {
                setToken("");
                setReloadKey((k) => k + 1);
              }}
            >
              Clear token
            </Button>
          )}
        </Card>
      </div>
    );
  }

  if (users === null) {
    return <div className="p-10 text-sm text-zinc-500">Loading...</div>;
  }

  if (users.length === 0 || !userId) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Card title="No profiles yet" className="max-w-md">
          <p className="text-sm text-zinc-400">
            Create a profile first (web onboarding, <code className="rounded bg-zinc-800 px-1">scripts.issue_token</code>,
            or <code className="rounded bg-zinc-800 px-1">PUT /users/me</code>), then reload.
          </p>
          <Button className="mt-4" onClick={() => setReloadKey((k) => k + 1)}>
            Reload
          </Button>
        </Card>
      </div>
    );
  }

  return (
    <Routes>
      <Route
        element={
          <Layout
            users={users}
            userId={userId}
            onUserChange={(id) => {
              localStorage.setItem(USER_KEY, id);
              setUserId(id);
            }}
          />
        }
      >
        <Route path="/" element={<Today userId={userId} />} />
        <Route path="/trends" element={<Trends userId={userId} />} />
        <Route path="/nutrition" element={<Nutrition userId={userId} />} />
        <Route path="/workouts" element={<Workouts userId={userId} />} />
        <Route path="/measures" element={<Measures userId={userId} />} />
        <Route path="/memory" element={<MemoryPage userId={userId} />} />
        <Route path="/cost" element={<Cost userId={userId} />} />
        <Route path="/settings" element={<SettingsPage userId={userId} onUsersChanged={() => setReloadKey((k) => k + 1)} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
