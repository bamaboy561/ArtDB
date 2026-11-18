import { useEffect, useState } from "react";
import { api, Arrival, InboundOrder, User } from "../api";
import { dateLabel } from "./Orders";

type View = "active" | "overdue" | "all" | "arrivals";

const VIEWS: { key: View; label: string }[] = [
  { key: "active", label: "Активные" },
  { key: "overdue", label: "Просроченные" },
  { key: "all", label: "Все" },
  { key: "arrivals", label: "История прихода" },
];

const amount = (value: number) => Math.round(value).toLocaleString("ru-RU");

function timing(order: InboundOrder): string {
  if (order.days_left === null) return order.status_label;
  if (order.days_left < 0) return `просрочен на ${-order.days_left} дн.`;
  if (order.days_left === 0) return "ожидается сегодня";
  return `через ${order.days_left} дн.`;
}

export default function Inbound({ user }: { user: User }) {
  const [view, setView] = useState<View>("active");
  const [orders, setOrders] = useState<InboundOrder[] | null>(null);
  const [arrivals, setArrivals] = useState<Arrival[] | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState("");
  const [round, setRound] = useState(0);

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

  if (user.role === "salon") return <div className="notice">Закупки доступны администратору и руководителю.</div>;

  const overdueCount = view === "active" ? orders?.filter((order) => order.overdue).length ?? 0 : 0;

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>В пути</h1>
          <p className="muted">Заказы, которые ещё не приехали целиком, и всё, что уже принято.</p>
        </div>
        <div className="chips">
          {VIEWS.map((item) => (
            <button key={item.key} className={`chip ${view === item.key ? "on" : ""}`} onClick={() => (setView(item.key), setSelected(""))}>
              {item.label}
            </button>
          ))}
        </div>
      </header>
      {error && <div className="error">{error}</div>}
      {overdueCount > 0 && (
        <div className="warning">
          Просрочено: {overdueCount}. Если товар приехал — примите заказ, если ещё едет — поставьте новую дату.
        </div>
      )}

      {view === "arrivals" ? (
        <ArrivalList arrivals={arrivals} />
      ) : (
        <div className={`split ${selected ? "with-card" : ""}`}>
          <div className="table-wrap tall">
            {!orders && !error && <div className="muted pad">Загружаем заказы…</div>}
            {orders && orders.length === 0 && <div className="muted pad">Здесь пусто.</div>}
            {orders && orders.length > 0 && (
              <table className="clickable">
                <thead>
                  <tr>
                    <th>Поставщик</th>
                    <th>Заказ от</th>
                    <th>Придёт</th>
                    <th>Принято</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((order) => (
                    <tr
                      key={order.order_id}
                      className={`${order.overdue ? "row-late" : ""} ${selected === order.order_id ? "picked" : ""}`}
                      onClick={() => setSelected(order.order_id)}
                    >
                      <td>
                        {order.supplier || "Без поставщика"}
                        <span className="code">#{order.order_id.slice(-4)}</span>
                      </td>
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
      <span>
        {amount(done)} / {amount(total)}
      </span>
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

  useEffect(() => {
    api.inboundOrder(orderId).then(
      (loaded) => {
        setOrder(loaded);
        setNewDate(loaded.expected_date ?? "");
      },
      (failure) => setError(failure.message),
    );
  }, [orderId]);

  const change = async (action: "receive" | "reschedule" | "cancel", question: string) => {
    if (!window.confirm(question)) return;
    setBusy(true);
    setError("");
    try {
      setOrder(await api.changeOrder(orderId, action, action === "reschedule" ? newDate : undefined));
      onChanged();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не получилось.");
    } finally {
      setBusy(false);
    }
  };

  const active = order && (order.status === "ordered" || order.status === "in_transit");

  return (
    <aside className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{order?.supplier || "Заказ"}</div>
          <div className="muted">
            #{orderId.slice(-4)} · {order?.status_label}
            {order && ` · заказ от ${dateLabel(order.order_date)}`}
          </div>
        </div>
        <button className="link-button" onClick={onClose}>
          Закрыть
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {!order && !error && <div className="muted">Загружаем…</div>}
      {order && (
        <>
          <div className="card-facts">
            <div>
              <span className="muted">Придёт</span>
              <b className={order.overdue ? "late" : ""}>{dateLabel(order.expected_date)}</b>
            </div>
            <div>
              <span className="muted">Принято</span>
              <b>
                {amount(order.received)} из {amount(order.ordered)}
              </b>
            </div>
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
                    <td className="number">{amount(item.remaining)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {canChange && active && (
            <div className="card-actions">
              <button
                className="button primary"
                disabled={busy}
                onClick={() => change("receive", "Принять остаток заказа? Товары будут зачислены на остаток.")}
              >
                Товар приехал — принять
              </button>
              <div className="inline">
                <input type="date" value={newDate} onChange={(event) => setNewDate(event.target.value)} />
                <button
                  className="button"
                  disabled={busy || !newDate || newDate === order.expected_date}
                  onClick={() => change("reschedule", `Перенести ожидаемую дату на ${dateLabel(newDate)}?`)}
                >
                  Новая дата
                </button>
              </div>
              <button className="link-button danger" disabled={busy} onClick={() => change("cancel", "Отменить заказ? Товар перестанет считаться в пути.")}>
                Отменить заказ
              </button>
            </div>
          )}
        </>
      )}
    </aside>
  );
}

function ArrivalList({ arrivals }: { arrivals: Arrival[] | null }) {
  if (!arrivals) return <div className="muted">Загружаем историю…</div>;
  if (arrivals.length === 0)
    return <div className="notice">Принятого прихода пока нет. Он появится после загрузки остатков или приёмки заказа.</div>;
  return (
    <div className="table-wrap tall">
      <table>
        <thead>
          <tr>
            <th>Когда</th>
            <th>Откуда</th>
            <th>Поставщик</th>
            <th>Товар</th>
            <th className="number">Принято</th>
          </tr>
        </thead>
        <tbody>
          {arrivals.map((arrival, index) => (
            <tr key={`${arrival.when}:${arrival.product}:${index}`}>
              <td>{arrival.when ? new Date(arrival.when).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—"}</td>
              <td>{arrival.source}</td>
              <td>
                {arrival.supplier}
                <span className="code">#{arrival.order_id.slice(-4)}</span>
              </td>
              <td>{arrival.product}</td>
              <td className="number">{amount(arrival.quantity)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
