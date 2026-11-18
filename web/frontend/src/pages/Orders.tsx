import { useEffect, useMemo, useRef, useState } from "react";
import { api, FilePreview, FormDetail, FormSummary, OrderRow, User } from "../api";

export const dateLabel = (iso: string | null) => (iso ? iso.split("-").reverse().join(".") : "—");

export default function Orders({ user }: { user: User }) {
  const [salons, setSalons] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [forms, setForms] = useState<FormSummary[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [round, setRound] = useState(0);

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
  }, [chosen, round]);

  if (user.role === "salon") return <div className="notice">Закупки доступны администратору и руководителю.</div>;

  const toggleSalon = (name: string) =>
    setChosen((current) => {
      const next = current.includes(name) ? current.filter((salon) => salon !== name) : [...current, name];
      return next.length ? next : current;
    });

  const ordered = (orderId: string) => {
    setNotice(`Заказ #${orderId.slice(-4)} оформлен и теперь в пути. Его видно в разделе «В пути».`);
    setRound((value) => value + 1);
  };

  const withOrders = forms?.filter((form) => form.to_order > 0) ?? [];
  const withoutOrders = forms?.filter((form) => form.to_order === 0) ?? [];

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Заказы поставщикам</h1>
          <p className="muted">Откройте поставщика, проверьте количества и скачайте файл заказа.</p>
        </div>
        <div className="chips" title="Прогноз считается по продажам выбранных салонов">
          {salons.map((name) => (
            <button key={name} className={`chip ${chosen.includes(name) ? "on" : ""}`} onClick={() => toggleSalon(name)}>
              {name}
            </button>
          ))}
        </div>
      </header>
      {notice && (
        <div className="success" onClick={() => setNotice("")}>
          {notice}
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {!forms && !error && <div className="muted">Считаем прогноз…</div>}
      {forms && (
        <>
          <div className="list">
            {withOrders.map((form) => (
              <SupplierOrder key={`${form.key}:${chosen.join(",")}:${round}`} summary={form} salons={chosen} user={user} onOrdered={ordered} />
            ))}
          </div>
          {withoutOrders.length > 0 && (
            <>
              <div className="list-title">Сейчас заказывать нечего</div>
              <div className="list">
                {withoutOrders.map((form) => (
                  <SupplierOrder key={`${form.key}:${chosen.join(",")}:${round}`} summary={form} salons={chosen} user={user} onOrdered={ordered} />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

function SupplierOrder({
  summary,
  salons,
  user,
  onOrdered,
}: {
  summary: FormSummary;
  salons: string[];
  user: User;
  onOrdered: (orderId: string) => void;
}) {
  const [detail, setDetail] = useState<FormDetail | null>(null);
  const [rows, setRows] = useState<OrderRow[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const canOrder = user.role === "admin";

  // A closed supplier costs nothing: its table is fetched the first time it opens.
  const open = (event: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!event.currentTarget.open || detail) return;
    api.form(summary.key, salons).then(
      (loaded) => {
        setDetail(loaded);
        // What to order comes first; the rest of the supplier's goods follow.
        const wanted = (row: OrderRow) => (row.quantity > 0 || row.edge_or_sheets > 0 ? 0 : 1);
        setRows([...loaded.rows].sort((a, b) => wanted(a) - wanted(b)));
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

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try {
      await action();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не получилось.");
    } finally {
      setBusy("");
    }
  };

  const download = () =>
    run("download", async () => {
      const { blob, filename } = await api.orderFile(summary.key, salons, rows);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
    });

  const chooseFile = (chosen: File | undefined) => {
    if (!chosen) return;
    setFile(chosen);
    setPreview(null);
    run("preview", async () => setPreview(await api.supplierFile(summary.key, salons, chosen, false)));
  };

  const confirmFile = () =>
    run("confirm", async () => {
      if (!file) return;
      const { order_id } = await api.supplierFile<{ order_id: string }>(summary.key, salons, file, true);
      onOrdered(order_id);
    });

  const asIs = () =>
    run("in-transit", async () => {
      if (!window.confirm(`Оформить заказ ${summary.title} в пути без изменений?`)) return;
      const { order_id } = await api.inTransit(summary.key, salons, rows);
      onOrdered(order_id);
    });

  const twoColumns = (detail?.layout.columns.length ?? 1) > 1;
  const amount = (quantity: number, edge: number) => (twoColumns ? `${quantity} / ${edge}` : `${edge}`);

  return (
    <details className="order" onToggle={open}>
      <summary>
        <span className="order-title">{summary.title}</span>
        {summary.to_order > 0 && <span className="badge warn">к заказу {summary.to_order}</span>}
        <span className="order-meta">придёт {dateLabel(summary.arrival)}</span>
      </summary>
      <div className="order-body">
        {error && <div className="error">{error}</div>}
        {!detail && !error && <div className="muted">Загружаем товары…</div>}
        {detail && detail.rows.length === 0 && <div className="muted">Сейчас нет товаров этого поставщика.</div>}

        {detail && detail.rows.length > 0 && !file && (
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
                    <th>Товар</th>
                    <th className="number">Есть и в пути</th>
                    <th className="number">Продаётся в мес.</th>
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
                      <td>
                        {row.name}
                        {row.code && <span className="code">{row.code}</span>}
                      </td>
                      <td className="number">{Math.round(row.available_stock_qty)}</td>
                      <td className="number">{Math.round(row.forecast_qty)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="action-bar">
              <span className="muted">
                {totals.lines} поз. ·{" "}
                {twoColumns ? `плит ${totals.quantity} шт · кромки ${totals.edge} м` : `всего ${totals.edge}${detail.layout.unit ? ` ${detail.layout.unit}` : ""}`}
              </span>
              <div className="actions">
                <button className="button primary" onClick={download} disabled={!!busy || totals.lines === 0}>
                  {busy === "download" ? "Собираем…" : "Скачать файл"}
                </button>
                {canOrder && (
                  <>
                    <button className="button" onClick={() => picker.current?.click()} disabled={!!busy}>
                      Загрузить файл поставщика
                    </button>
                    <button className="button" onClick={asIs} disabled={!!busy || totals.lines === 0} title="Поставщик подтвердил заказ без изменений">
                      В пути без изменений
                    </button>
                  </>
                )}
              </div>
            </div>
            <input
              ref={picker}
              type="file"
              accept=".xlsx"
              hidden
              onChange={(event) => {
                chooseFile(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
          </>
        )}

        {file && (
          <div className="file-check">
            <div className="file-check-head">
              <b>{file.name}</b>
              <button className="link-button" onClick={() => (setFile(null), setPreview(null))}>
                Отмена
              </button>
            </div>
            {busy === "preview" && <div className="muted">Читаем файл…</div>}
            {preview && preview.errors.map((message) => <div key={message} className="error">{message}</div>)}
            {preview && preview.errors.length === 0 && (
              <>
                <p className="muted">
                  В файле {preview.ordered_lines} поз. ·{" "}
                  {twoColumns
                    ? `плит ${preview.total_quantity} шт · кромки ${preview.total_edge_or_sheets} м`
                    : `всего ${preview.total_edge_or_sheets}`}
                  . Это будет новый заказ, прежние заказы не меняются.
                </p>
                {preview.already_saved && <div className="error">Этот файл уже оформлен как заказ.</div>}
                {preview.new_positions.length > 0 && (
                  <div className="warning">Нет среди товаров поставщика на сайте, войдут под названием из файла: {preview.new_positions.join(", ")}.</div>
                )}
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Товар</th>
                        <th className="number">Предложено</th>
                        <th className="number">В файле</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.lines.map((line, index) => (
                        <tr key={`${line.code}:${line.name}:${index}`} className={line.changed ? "changed" : ""}>
                          <td>
                            {line.name}
                            {line.code && <span className="code">{line.code}</span>}
                          </td>
                          <td className="number">{amount(line.proposed_quantity, line.proposed_edge_or_sheets)}</td>
                          <td className="number">{amount(line.quantity, line.edge_or_sheets)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="action-bar">
                  <span className="muted">Изменённые строки выделены.</span>
                  <button className="button primary" onClick={confirmFile} disabled={!!busy || preview.already_saved || preview.ordered_lines === 0}>
                    {busy === "confirm" ? "Оформляем…" : "Оформить заказ в пути"}
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </details>
  );
}
