// The API of web/api. Every call sends the session cookie; a 401 means the user must sign in again.

export type User = {
  username: string;
  display_name: string;
  role: "admin" | "manager" | "salon";
  salon: string;
};

export type FormSummary = {
  key: string;
  title: string;
  to_order: number;
  positions: number;
  lead_time_days: number;
  arrival: string;
};

export type OrderRow = {
  row_id: string;
  code: string;
  name: string;
  quantity: number;
  edge_or_sheets: number;
  section: string;
  pack: number | null;
  abc_class: string;
  xyz_class: string;
  priority: string;
  stock_status: string;
  available_stock_qty: number;
  forecast_qty: number;
};

export type QuantityColumn = { field: "quantity" | "edge_or_sheets"; title: string };

export type FormDetail = {
  key: string;
  title: string;
  layout: { columns: QuantityColumn[]; box_layout: boolean; unit: string };
  lead_time_days: number;
  arrival: string;
  rows: OrderRow[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? `Ошибка ${response.status}`);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  me: () => request<User>("/api/auth/me"),
  login: (login: string, password: string) => request<User>("/api/auth/login", json({ login, password })),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  salons: () => request<string[]>("/api/salons"),
  forms: (salons: string[]) => request<FormSummary[]>(`/api/orders/forms?salons=${encodeURIComponent(salons.join(","))}`),
  form: (key: string, salons: string[]) =>
    request<FormDetail>(`/api/orders/forms/${encodeURIComponent(key)}?salons=${encodeURIComponent(salons.join(","))}`),

  async orderFile(key: string, salons: string[], rows: OrderRow[]): Promise<{ blob: Blob; filename: string }> {
    const lines = rows.map(({ row_id, quantity, edge_or_sheets }) => ({ row_id, quantity, edge_or_sheets }));
    const response = await fetch(`/api/orders/forms/${encodeURIComponent(key)}/file`, json({ salons, lines }));
    if (!response.ok) throw new ApiError(response.status, "Не удалось собрать файл заказа.");
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const encoded = /filename\*=UTF-8''([^;]+)/.exec(disposition)?.[1];
    return { blob: await response.blob(), filename: encoded ? decodeURIComponent(encoded) : `${key}_zakaz.xlsx` };
  },
};
