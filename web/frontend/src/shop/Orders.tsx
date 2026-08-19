/**
 * Свои заказы: что купил, на какой стадии, номер накладной.
 *
 * Действий здесь нет намеренно. Оплата, отмена и вопросы по заказу живут в
 * чате — там кнопки под сообщением бота и там же ответит человек. Дублировать
 * их сюда значило бы вести один и тот же разговор в двух местах.
 */
import { useNavigate } from "react-router-dom";

import { EmptyState, LoadError, Loading } from "../components/ui";
import { useBackButton } from "../lib/telegram-ui";
import { useOrders } from "./api";

/* Цвет ярлыка по стадии заказа: ждём денег — нейтрально, едет и выполнен —
   зелёным, отменён — красным. Ярлыки те же, что в панели владельца. */
const TONE: Record<string, string> = {
  awaiting_payment: "",
  paid_claimed: "",
  confirmed: "ok",
  shipped: "ok",
  done: "ok",
  cancelled: "danger",
};

export default function Orders() {
  const navigate = useNavigate();
  const { data, isPending, error, refetch } = useOrders();

  useBackButton(() => navigate("/shop"));

  if (isPending) return <Loading />;
  if (error) return <LoadError error={error} onRetry={() => refetch()} />;

  if (!data?.orders.length) {
    return (
      <div className="shop-page">
        <EmptyState title="Заказов пока нет." hint="Всё, что купите, появится здесь." />
      </div>
    );
  }

  return (
    <div className="shop-page">
      {data.orders.map((order) => (
        <div key={order.id} className="shop-order">
          <div className="shop-order-head">
            <p className="shop-order-id">
              №{order.id}
              <span className="shop-order-date">{order.created_at.slice(0, 10)}</span>
            </p>
            <span className={`tag ${TONE[order.status] ?? ""}`}>{order.status_text}</span>
          </div>

          <ul className="shop-order-items">
            {order.items.map((item, index) => (
              <li key={`${order.id}-${index}`}>
                <span>
                  {item.title} — {item.label} × {item.qty}
                </span>
                <span className="muted">{item.sum_text}</span>
              </li>
            ))}
          </ul>

          {order.ttn ? (
            <p className="shop-order-ttn">
              Накладная: <b>{order.ttn}</b>
            </p>
          ) : null}
          <p className="shop-order-total">{order.total_text}</p>
        </div>
      ))}
    </div>
  );
}
