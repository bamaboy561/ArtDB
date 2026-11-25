import { useEffect, useMemo, useRef, useState } from "react";
import { api, FilePreview, FormDetail, FormSummary, InboundOrder, OrderRow, User } from "../api";
import { Empty, Icon, initials, SalonPicker, SearchField, Segmented, Skeleton, Stat, tintOf, useFeedback } from "../ui";

export const dateLabel = (iso: string | null) => (iso ? iso.split("-").reverse().join(".") : "—");
const number = (value: number) => Math.round(value).toLocaleString("ru-RU");
const daysUntil = (iso: string) => Math.round((new Date(`${iso}T00:00:00`).getTime() - new Date().setHours(0, 0, 0, 0)) / 86_400_000);

type Filter = "wanted" | "all";

export default function Orders({ user }: { user: User }) {
  const [salons, setSalons] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [forms, setForms] = useState<FormSummary[] | null>(null);
  const [error, setError] = useState("");
  const [round, setRound] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("wanted");
  const [onTheWay, setOnTheWay] = useState<InboundOrder[] | null>(null);
  const { notify } = useFeedback();

  // What is already on the way belongs next to what to order: a late order changes the decision.
  useEffect(() => {
    api.inbound("active").then(setOnTheWay, () => setOnTheWay([]));
  }, [round]);

  useEffect(() => {
    api.salons().then((names) => {
      setSalons(names);
      setChosen(names);
    }, (failure) => setError(failure.message));
  }, []);

  useEffect(() => {
    if (!chosen.length) return;
    setForms(null);
    setError("");
    api.forms(chosen).then(setForms, (failure) => setError(failure.message));
  }, [chosen, round]);

  if (user.role === "salon") return <Empty icon="orders" title="Закупки доступны администратору и руководителю" />;

  const ordered = (orderId: string) => {
    notify(`Заказ #${orderId.slice(-4)} оформлен и теперь в разделе «В пути».`);
    setRound((value) => value + 1);
  };

  const withOrders = forms?.filter((form) => form.to_order > 0) ?? [];
  const nearest = withOrders.map((form) => form.arrival).filter(Boolean).sort()[0];
  const lateCount = onTheWay?.filter((order) => order.overdue).length ?? 0;
  const needle = query.trim().toLowerCase();
  const shown = (forms ?? [])
    .filter((form) => (filter === "wanted" ? form.to_order > 0 : true))
    .filter((form) => !needle || form.title.toLowerCase().includes(needle))
    .sort((a, b) => b.to_order - a.to_order || a.title.localeCompare(b.title, "ru"));

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Заказы поставщикам</h1>
          <p className="muted">Откройте поставщика, проверьте количества и скачайте файл заказа.</p>
        </div>
        {salons.length > 0 && <SalonPicker salons={salons} chosen={chosen} onChange={setChosen} />}
      </header>

      <div className="stats">
        <Stat icon="orders" label="Поставщиков к заказу" value={forms ? withOrders.length : "…"} hint={forms ? `из ${forms.length}` : undefined} tone="accent" />
        <Stat icon="box" label="Позиций к заказу" value={forms ? number(withOrders.reduce((sum, form) => sum + form.to_order, 0)) : "…"} />
        <Stat
          icon="calendar"
          label="Ближайший приход"
          value={nearest ? dateLabel(nearest) : "—"}
          hint={nearest ? `если заказать сегодня · ${Math.max(daysUntil(nearest), 0)} дн.` : undefined}
        />
        <a className="stat-link" href="#/inbound" title="Открыть «В пути»">
          <Stat
            icon="truck"
            label="Уже в пути"
            value={onTheWay ? onTheWay.length : "…"}
            hint={onTheWay ? (lateCount ? `просрочено ${lateCount} →` : "всё по графику →") : undefined}
            tone={lateCount ? "danger" : ""}
          />
        </a>
      </div>

      <div className="toolbar">
        <SearchField value={query} onChange={setQuery} placeholder="Найти поставщика" />
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          options={[
            { key: "wanted", label: "К заказу", count: forms ? withOrders.length : undefined },
            { key: "all", label: "Все", count: forms?.length },
          ]}
        />
      </div>

      {error && (
        <div className="alert error">
          <Icon name="alert" size={16} />
          {error}
        </div>
      )}
      {!forms && !error && (
        <div className="list">
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className="order ghost">
              <Skeleton rows={1} />
            </div>
          ))}
        </div>
      )}
      {forms && shown.length === 0 && (
        <Empty
          icon={needle ? "search" : "check"}
          title={needle ? "Ничего не нашлось" : "Сейчас заказывать нечего"}
          text={needle ? "Проверьте название поставщика." : "Остатков хватает до следующей поставки. Все поставщики — во вкладке «Все»."}
        />
      )}
      {forms && shown.length > 0 && (
        <div className="list">
          {shown.map((form) => (
            <SupplierOrder key={`${form.key}:${chosen.join(",")}:${round}`} summary={form} salons={chosen} user={user} onOrdered={ordered} />
          ))}
        </div>
      )}
    </section>
  );
}

function Stepper({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  return (
    <div className={`stepper ${value > 0 ? "on" : ""}`}>
      <button type="button" aria-label="Меньше" onClick={() => onChange(Math.max(0, value - 1))} tabIndex={-1}>
        <Icon name="minus" size={14} />
      </button>
      <input
        type="number"
        min={0}
        inputMode="numeric"
        value={value}
        onFocus={(event) => event.target.select()}
        onChange={(event) => onChange(Math.max(0, Math.round(Number(event.target.value) || 0)))}
      />
      <button type="button" aria-label="Больше" onClick={() => onChange(value + 1)} tabIndex={-1}>
        <Icon name="plus" size={14} />
      </button>
    </div>
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
  const [query, setQuery] = useState("");
  const [onlyWanted, setOnlyWanted] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const canOrder = user.role === "admin";
  const { confirm } = useFeedback();

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

  const setQuantity = (rowId: string, field: "quantity" | "edge_or_sheets", value: number) =>
    setRows((current) => current.map((row) => (row.row_id === rowId ? { ...row, [field]: value } : row)));

  const totals = useMemo(
    () => ({
      lines: rows.filter((row) => row.quantity > 0 || row.edge_or_sheets > 0).length,
      quantity: rows.reduce((sum, row) => sum + row.quantity, 0),
      edge: rows.reduce((sum, row) => sum + row.edge_or_sheets, 0),
    }),
    [rows],
  );

  const needle = query.trim().toLowerCase();
  const visibleRows = rows.filter(
    (row) =>
      (!onlyWanted || row.quantity > 0 || row.edge_or_sheets > 0) &&
      (!needle || row.name.toLowerCase().includes(needle) || row.code.toLowerCase().includes(needle)),
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

  const asIs = async () => {
    const yes = await confirm({
      title: `Заказ ${summary.title} в пути`,
      text: `Поставщик подтвердил заказ без изменений: ${totals.lines} поз. уйдут в раздел «В пути».`,
      action: "Оформить",
    });
    if (!yes) return;
    run("in-transit", async () => {
      const { order_id } = await api.inTransit(summary.key, salons, rows);
      onOrdered(order_id);
    });
  };

  const twoColumns = (detail?.layout.columns.length ?? 1) > 1;
  const amount = (quantity: number, edge: number) => (twoColumns ? `${quantity} / ${edge}` : `${edge}`);
  const leadDays = summary.lead_time_days;

  return (
    <details className="order" onToggle={open}>
      <summary>
        <span className={`supplier-avatar tint-${tintOf(summary.title)}`}>{initials(summary.title)}</span>
        <span className="order-heading">
          <span className="order-title">{summary.title}</span>
          <span className="order-sub">
            {summary.positions} поз. в ассортименте{leadDays ? ` · поставка ${leadDays} дн.` : ""}
          </span>
        </span>
        {summary.to_order > 0 ? <span className="pill warn">{summary.to_order} поз. к заказу</span> : <span className="pill quiet">хватает</span>}
        <span className="order-meta" title="Когда придёт, если заказать сегодня">
          <span className="order-when">
            <small>придёт</small>
            {dateLabel(summary.arrival)}
          </span>
        </span>
        <Icon name="chevron" size={16} className="order-chevron" />
      </summary>
      <div className="order-body">
        {error && (
          <div className="alert error">
            <Icon name="alert" size={16} />
            {error}
          </div>
        )}
        {!detail && !error && <Skeleton rows={5} />}
        {detail && detail.rows.length === 0 && <Empty icon="box" title="Сейчас нет товаров этого поставщика" />}

        {detail && detail.rows.length > 0 && !file && (
          <>
            <div className="toolbar compact">
              <SearchField value={query} onChange={setQuery} placeholder="Найти товар или код" />
              <label className="switch">
                <input type="checkbox" checked={onlyWanted} onChange={(event) => setOnlyWanted(event.target.checked)} />
                <span className="switch-track" />
                Только к заказу
              </label>
            </div>
            <div className="table-wrap">
              <table className="goods">
                <thead>
                  <tr>
                    <th>Товар</th>
                    {detail.layout.box_layout && <th className="number">В упаковке</th>}
                    <th className="number">Есть и в пути</th>
                    <th className="number">Продаётся в мес.</th>
                    {detail.layout.columns.map((column) => (
                      <th key={column.field} className="number">
                        {column.title}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row) => {
                    const wanted = row.quantity > 0 || row.edge_or_sheets > 0;
                    const short = row.forecast_qty > 0 && row.available_stock_qty < row.forecast_qty;
                    return (
                      <tr key={row.row_id} className={wanted ? "" : "idle"}>
                        <td className="goods-name">
                          <div className="product">
                            <span className="product-name">{row.name}</span>
                            <span className="product-tags">
                              {row.code && <span className="code">{row.code}</span>}
                              {row.abc_class && <span className={`tag abc-${row.abc_class.toLowerCase()}`}>{row.abc_class}</span>}
                            </span>
                          </div>
                        </td>
                        {detail.layout.box_layout && (
                          <td className="number goods-fact" data-label="в упаковке">
                            {row.pack || ""}
                          </td>
                        )}
                        <td className={`number goods-fact ${short ? "late" : ""}`} data-label="есть и в пути">
                          {number(row.available_stock_qty)}
                        </td>
                        <td className="number goods-fact" data-label="в месяц">
                          {number(row.forecast_qty)}
                        </td>
                        {detail.layout.columns.map((column) => (
                          <td key={column.field} className="number goods-qty" data-label={column.title}>
                            <Stepper value={row[column.field]} onChange={(value) => setQuantity(row.row_id, column.field, value)} />
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                  {visibleRows.length === 0 && (
                    <tr>
                      <td colSpan={9} className="muted center">
                        Ничего не нашлось.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="action-bar">
              <div className="totals">
                <span>
                  <b>{totals.lines}</b> поз.
                </span>
                {twoColumns ? (
                  <>
                    <span>
                      плит <b>{number(totals.quantity)}</b> шт
                    </span>
                    <span>
                      кромки <b>{number(totals.edge)}</b> м
                    </span>
                  </>
                ) : (
                  <span>
                    всего <b>{number(totals.edge)}</b>
                    {detail.layout.unit ? ` ${detail.layout.unit}` : ""}
                  </span>
                )}
              </div>
              <div className="actions">
                {canOrder && (
                  <>
                    <button
                      className="button ghost"
                      onClick={asIs}
                      disabled={!!busy || totals.lines === 0}
                      title="Поставщик подтвердил заказ как есть: он сразу уходит в «В пути», без файла"
                    >
                      <Icon name="truck" size={16} />
                      Оформить как есть
                    </button>
                    <button className="button" onClick={() => picker.current?.click()} disabled={!!busy}>
                      <Icon name="upload" size={16} />
                      Файл поставщика
                    </button>
                  </>
                )}
                <button className="button primary" onClick={download} disabled={!!busy || totals.lines === 0}>
                  <Icon name="download" size={16} />
                  {busy === "download" ? "Собираем…" : "Скачать заказ"}
                </button>
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
            <div className="file-chip">
              <Icon name="file" />
              <b>{file.name}</b>
              <button className="icon-button" aria-label="Убрать файл" onClick={() => (setFile(null), setPreview(null))}>
                <Icon name="close" size={16} />
              </button>
            </div>
            {busy === "preview" && <Skeleton rows={4} />}
            {preview &&
              preview.errors.map((message) => (
                <div key={message} className="alert error">
                  <Icon name="alert" size={16} />
                  {message}
                </div>
              ))}
            {preview && preview.errors.length === 0 && (
              <>
                <div className="totals">
                  <span>
                    В файле <b>{preview.ordered_lines}</b> поз.
                  </span>
                  {twoColumns ? (
                    <>
                      <span>
                        плит <b>{number(preview.total_quantity)}</b> шт
                      </span>
                      <span>
                        кромки <b>{number(preview.total_edge_or_sheets)}</b> м
                      </span>
                    </>
                  ) : (
                    <span>
                      всего <b>{number(preview.total_edge_or_sheets)}</b>
                    </span>
                  )}
                  <span className="muted">Это будет новый заказ, прежние не меняются.</span>
                </div>
                {preview.already_saved && (
                  <div className="alert error">
                    <Icon name="alert" size={16} />
                    Этот файл уже оформлен как заказ.
                  </div>
                )}
                {preview.new_positions.length > 0 && (
                  <div className="alert warning">
                    <Icon name="alert" size={16} />
                    Нет среди товаров поставщика на сайте, войдут под названием из файла: {preview.new_positions.join(", ")}.
                  </div>
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
                            <div className="product">
                              <span className="product-name">{line.name}</span>
                              {line.code && <span className="code">{line.code}</span>}
                            </div>
                          </td>
                          <td className="number muted">{amount(line.proposed_quantity, line.proposed_edge_or_sheets)}</td>
                          <td className="number">
                            <b>{amount(line.quantity, line.edge_or_sheets)}</b>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="action-bar">
                  <span className="muted">
                    <span className="legend changed" /> изменено поставщиком
                  </span>
                  <button className="button primary" onClick={confirmFile} disabled={!!busy || preview.already_saved || preview.ordered_lines === 0}>
                    <Icon name="check" size={16} />
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
