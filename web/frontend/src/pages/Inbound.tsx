import { useEffect, useState } from "react";
import { api, Arrival, InboundOrder, User } from "../api";
import { Empty, Icon, initials, SearchField, Segmented, Skeleton, Stat, tintOf, useFeedback } from "../ui";
import { dateLabel } from "./Orders";

type View = "active" | "overdue" | "all" | "arrivals";

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
  const [view, setView] = useState<View>("active");
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

  useEffect(() => {
    setError("");
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
