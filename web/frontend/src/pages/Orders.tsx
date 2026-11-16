import { useEffect, useMemo, useState } from "react";
import { api, FormDetail, FormSummary, OrderRow, User } from "../api";

const dateLabel = (iso: string) => iso.split("-").reverse().join(".");

export default function Orders({ user }: { user: User }) {
  const [salons, setSalons] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [forms, setForms] = useState<FormSummary[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.salons().then((names) => {
      setSalons(names);
      setChosen(names);
    });
  }, []);

  useEffect(() => {
    if (!chosen.length) return;
    setForms(null);
    setError("");
    api.forms(chosen).then(setForms, (failure) => setError(failure.message));
  }, [chosen]);

  const toggleSalon = (name: string) =>
    setChosen((current) => {
      const next = current.includes(name) ? current.filter((salon) => salon !== name) : [...current, name];
      return next.length ? next : current;
    });

  if (user.role === "salon") {
    return <div className="notice">Закупки доступны администратору и руководителю.</div>;
  }

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>Заказы поставщикам</h1>
          <p className="muted">
            Откройте поставщика, поправьте количества и скачайте файл. Считается по продажам выбранных салонов.
          </p>
        </div>
        <div className="chips">
          {salons.map((name) => (
            <button
              key={name}
              className={`chip ${chosen.includes(name) ? "on" : ""}`}
              onClick={() => toggleSalon(name)}
            >
              {name}
            </button>
          ))}
        </div>
      </header>
      {error && <div className="error">{error}</div>}
      {!forms && !error && <div className="muted">Считаем прогноз…</div>}
      {forms?.map((form) => <SupplierOrder key={`${form.key}:${chosen.join(",")}`} summary={form} salons={chosen} />)}
    </section>
  );
}

function SupplierOrder({ summary, salons }: { summary: FormSummary; salons: string[] }) {
  const [detail, setDetail] = useState<FormDetail | null>(null);
  const [rows, setRows] = useState<OrderRow[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // A closed order costs nothing: its table is fetched the first time it opens.
  const open = (event: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!event.currentTarget.open || detail) return;
    api.form(summary.key, salons).then(
      (loaded) => {
        setDetail(loaded);
        setRows(loaded.rows);
      },
      (failure) => setError(failure.message),
    );
  };

  const setQuantity = (rowId: string, field: "quantity" | "edge_or_sheets", value: string) => {
    const number = Math.max(0, Math.round(Number(value) || 0));
    setRows((current) => current.map((row) => (row.row_id === rowId ? { ...row, [field]: number } : row)));
  };

  const totals = useMemo(
    () => ({
      lines: rows.filter((row) => row.quantity > 0 || row.edge_or_sheets > 0).length,
      quantity: rows.reduce((sum, row) => sum + row.quantity, 0),
      edge: rows.reduce((sum, row) => sum + row.edge_or_sheets, 0),
    }),
    [rows],
  );

  const download = async () => {
    setBusy(true);
    try {
      const { blob, filename } = await api.orderFile(summary.key, salons, rows);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось скачать файл.");
    } finally {
      setBusy(false);
    }
  };

  const proposal = summary.to_order ? `к заказу ${summary.to_order} поз.` : "заказывать нечего";

  return (
    <details className="order" onToggle={open}>
      <summary>
        <span className="order-title">Заказ поставщику {summary.title}</span>
        <span className={`badge ${summary.to_order ? "warn" : ""}`}>{proposal}</span>
      </summary>
      <div className="order-body">
        <p className="muted">
          Если отправить заказ сегодня, груз придёт <b>{dateLabel(summary.arrival)}</b>, через {summary.lead_time_days} дн.
        </p>
        {error && <div className="error">{error}</div>}
        {!detail && !error && <div className="muted">Загружаем товары…</div>}
        {detail && detail.rows.length === 0 && <div className="muted">Сейчас нет товаров этого поставщика.</div>}
        {detail && detail.rows.length > 0 && (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {detail.layout.columns.map((column) => (
                      <th key={column.field} className="number">
                        {column.title}
                      </th>
                    ))}
                    {detail.layout.box_layout && <th className="number">В упаковке</th>}
                    <th>Код</th>
                    <th>Название</th>
                    <th>ABC</th>
                    <th>Приоритет</th>
                    <th className="number">Доступно</th>
                    <th className="number">Прогноз</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.row_id} className={row.quantity > 0 || row.edge_or_sheets > 0 ? "" : "idle"}>
                      {detail.layout.columns.map((column) => (
                        <td key={column.field} className="number">
                          <input
                            type="number"
                            min={0}
                            value={row[column.field]}
                            onChange={(event) => setQuantity(row.row_id, column.field, event.target.value)}
                          />
                        </td>
                      ))}
                      {detail.layout.box_layout && <td className="number">{row.pack || ""}</td>}
                      <td>{row.code}</td>
                      <td>{row.name}</td>
                      <td>{row.abc_class}</td>
                      <td>{row.priority}</td>
                      <td className="number">{Math.round(row.available_stock_qty)}</td>
                      <td className="number">{Math.round(row.forecast_qty)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="order-footer">
              <span className="muted">
                К заказу: позиций {totals.lines}
                {detail.layout.columns.length > 1
                  ? ` · плит ${totals.quantity} шт · кромки ${totals.edge} м`
                  : ` · всего ${totals.edge}${detail.layout.unit ? ` ${detail.layout.unit}` : ""}`}
              </span>
              <button className="button primary" onClick={download} disabled={busy || totals.lines === 0}>
                {busy ? "Собираем файл…" : `Скачать заказ ${summary.title}`}
              </button>
            </div>
          </>
        )}
      </div>
    </details>
  );
}
