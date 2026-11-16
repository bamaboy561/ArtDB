import { useEffect, useState } from "react";
import { api, ApiError, User } from "./api";
import Login from "./pages/Login";
import Orders from "./pages/Orders";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch((error) => {
        if (!(error instanceof ApiError) || error.status !== 401) console.error(error);
      })
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <div className="splash">Загрузка…</div>;
  if (!user) return <Login onSignedIn={setUser} />;

  const signOut = async () => {
    await api.logout().catch(() => undefined);
    setUser(null);
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">{user.display_name.slice(0, 1).toUpperCase()}</div>
          <div>
            <div className="brand-name">{user.display_name}</div>
            <div className="brand-meta">ArtDB · новая версия</div>
          </div>
        </div>
        <nav className="nav">
          <div className="nav-label">Разделы</div>
          <a className="nav-item active">Закупки · Заказы</a>
          <div className="nav-soon">Остальные разделы переезжают сюда по очереди.</div>
        </nav>
        <button className="button ghost" onClick={signOut}>
          Выйти
        </button>
      </aside>
      <main className="main">
        <Orders user={user} />
      </main>
    </div>
  );
}
