import { useEffect, useMemo, useRef, useState } from "react";
import { api, FilePreview, FormDetail, FormSummary, InboundOrder, OrderRow, User } from "../api";
import { Empty, Icon, initials, SalonPicker, SearchField, Segmented, Skeleton, Stat, tintOf, useFeedback } from "../ui";

export const dateLabel = (iso: string | null) => (iso ? iso.split("-").reverse().join(".") : "—");
/** "09.12" this year, the full date otherwise. */
export const shortDate = (iso: string | null) =>
  iso && iso.startsWith(String(new Date().getFullYear())) ? dateLabel(iso).slice(0, 5) : dateLabel(iso);
const number = (value: number) => Math.round(value).toLocaleString("ru-RU");

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
  const needle = query.trim().toLowerCase();
  const shown = (forms ?? [])
    .filter((form) => (filter === "wanted" ? form.to_order > 0 : true))
    .filter((form) => !needle || form.title.toLowerCase().includes(needle))
    .sort((a, b) => urgent(b) - urgent(a) || b.to_order - a.to_order || a.title.localeCompare(b.title, "ru"));
  const today = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="muted eyebrow">
            {today[0].toUpperCase() + today.slice(1)}
            {chosen.length ? ` · прогноз по ${chosen.length} ${plural(chosen.length, ["салону", "салонам", "салонам"])}` : ""}
          </p>
          <h1>Что заказать сегодня</h1>
        </div>
        {salons.length > 0 && <SalonPicker salons={salons} chosen={chosen} onChange={setChosen} />}
      </header>

      <div className="stats">
        <Outlook forms={forms} />
        <Stat
          icon="box"
          label="Позиций к заказу"
          value={forms ? number(withOrders.reduce((sum, form) => sum + form.to_order, 0)) : "…"}
          hint={forms ? `у ${withOrders.length} ${plural(withOrders.length, ["поставщика", "поставщиков", "поставщиков"])}` : undefined}
        />
        <a className="stat-link" href="#/inbound" title="Открыть «В пути»">
          <OnTheWay orders={onTheWay} />
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
        <div className="suppliers">
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
        <div className="suppliers">
          <div className="suppliers-head" aria-hidden="true">
            <span>Поставщик</span>
            <span>Запас по позициям</span>
            <span className="number">К заказу</span>
            <span className="number">Придёт</span>
            <span />
          </div>
          {shown.map((form) => (
            <SupplierOrder key={`${form.key}:${chosen.join(",")}:${round}`} summary={form} salons={chosen} user={user} onOrdered={ordered} />
          ))}
        </div>
      )}
    </section>
  );
}

const urgent = (form: FormSummary) => form.stock.out + form.stock.short;

export function plural(count: number, [one, few, many]: [string, string, string]) {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return many;
  if (ones === 1) return one;
  return ones >= 2 && ones <= 4 ? few : many;
}

/** The main card: how many positions run out before an order placed today arrives. */
function Outlook({ forms }: { forms: FormSummary[] | null }) {
  if (!forms)
    return (
      <div className="outlook">
        <Skeleton rows={2} />
      </div>
    );
  const sum = (level: keyof FormSummary["stock"]) => forms.reduce((total, form) => total + form.stock[level], 0);
  const out = sum("out");
  const short = out + sum("short");
  const low = sum("low");
  const ok = sum("ok");
  const suppliers = forms.filter((form) => urgent(form) > 0).length;
  return (
    <div className="outlook">
      <span className="outlook-label">{short ? "Не хватит до прихода, если не заказать сегодня" : "До следующего прихода хватит всего"}</span>
      <div className="outlook-figure">
        {short ? (
          <>
            <b>{number(short)}</b>
            <span>
              {plural(short, ["позиция", "позиции", "позиций"])} у {suppliers} {plural(suppliers, ["поставщика", "поставщиков", "поставщиков"])}
              {out ? ` · ${number(out)} уже нет на складе` : ""}
            </span>
          </>
        ) : (
          <>
            <Icon name="check" size={30} />
            <span>ничего не закончится раньше поставки</span>
          </>
        )}
      </div>
      <StockBar short={short} low={low} ok={ok} />
      <div className="outlook-legend">
        <span>
          <i className="dot short" />
          не хватит — {number(short)}
        </span>
        <span>
          <i className="dot low" />
          стоит пополнить — {number(low)}
        </span>
        <span>
          <i className="dot ok" />
          хватает — {number(ok)}
        </span>
      </div>
    </div>
  );
}

function OnTheWay({ orders }: { orders: InboundOrder[] | null }) {
  const late = orders?.filter((order) => order.overdue).length ?? 0;
  const next = orders
    ?.map((order) => order.expected_date)
    .filter((when): when is string => !!when)
    .sort()[0];
  const hint = !orders ? undefined : late ? `просрочено ${late} →` : next ? `ближайший ${shortDate(next)} · по графику →` : "→";
  return (
    <Stat
      icon="truck"
      label="Уже в пути"
      value={orders ? `${orders.length} ${plural(orders.length, ["заказ", "заказа", "заказов"])}` : "…"}
      hint={hint}
      tone={late ? "danger" : orders?.length ? "good" : ""}
    />
  );
}

/** Red: runs out before the next delivery; amber: worth topping up; green: enough. */
function StockBar({ short, low, ok }: { short: number; low: number; ok: number }) {
  const total = short + low + ok;
  const width = (part: number) => `${total ? (part / total) * 100 : 0}%`;
  return (
    <div className="stock-bar" aria-hidden="true">
      <span className="short" style={{ width: width(short) }} />
      <span className="low" style={{ width: width(low) }} />
      <span className="ok" style={{ width: width(ok) }} />
    </div>
  );
}

function stockCaption(form: FormSummary) {
  const parts = [
    form.stock.out ? `${form.stock.out} нет на складе` : "",
    form.stock.short ? `${form.stock.short} не хватит до прихода` : "",
    form.stock.low ? `${form.stock.low} пополнить` : "",
    form.stock.ok ? `${form.stock.ok} хватает` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "нет продаж";
}

/** How many days the stock lasts; the mark is when an order placed today arrives. */
function Cover({ row, lead }: { row: OrderRow; lead: number }) {
  const scale = Math.max(60, lead * 2);
  const days = row.cover_days;
  const fill = days === null ? 0 : Math.min(100, (days / scale) * 100);
  const text =
    row.stock_level === "idle"
      ? "не продаётся"
      : row.stock_level === "out"
        ? "нет на складе"
        : days !== null && days >= scale
          ? `хватит больше чем на ${Math.round(scale / 30)} мес.`
          : `хватит на ${Math.round(days ?? 0)} дн.${row.stock_level === "short" ? " — кончится до прихода" : ""}`;
  return (
    <div className={`cover ${row.stock_level}`}>
      <div className="cover-track" aria-hidden="true">
        <span className="cover-fill" style={{ width: `${row.stock_level === "out" ? 0 : Math.max(fill, 2)}%` }} />
        {lead > 0 && <span className="cover-mark" style={{ left: `${(lead / scale) * 100}%` }} />}
      </div>
      <span className="cover-text">{text}</span>
    </div>
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
        const rank: Record<OrderRow["stock_level"], number> = { out: 0, short: 1, low: 2, ok: 3, idle: 4 };
        const wanted = (row: OrderRow) => (row.quantity > 0 || row.edge_or_sheets > 0 ? 0 : 1);
        setRows([...loaded.rows].sort((a, b) => wanted(a) - wanted(b) || rank[a.stock_level] - rank[b.stock_level]));
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
        <span className="supplier-name">
          <span className={`supplier-avatar tint-${tintOf(summary.title)}`}>{initials(summary.title)}</span>
          <span className="order-heading">
            <span className="order-title">{summary.title}</span>
            <span className="order-sub">
              {summary.positions} поз. в ассортименте{leadDays ? ` · поставка ${leadDays} дн.` : ""}
            </span>
          </span>
        </span>
        <span className="supplier-stock">
          <StockBar short={urgent(summary)} low={summary.stock.low} ok={summary.stock.ok} />
          <span className={`supplier-stock-text ${urgent(summary) ? "late" : ""}`}>{stockCaption(summary)}</span>
        </span>
        <span className="supplier-order number">
          {summary.to_order > 0 ? (
            <>
              <b>{summary.to_order}</b> поз.
            </>
          ) : (
            <span className="muted">—</span>
          )}
        </span>
        <span className="supplier-when number" title="Когда придёт, если заказать сегодня">
          {shortDate(summary.arrival)}
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
                    <th className="cover-head">
                      Запас <span className="cover-hint">· черта — приход заказа, сделанного сегодня{leadDays ? ` (${leadDays} дн.)` : ""}</span>
                    </th>
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
                    return (
                      <tr key={row.row_id} className={wanted ? "" : "idle"}>
                        <td className="goods-name">
                          <div className="product">
                            <span className="product-name">{row.name}</span>
                            <span className="product-tags">
                              {row.code && <span className="code">{row.code}</span>}
                              {row.abc_class && <span className={`tag abc-${row.abc_class.toLowerCase()}`}>{row.abc_class}</span>}
                              <span className="code">
                                есть {number(row.available_stock_qty)} · продаётся {number(row.forecast_qty)} в мес.
                              </span>
                            </span>
                          </div>
                        </td>
                        {detail.layout.box_layout && (
                          <td className="number goods-fact" data-label="в упаковке">
                            {row.pack || ""}
                          </td>
                        )}
                        <td className="goods-cover">
                          <Cover row={row} lead={leadDays} />
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
