import { useEffect, useState } from "react";
import type { OrderEvent, OrderOutcome } from "../api";
import { api, clock } from "../api";

const EVENT_TEXT: Record<string, string> = {
  arrived: "Order arrived",
  blocked_no_stock: "Blocked, not enough stock",
  stock_received: "Inbound stock received",
  stock_reserved: "Stock reserved",
  pick_start: "Picking started",
  pick_end: "Picking finished",
  pack_start: "Packing started",
  pack_end: "Packing finished",
  dispatched: "Dispatched",
  missed_cutoff: "Missed the dispatch cutoff",
  unfulfilled: "Not fulfilled during the day",
};

interface Props {
  experimentId: string | null;
  arm: "baseline" | "candidate";
}

export function OrderInspector({ experimentId, arm }: Props) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [orders, setOrders] = useState<OrderOutcome[]>([]);
  const [onlyLate, setOnlyLate] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<OrderEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
    setEvents([]);
    setPage(0);
    setOrders([]);
    if (!experimentId) {
      setOrders([]);
      return;
    }
    let cancelled = false;
    api
      .orders(experimentId, arm, onlyLate)
      .then((rows) => {
        if (!cancelled) {
          setOrders(rows);
          setError(null);
        }
      })
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [experimentId, arm, onlyLate]);

  useEffect(() => {
    if (!experimentId || !selected) {
      setEvents([]);
      return;
    }
    setEvents([]);
    let cancelled = false;
    api
      .orderTimeline(experimentId, selected, arm)
      .then((body) => !cancelled && setEvents(body.events))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [experimentId, selected, arm]);

  if (!experimentId) {
    return (
      <div className="panel">
        <h2>Order explanation</h2>
        <p className="empty">
          Run a baseline or comparison, then select an order to see where it
          waited.
        </p>
      </div>
    );
  }

  const filtered = orders.filter((o) =>
    o.order_id.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div className="panel">
      <p className="eyebrow">03 / Explain an outcome</p>
      <h2>Follow a single order</h2>
      <label className="field">
        Find an order
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
          placeholder="Search by order ID"
        />
      </label>
      <div className="head" style={{ marginBottom: 12 }}>
        <label>
          <input
            type="checkbox"
            checked={onlyLate}
            onChange={(e) => setOnlyLate(e.target.checked)}
          />{" "}
          Show only orders that missed their deadline
        </label>
      </div>

      {error && <p className="banner warning">{error}</p>}

      {orders.length === 0 ? (
        <p className="empty">
          {onlyLate
            ? "No order missed its deadline in the stored replication."
            : "No orders to show."}
        </p>
      ) : (
        <div className="table-scroll">
          <table className="data">
            <caption>
              Stored replication, {arm} lane. Select a row to see its event
              path.
            </caption>
            <thead>
              <tr>
                <th scope="col">Order</th>
                <th scope="col">Class</th>
                <th scope="col" className="num">
                  Arrived
                </th>
                <th scope="col" className="num">
                  Deadline
                </th>
                <th scope="col" className="num">
                  Dispatched
                </th>
                <th scope="col" className="num">
                  Waited
                </th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(page * 25, (page + 1) * 25).map((order) => (
                <tr
                  key={order.order_id}
                  className={selected === order.order_id ? "selected" : ""}
                  onClick={() => setSelected(order.order_id)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(order.order_id);
                    }
                  }}
                >
                  <th scope="row" style={{ fontWeight: 400 }}>
                    {order.order_id}
                  </th>
                  <td>{order.service_class}</td>
                  <td className="num">{clock(order.arrival_minute)}</td>
                  <td className="num">{clock(order.deadline_minute)}</td>
                  <td className="num">
                    {order.dispatched_minute === null ? (
                      <span className="not-run">not dispatched</span>
                    ) : (
                      clock(order.dispatched_minute)
                    )}
                  </td>
                  <td className="num">
                    {order.wait_minutes === null
                      ? ""
                      : `${order.wait_minutes.toFixed(1)} min`}
                  </td>
                  <td>
                    <span
                      className={`verdict ${order.is_late ? "regression" : "improvement"}`}
                    >
                      {order.is_late ? "late" : "on time"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="pagination">
        <button
          className="btn"
          disabled={page === 0}
          onClick={() => setPage(page - 1)}
        >
          Previous
        </button>
        <span>
          {filtered.length} matching orders / page {page + 1} of{" "}
          {Math.max(1, Math.ceil(filtered.length / 25))}
        </span>
        <button
          className="btn"
          disabled={(page + 1) * 25 >= filtered.length}
          onClick={() => setPage(page + 1)}
        >
          Next
        </button>
      </div>
      {selected && events.length > 0 && (
        <>
          <h3>Where {selected} waited</h3>
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col" className="num">
                    Time
                  </th>
                  <th scope="col">Event</th>
                  <th scope="col">Detail</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.sequence}>
                    <td className="num">{clock(event.minute)}</td>
                    <td>{EVENT_TEXT[event.event_type] ?? event.event_type}</td>
                    <td className="footnote">
                      {Object.entries(event.detail)
                        .map(([key, value]) => `${key}: ${value}`)
                        .join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
