import { useEffect, useState } from "react";
import { api, ApiError, User } from "./api";
import Inbound from "./pages/Inbound";
import Login from "./pages/Login";
import Orders from "./pages/Orders";

// Only the screens that already work: the rest move here one by one.
const SECTIONS = [
  {
    title: "Закупки",
    pages: [
      { path: "orders", label: "Заказы поставщикам" },
      { path: "inbound", label: "В пути" },
    ],
  },
] as const;

type Path = (typeof SECTIONS)[number]["pages"][number]["path"];

const currentPath = (): Path => (window.location.hash.replace(/^#\/?/, "") === "inbound" ? "inbound" : "orders");

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [path, setPath] = useState<Path>(currentPath);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch((error) => {
        if (!(error instanceof ApiError) || error.status !== 401) console.error(error);
      })
      .finally(() => setChecking(false));
    const follow = () => setPath(currentPath());
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
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
          <div className="brand-mark">A</div>
          <div className="brand-name">ArtDB</div>
        </div>
        <nav className="nav">
          {SECTIONS.map((section) => (
            <div key={section.title} className="nav-group">
              <div className="nav-label">{section.title}</div>
              {section.pages.map((page) => (
                <a key={page.path} href={`#/${page.path}`} className={`nav-item ${path === page.path ? "active" : ""}`}>
                  {page.label}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="account">
          <div className="account-name">{user.display_name}</div>
          <button className="link-button" onClick={signOut}>
            Выйти
          </button>
        </div>
      </aside>
      <main className="main">{path === "inbound" ? <Inbound user={user} /> : <Orders user={user} />}</main>
    </div>
  );
}
