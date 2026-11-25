import { Fragment, useEffect, useState } from "react";
import { api, Arrival, FormSummary, InboundOrder, User } from "../api";
import { Empty, Icon, initials, SearchField, Segmented, Skeleton, Stat, tintOf, useFeedback } from "../ui";
import { dateLabel, shortDate } from "./Orders";

type View = "schedule" | "active" | "overdue" | "all" | "arrivals";

const amount = (value: number) => Math.round(value).toLocaleString("ru-RU");

function timing(order: InboundOrder): string {
  if (order.days_left === null) return order.status_label;
  if (order.days_left < 0) return `просрочен на ${-order.days_left} дн.`;
  if (order.days_left === 0) return "ожидается сегодня";
  return `через ${order.days_left} дн.`;
}

function statusPill(order: InboundOrder) {
  if (order.overdue) return <span className="pill late">просрочен</span>;
  if (order.status === "received") return <span className="pill ok">принят</span>;
  if (order.status === "cancelled") return <span className="pill quiet">отменён</span>;
  if (order.received > 0) return <span className="pill info">частично</span>;
  return <span className="pill info">{order.status_label.toLowerCase()}</span>;
}

export default function Inbound({ user }: { user: User }) {
  const [view, setView] = useState<View>("schedule");
  const [planned, setPlanned] = useState<FormSummary[] | null>(null);
  const [orders, setOrders] = useState<InboundOrder[] | null>(null);
  const [active, setActive] = useState<InboundOrder[] | null>(null);
  const [arrivals, setArrivals] = useState<Arrival[] | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState("");
  const [round, setRound] = useState(0);
  const [query, setQuery] = useState("");

  // The figures on top always describe the active orders, whatever list is open.
  useEffect(() => {
    api.inbound("active").then(setActive, () => setActive([]));
  }, [round]);

  // The schedule also shows when the orders still to place would arrive if placed today.
  useEffect(() => {
    if (view !== "schedule" || planned) return;
    api
      .salons()
      .then((salons) => api.forms(salons))
      .then(setPlanned, () => setPlanned([]));
  }, [view, planned]);

  useEffect(() => {
    setError("");
    if (view === "schedule") return;
    if (view === "arrivals") {
      setArrivals(null);
      api.arrivals().then(setArrivals, (failure) => setError(failure.message));
    } else {
      setOrders(null);
      api.inbound(view).then(setOrders, (failure) => setError(failure.message));
    }
  }, [view, round]);

  if (user.role === "salon") return <Empty icon="truck" title="Закупки доступны администратору и руководителю" />;

  const overdue = active?.filter((order) => order.overdue).length ?? 0;
  const thisWeek = active?.filter((order) => order.days_left !== null && order.days_left >= 0 && order.days_left <= 7).length ?? 0;
  const ordered = active?.reduce((sum, order) => sum + order.ordered, 0) ?? 0;
  const received = active?.reduce((sum, order) => sum + order.received, 0) ?? 0;

  const needle = query.trim().toLowerCase();
  const shownOrders = (orders ?? []).filter((order) => !needle || order.supplier.toLowerCase().includes(needle) || order.order_id.toLowerCase().includes(needle));
  const shownArrivals = (arrivals ?? []).filter(
    (arrival) => !needle || arrival.supplier.toLowerCase().includes(needle) || arrival.product.toLowerCase().includes(needle),
  );

  const choose = (next: View) => {
    setView(next);
    setSelected("");
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>В пути</h1>
          <p className="muted">Заказы, которые ещё не приехали целиком, и всё, что уже принято.</p>
        </div>
      </header>

      <div className="stats">
        <Stat icon="truck" label="Заказов в пути" value={active ? active.length : "…"} tone="accent" />
        <Stat icon="alert" label="Просрочено" value={active ? overdue : "…"} tone={overdue ? "danger" : ""} hint={overdue ? "примите или перенесите дату" : "всё по графику"} />
        <Stat icon="calendar" label="Приедут за 7 дней" value={active ? thisWeek : "…"} />
        <Stat
          icon="box"
          label="Уже принято"
          value={active ? (ordered ? `${Math.round((received / ordered) * 100)}%` : "—") : "…"}
          hint={active && ordered ? `${amount(received)} из ${amount(ordered)}` : undefined}
        />
      </div>

      <div className="toolbar">
        <SearchField value={query} onChange={setQuery} placeholder={view === "arrivals" ? "Поставщик или товар" : "Поставщик или номер заказа"} />
        <Segmented<View>
          value={view}
          onChange={choose}
          options={[
            { key: "schedule", label: "График" },
            { key: "active", label: "Активные", count: active?.length },
            { key: "overdue", label: "Просроченные", count: active ? overdue : undefined },
            { key: "all", label: "Все" },
            { key: "arrivals", label: "История прихода" },
          ]}
        />
      </div>

      {error && (
        <div className="alert error">
          <Icon name="alert" size={16} />
          {error}
        </div>
      )}

      {view === "arrivals" ? (
        <ArrivalList arrivals={arrivals ? shownArrivals : null} />
      ) : (
        <div className={`split ${selected ? "with-card" : ""}`}>
          {view === "schedule" ? (
            <Schedule
              orders={active?.filter((order) => !needle || order.supplier.toLowerCase().includes(needle)) ?? null}
              planned={planned?.filter((form) => form.to_order > 0 && (!needle || form.title.toLowerCase().includes(needle))) ?? null}
              selected={selected}
              onPick={setSelected}
            />
          ) : (
          <div className="panel">
            {!orders && !error && <Skeleton rows={6} />}
            {orders && shownOrders.length === 0 && (
              <Empty
                icon={needle ? "search" : "check"}
                title={needle ? "Ничего не нашлось" : view === "overdue" ? "Просроченных заказов нет" : "Здесь пусто"}
              />
            )}
            {orders && shownOrders.length > 0 && (
              <div className="table-wrap tall flat">
                <table className="clickable">
                  <thead>
                    <tr>
                      <th>Поставщик</th>
                      <th>Статус</th>
                      <th>Заказ от</th>
                      <th>Придёт</th>
                      <th>Принято</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shownOrders.map((order) => (
                      <tr
                        key={order.order_id}
                        className={`${order.overdue ? "row-late" : ""} ${selected === order.order_id ? "picked" : ""}`}
                        onClick={() => setSelected(order.order_id)}
                      >
                        <td>
                          <div className="supplier-cell">
                            <span className={`supplier-avatar small tint-${tintOf(order.supplier || "?")}`}>{initials(order.supplier || "?")}</span>
                            <div className="product">
                              <span className="product-name">{order.supplier || "Без поставщика"}</span>
                              <span className="code">#{order.order_id.slice(-4)}</span>
                            </div>
                          </div>
                        </td>
                        <td>{statusPill(order)}</td>
                        <td>{dateLabel(order.order_date)}</td>
                        <td>
                          {dateLabel(order.expected_date)}
                          <span className={`timing ${order.overdue ? "late" : ""}`}>{timing(order)}</span>
                        </td>
                        <td>
                          <Progress done={order.received} total={order.ordered} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          )}
          {selected && (
            <OrderCard
              key={`${selected}:${round}`}
              orderId={selected}
              canChange={user.role === "admin"}
              onClose={() => setSelected("")}
              onChanged={() => setRound((value) => value + 1)}
            />
          )}
        </div>
      )}
    </section>
  );
}

const WEEKS = 5;
const DAY = 86_400_000;
const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const atMidnight = (iso: string) => new Date(`${iso}T00:00:00`).getTime();

function weekLabel(start: Date) {
  const end = new Date(start.getTime() + 6 * DAY);
  return start.getMonth() === end.getMonth()
    ? `${start.getDate()}–${end.getDate()} ${MONTHS[end.getMonth()]}`
    : `${start.getDate()} ${MONTHS[start.getMonth()]} – ${end.getDate()} ${MONTHS[end.getMonth()]}`;
}

type Lane = {
  key: string;
  title: string;
  sub: string;
  kind: "on" | "late" | "plan";
  from: number;
  to: number;
  label: string;
  orderId?: string;
};

/** Orders as bars over five weeks: what is on the way, what is late, and what an order placed today would bring. */
function Schedule({
  orders,
  planned,
  selected,
  onPick,
}: {
  orders: InboundOrder[] | null;
  planned: FormSummary[] | null;
  selected: string;
  onPick: (orderId: string) => void;
}) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const monday = new Date(today.getTime() - ((today.getDay() + 6) % 7) * DAY);
  const start = monday.getTime();
  const span = WEEKS * 7 * DAY;
  const place = (time: number) => Math.min(Math.max(((time - start) / span) * 100, 0), 100);
  const now = today.getTime();

  if (!orders || !planned)
    return (
      <div className="panel">
        <Skeleton rows={6} />
      </div>
    );

  const lanes: Lane[] = [
    ...[...orders]
      .sort((a, b) => (a.expected_date ?? "9").localeCompare(b.expected_date ?? "9"))
      .map((order): Lane => {
        const expected = order.expected_date ? atMidnight(order.expected_date) : now;
        const ordered = order.order_date ? atMidnight(order.order_date) : now;
        return {
          key: order.order_id,
          orderId: order.order_id,
          title: `${order.supplier || "Без поставщика"} · #${order.order_id.slice(-4)}`,
          sub: order.overdue ? `опаздывает на ${-(order.days_left ?? 0)} дн.` : `в пути · заказ от ${shortDate(order.order_date)}`,
          kind: order.overdue ? "late" : "on",
          from: ordered,
          to: order.overdue ? now + DAY : expected,
          label: order.overdue ? `ждали ${shortDate(order.expected_date)}` : shortDate(order.expected_date),
        };
      }),
    ...[...planned]
      .sort((a, b) => a.arrival.localeCompare(b.arrival) || b.to_order - a.to_order)
      .map(
        (form): Lane => ({
          key: `plan:${form.key}`,
          title: `${form.title} · ${form.to_order} поз.`,
          sub: `поставка ${form.lead_time_days} дн.`,
          kind: "plan",
          from: now,
          to: atMidnight(form.arrival),
          label: shortDate(form.arrival),
        }),
      ),
  ];

  const weeks = Array.from({ length: WEEKS }, (_, index) => weekLabel(new Date(start + index * 7 * DAY)));
  const todayAt = place(now);

  return (
    <div className="panel schedule">
      {lanes.length === 0 ? (
        <Empty icon="check" title="Ничего не едет и заказывать нечего" />
      ) : (
        <div className="schedule-scroll">
          <div className="schedule-grid">
            <div className="schedule-row schedule-head">
              <span className="schedule-name">Заказ</span>
              <div className="schedule-weeks">
                {weeks.map((label) => (
                  <span key={label}>{label}</span>
                ))}
              </div>
            </div>
            {lanes.map((lane, index) => {
              const left = place(lane.from);
              const width = Math.max(place(lane.to) - left, 3);
              const content = (
                <>
                  <span className="schedule-name">
                    <b>{lane.title}</b>
                    <span className={`schedule-sub ${lane.kind}`}>{lane.sub}</span>
                  </span>
                  <span className="schedule-track">
                    <span className="schedule-today" style={{ left: `${todayAt}%` }} />
                    <span className={`schedule-bar ${lane.kind}`} style={{ left: `${left}%`, width: `${width}%` }}>
                      <span>{lane.label}</span>
                    </span>
                  </span>
                </>
              );
              const heading =
                index === 0 || lanes[index - 1].orderId !== undefined
                  ? lane.orderId
                    ? index === 0 && "В пути"
                    : "Ещё не заказано — когда придёт, если заказать сегодня"
                  : false;
              const row = lane.orderId ? (
                <button
                  key={lane.key}
                  type="button"
                  className={`schedule-row ${selected === lane.orderId ? "picked" : ""}`}
                  onClick={() => onPick(lane.orderId!)}
                >
                  {content}
                </button>
              ) : (
                <a key={lane.key} className="schedule-row" href="#/orders" title="Открыть заказы поставщикам">
                  {content}
                </a>
              );
              return heading ? (
                <Fragment key={lane.key}>
                  <div className="schedule-divider">{heading}</div>
                  {row}
                </Fragment>
              ) : (
                row
              );
            })}
          </div>
        </div>
      )}
      <div className="schedule-legend">
        <span>
          <i className="schedule-key today" />
          сегодня, {today.getDate()} {MONTHS[today.getMonth()]}
        </span>
        <span>
          <i className="schedule-key on" />в пути, по графику
        </span>
        <span>
          <i className="schedule-key plan" />
          если заказать сегодня
        </span>
        <span>
          <i className="schedule-key late" />
          опаздывает
        </span>
      </div>
    </div>
  );
}

function Progress({ done, total }: { done: number; total: number }) {
  const share = total > 0 ? Math.min(done / total, 1) : 0;
  return (
    <div className="progress" title={`${amount(done)} из ${amount(total)}`}>
      <div className="progress-bar">
        <div style={{ width: `${share * 100}%` }} />
      </div>
      <span>{Math.round(share * 100)}%</span>
    </div>
  );
}

function OrderCard({
  orderId,
  canChange,
  onClose,
  onChanged,
}: {
  orderId: string;
  canChange: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [order, setOrder] = useState<InboundOrder | null>(null);
  const [newDate, setNewDate] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { confirm, notify } = useFeedback();

  useEffect(() => {
    api.inboundOrder(orderId).then(
      (loaded) => {
        setOrder(loaded);
        setNewDate(loaded.expected_date ?? "");
      },
      (failure) => setError(failure.message),
    );
  }, [orderId]);

  const QUESTIONS = {
    receive: { title: "Принять заказ?", text: "Остаток заказа будет зачислен на склад.", action: "Принять", done: "Заказ принят." },
    reschedule: { title: "Перенести дату?", text: `Новая ожидаемая дата — ${dateLabel(newDate)}.`, action: "Перенести", done: "Дата перенесена." },
    cancel: { title: "Отменить заказ?", text: "Товар перестанет считаться в пути. Это нельзя отменить.", action: "Отменить заказ", done: "Заказ отменён.", danger: true },
  } as const;

  const change = async (action: keyof typeof QUESTIONS) => {
    const question = QUESTIONS[action];
    if (!(await confirm(question))) return;
    setBusy(true);
    setError("");
    try {
      setOrder(await api.changeOrder(orderId, action, action === "reschedule" ? newDate : undefined));
      notify(question.done);
      onChanged();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не получилось.");
    } finally {
      setBusy(false);
    }
  };

  const isActive = order && (order.status === "ordered" || order.status === "in_transit");
  const share = order && order.ordered > 0 ? Math.min(order.received / order.ordered, 1) : 0;

  return (
    <>
      <div className="sheet-backdrop" onClick={onClose} />
      <aside className="card sheet">
        <div className="card-head">
          <div className="supplier-cell">
            <span className={`supplier-avatar tint-${tintOf(order?.supplier || "?")}`}>{initials(order?.supplier || "?")}</span>
            <div>
              <div className="card-title">{order?.supplier || "Заказ"}</div>
              <div className="muted small">
                #{orderId.slice(-4)}
                {order && ` · заказ от ${dateLabel(order.order_date)}`}
              </div>
            </div>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>
        {error && (
          <div className="alert error">
            <Icon name="alert" size={16} />
            {error}
          </div>
        )}
        {!order && !error && <Skeleton rows={5} />}
        {order && (
          <>
            <div className="card-facts">
              <div>
                <span className="muted small">Придёт</span>
                <b className={order.overdue ? "late" : ""}>{dateLabel(order.expected_date)}</b>
                <span className={`timing ${order.overdue ? "late" : ""}`}>{timing(order)}</span>
              </div>
              <div>
                <span className="muted small">Принято</span>
                <b>{Math.round(share * 100)}%</b>
                <span className="timing">
                  {amount(order.received)} из {amount(order.ordered)}
                </span>
              </div>
            </div>
            <div className="progress-bar wide">
              <div style={{ width: `${share * 100}%` }} />
            </div>
            {order.comment && <p className="muted small">{order.comment}</p>}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Товар</th>
                    <th className="number">Заказано</th>
                    <th className="number">Принято</th>
                    <th className="number">Осталось</th>
                  </tr>
                </thead>
                <tbody>
                  {order.items?.map((item, index) => (
                    <tr key={`${item.product}:${index}`} className={item.remaining > 0 ? "" : "idle"}>
                      <td>{item.product}</td>
                      <td className="number">{amount(item.quantity)}</td>
                      <td className="number">{amount(item.received)}</td>
                      <td className="number">{item.remaining > 0 ? <b>{amount(item.remaining)}</b> : <Icon name="check" size={14} className="ok" />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {canChange && isActive && (
              <div className="card-actions">
                <button className="button primary large" disabled={busy} onClick={() => change("receive")}>
                  <Icon name="check" size={16} />
                  Товар приехал — принять
                </button>
                <div className="inline">
                  <input type="date" value={newDate} onChange={(event) => setNewDate(event.target.value)} aria-label="Новая дата" />
                  <button className="button" disabled={busy || !newDate || newDate === order.expected_date} onClick={() => change("reschedule")}>
                    <Icon name="calendar" size={16} />
                    Перенести
                  </button>
                </div>
                <button className="link-button danger" disabled={busy} onClick={() => change("cancel")}>
                  Отменить заказ
                </button>
              </div>
            )}
          </>
        )}
      </aside>
    </>
  );
}

const dayOf = (when: string | null) => (when ? new Date(when).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) : "Без даты");

function ArrivalList({ arrivals }: { arrivals: Arrival[] | null }) {
  if (!arrivals) return <div className="panel"><Skeleton rows={6} /></div>;
  if (arrivals.length === 0)
    return <Empty icon="history" title="Принятого прихода пока нет" text="Он появится после загрузки остатков или приёмки заказа." />;

  // One block per day, newest first, as the journal gives them.
  const days: { day: string; items: Arrival[] }[] = [];
  for (const arrival of arrivals) {
    const day = dayOf(arrival.when);
    const last = days[days.length - 1];
    if (last && last.day === day) last.items.push(arrival);
    else days.push({ day, items: [arrival] });
  }

  return (
    <div className="timeline">
      {days.map(({ day, items }) => (
        <div key={day} className="timeline-day">
          <div className="timeline-head">
            <span className="timeline-dot" />
            <b>{day}</b>
            <span className="muted small">
              {items.length} поз. · {amount(items.reduce((sum, item) => sum + item.quantity, 0))} шт
            </span>
          </div>
          <div className="table-wrap flat">
            <table>
              <tbody>
                {items.map((arrival, index) => (
                  <tr key={`${arrival.when}:${arrival.product}:${index}`}>
                    <td className="muted nowrap">{arrival.when ? new Date(arrival.when).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "—"}</td>
                    <td>
                      <div className="product">
                        <span className="product-name">{arrival.product}</span>
                        <span className="code">
                          {arrival.supplier} · #{arrival.order_id.slice(-4)}
                        </span>
                      </div>
                    </td>
                    <td>
                      <span className="pill quiet">{arrival.source}</span>
                    </td>
                    <td className="number">
                      <b>+{amount(arrival.quantity)}</b>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
