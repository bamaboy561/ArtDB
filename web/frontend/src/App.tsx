import { useEffect, useState } from "react";
import { api, ApiError, User } from "./api";
import Inbound from "./pages/Inbound";
import Login from "./pages/Login";
import Orders from "./pages/Orders";
import { FeedbackProvider, Icon, initials, useTheme } from "./ui";

// Only the screens that already work: the rest move here one by one.
const SECTIONS = [
  {
    title: "Закупки",
    pages: [
      { path: "orders", label: "Заказы поставщикам", short: "Заказы", icon: "orders", hint: "Что заказать и файл для поставщика" },
      { path: "inbound", label: "В пути", short: "В пути", icon: "truck", hint: "Заказы в дороге и приход" },
    ],
  },
] as const;

type Path = (typeof SECTIONS)[number]["pages"][number]["path"];
const PAGES = SECTIONS.flatMap((section) => section.pages);

const currentPath = (): Path => (window.location.hash.replace(/^#\/?/, "") === "inbound" ? "inbound" : "orders");

const ROLE_LABELS: Record<User["role"], string> = { admin: "Администратор", manager: "Руководитель", salon: "Салон" };

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [path, setPath] = useState<Path>(currentPath);
  const [theme, toggleTheme] = useTheme();

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

  if (checking)
    return (
      <div className="splash">
        <div className="brand-mark large">A</div>
      </div>
    );
  if (!user) return <Login onSignedIn={setUser} />;

  const signOut = async () => {
    await api.logout().catch(() => undefined);
    setUser(null);
  };

  const page = PAGES.find((item) => item.path === path) ?? PAGES[0];

  return (
    <FeedbackProvider>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-mark">A</div>
            <div>
              <div className="brand-name">ArtDB</div>
              <div className="brand-sub">аналитика салонов</div>
            </div>
          </div>
          <nav className="nav">
            {SECTIONS.map((section) => (
              <div key={section.title} className="nav-group">
                <div className="nav-label">{section.title}</div>
                {section.pages.map((item) => (
                  <a key={item.path} href={`#/${item.path}`} className={`nav-item ${path === item.path ? "active" : ""}`} title={item.hint}>
                    <Icon name={item.icon} />
                    <span>{item.label}</span>
                  </a>
                ))}
              </div>
            ))}
          </nav>
          <div className="account">
            <div className="avatar">{initials(user.display_name)}</div>
            <div className="account-text">
              <div className="account-name">{user.display_name}</div>
              <div className="account-role">{ROLE_LABELS[user.role]}</div>
            </div>
            <button className="icon-button" onClick={signOut} title="Выйти" aria-label="Выйти">
              <Icon name="logout" />
            </button>
          </div>
        </aside>

        <div className="workspace">
          <header className="topbar">
            <div className="crumbs">
              <span className="muted">Закупки</span>
              <Icon name="chevron" size={14} className="muted" />
              <b>{page.label}</b>
            </div>
            <div className="topbar-actions">
              <button className="icon-button" onClick={toggleTheme} title={theme === "dark" ? "Светлая тема" : "Тёмная тема"} aria-label="Сменить тему">
                <Icon name={theme === "dark" ? "sun" : "moon"} />
              </button>
              <button className="icon-button mobile-only" onClick={signOut} title="Выйти" aria-label="Выйти">
                <Icon name="logout" />
              </button>
            </div>
          </header>
          <main className="main">{path === "inbound" ? <Inbound user={user} /> : <Orders user={user} />}</main>
        </div>

        <nav className="tabbar">
          {PAGES.map((item) => (
            <a key={item.path} href={`#/${item.path}`} className={path === item.path ? "active" : ""}>
              <Icon name={item.icon} size={20} />
              <span>{item.short}</span>
            </a>
          ))}
        </nav>
      </div>
    </FeedbackProvider>
  );
}
