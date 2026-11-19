// Small building blocks shared by every screen: icons, notices, the confirm dialog and loading placeholders.
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";

const PATHS: Record<string, string> = {
  orders: "M3 4h2l2.4 10.2a1 1 0 0 0 1 .8h8.9a1 1 0 0 0 1-.8L20 7H6.2M9 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm8 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z",
  truck: "M3 6h11v9H3zM14 9h4l3 3v3h-7M7.5 18.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Zm10 0a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm9 3-4.3-4.3",
  chevron: "m9 6 6 6-6 6",
  down: "m6 9 6 6 6-6",
  download: "M12 4v11m0 0-4-4m4 4 4-4M5 19h14",
  upload: "M12 20V9m0 0-4 4m4-4 4 4M5 5h14",
  check: "m5 12 4.5 4.5L19 7",
  calendar: "M5 6h14v14H5zM5 10h14M9 3v4m6-4v4",
  close: "M6 6l12 12M18 6 6 18",
  sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-13v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z",
  logout: "M15 4h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3M10 16l-4-4 4-4M6 12h10",
  alert: "M12 9v4m0 3.5v.01M10.3 4.2 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4l3 2",
  box: "M21 8 12 3 3 8v8l9 5 9-5V8Zm-18 0 9 5 9-5m-9 5v8",
  store: "M4 9h16l-1-5H5L4 9Zm0 0v11h16V9M9 20v-6h6v6",
  minus: "M5 12h14",
  plus: "M12 5v14M5 12h14",
  file: "M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8l-5-5Zm0 0v5h5",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5m4-1v5l3 2",
};

export function Icon({ name, size = 18, className = "" }: { name: keyof typeof PATHS | string; size?: number; className?: string }) {
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}

// Light, dark or as the device is set; the choice is kept in this browser only.
export type Theme = "light" | "dark";

export function useTheme(): [Theme, () => void] {
  const system = () => (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem("artdb-theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch {
      /* storage can be blocked */
    }
    return system();
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const toggle = () =>
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      try {
        localStorage.setItem("artdb-theme", next);
      } catch {
        /* storage can be blocked */
      }
      return next;
    });
  return [theme, toggle];
}

// Notices that appear in the corner and leave by themselves.
type Toast = { id: number; text: string; tone: "ok" | "error" };
type ConfirmRequest = { title: string; text: string; action: string; danger?: boolean; resolve: (yes: boolean) => void };

const FeedbackContext = createContext<{
  notify: (text: string, tone?: Toast["tone"]) => void;
  confirm: (request: Omit<ConfirmRequest, "resolve">) => Promise<boolean>;
}>({ notify: () => undefined, confirm: async () => false });

export const useFeedback = () => useContext(FeedbackContext);

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const counter = useRef(0);

  const notify = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = ++counter.current;
    setToasts((current) => [...current, { id, text, tone }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 6000);
  }, []);

  const confirm = useCallback(
    (details: Omit<ConfirmRequest, "resolve">) => new Promise<boolean>((resolve) => setRequest({ ...details, resolve })),
    [],
  );

  const answer = (yes: boolean) => {
    request?.resolve(yes);
    setRequest(null);
  };

  useEffect(() => {
    if (!request) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && answer(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <FeedbackContext.Provider value={{ notify, confirm }}>
      {children}
      <div className="toasts" role="status">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.tone}`}>
            <Icon name={toast.tone === "ok" ? "check" : "alert"} />
            <span>{toast.text}</span>
            <button className="icon-button" aria-label="Закрыть" onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}>
              <Icon name="close" size={16} />
            </button>
          </div>
        ))}
      </div>
      {request && (
        <div className="overlay" onClick={() => answer(false)}>
          <div className="dialog" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <div className={`dialog-icon ${request.danger ? "danger" : ""}`}>
              <Icon name={request.danger ? "alert" : "check"} size={22} />
            </div>
            <h2>{request.title}</h2>
            <p className="muted">{request.text}</p>
            <div className="dialog-actions">
              <button className="button" onClick={() => answer(false)}>
                Отмена
              </button>
              <button className={`button ${request.danger ? "danger" : "primary"}`} onClick={() => answer(true)} autoFocus>
                {request.action}
              </button>
            </div>
          </div>
        </div>
      )}
    </FeedbackContext.Provider>
  );
}

export function Stat({ label, value, hint, tone = "", icon }: { label: string; value: ReactNode; hint?: string; tone?: string; icon: string }) {
  return (
    <div className={`stat ${tone}`}>
      <div className="stat-icon">
        <Icon name={icon} size={18} />
      </div>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { key: T; label: string; count?: number }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="tablist">
      {options.map((option) => (
        <button key={option.key} role="tab" aria-selected={value === option.key} className={value === option.key ? "on" : ""} onClick={() => onChange(option.key)}>
          {option.label}
          {option.count !== undefined && <span className="segmented-count">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function SearchField({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
  return (
    <label className="search">
      <Icon name="search" size={16} />
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
      {value && (
        <button className="icon-button" aria-label="Очистить" onClick={() => onChange("")}>
          <Icon name="close" size={14} />
        </button>
      )}
    </label>
  );
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="skeleton" aria-label="Загрузка">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton-line" style={{ width: `${92 - (index % 3) * 14}%` }} />
      ))}
    </div>
  );
}

export function Empty({ icon, title, text }: { icon: string; title: string; text?: string }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon name={icon} size={22} />
      </div>
      <b>{title}</b>
      {text && <span className="muted">{text}</span>}
    </div>
  );
}

// Which salons' sales the forecast uses: a compact menu instead of a row of buttons.
export function SalonPicker({ salons, chosen, onChange }: { salons: string[]; chosen: string[]; onChange: (next: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => !box.current?.contains(event.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const toggle = (name: string) => {
    const next = chosen.includes(name) ? chosen.filter((salon) => salon !== name) : [...chosen, name];
    if (next.length) onChange(next);
  };
  const label = chosen.length === salons.length ? "Все салоны" : chosen.length === 1 ? chosen[0] : `${chosen.length} салона`;

  return (
    <div className="picker" ref={box}>
      <button className="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <Icon name="store" size={16} />
        {label}
        <Icon name="down" size={14} />
      </button>
      {open && (
        <div className="picker-menu">
          <div className="picker-head">
            <span>Прогноз по продажам</span>
            {chosen.length !== salons.length && (
              <button className="link-button" onClick={() => onChange(salons)}>
                Выбрать все
              </button>
            )}
          </div>
          {salons.map((name) => (
            <label key={name} className="picker-item">
              <input type="checkbox" checked={chosen.includes(name)} onChange={() => toggle(name)} />
              <span>{name}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export const initials = (text: string) =>
  text
    .replace(/[^\p{L}\p{N} ]/gu, " ")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase() || "?";

// The same supplier always gets the same tint.
export const tintOf = (text: string) => {
  let hash = 0;
  for (const char of text) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 6;
};
