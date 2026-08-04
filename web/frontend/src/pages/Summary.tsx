/**
 * Сводка — экран, который открывают первым утром.
 *
 * Главное правило раздела осталось прежним: каждая цифра кликабельна и ведёт в
 * список, из которого посчитана. Ссылку считает сервер (`href` в ответе), здесь
 * она просто превращается в `<Link>` — чтобы «пять заказов» всегда можно было
 * увидеть поимённо.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { get } from "../api/client";
import { Head, Loading, LoadError } from "../components/ui";
import { usePageTitle } from "../lib/meta";

type Metric = {
  title: string;
  hint: string;
  revenue_text: string;
  live_text: string;
  cancelled: number;
  href: string;
};

type Attention = {
  title: string;
  hint: string;
  count: number;
  href: string;
  link_hint: string;
  urgent: boolean;
};

type ZeroStock = {
  product_id: number;
  title: string;
  variant: string;
  category: string | null;
};

type Dashboard = {
  shop_name: string;
  today: string;
  metrics: Metric[];
  attention: Attention[];
  zero_stock: ZeroStock[];
  zero_stock_total: number;
  zero_stock_limit: number;
};

export default function Summary() {
  usePageTitle("Сводка");
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["summary"],
    queryFn: ({ signal }) => get<Dashboard>("/api/summary", signal),
  });

  if (isPending) return <Loading />;
  if (error) return <LoadError error={error} onRetry={() => refetch()} />;

  return (
    <>
      <Head title="Сводка" lead={`${data.today} · ${data.shop_name}`} />

      <div className="metrics">
        {data.metrics.map((metric) => (
          <Link className="metric" key={metric.title} to={metric.href}>
            <span className="metric-title">{metric.title}</span>
            <span className="metric-value">{metric.revenue_text}</span>
            <span className="metric-sub">
              {metric.live_text}
              {metric.cancelled ? `, отменено ${metric.cancelled}` : ""}
            </span>
            <span className="metric-hint">{metric.hint}</span>
          </Link>
        ))}
      </div>

      {/* Две колонки: слева то, что делают руками (заказы, ждущие человека),
          справа — лента складских сообщений. Разделение по смыслу, а не ради
          вида: слева работа с клиентами, справа работа с товаром, и одно не
          должно оттеснять другое вниз страницы. */}
      <div className="summary-grid">
      <div className="card">
        <h2>Требует внимания</h2>
        {data.attention.length ? (
          /* Каждый пункт — плашка со «счётчиком» слева: цифра и есть ссылка,
             по ней попадают в отфильтрованный список, не целясь в подпись.
             Срочное отличается не только цветом цифры — у плашки свой фон,
             чтобы «ждут проверки оплаты» находилось взглядом, а не чтением. */
          <ul className="attention">
            {data.attention.map((row) => (
              <li key={row.title} className={row.urgent ? "urgent" : ""}>
                <Link className="attention-count" to={row.href}>
                  {row.count}
                </Link>
                <span className="attention-main">
                  <Link className="attention-title" to={row.href}>
                    {row.title}
                  </Link>
                  <span className="attention-hint">
                    {row.hint}
                    {row.link_hint ? ` · ${row.link_hint}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">Всё разобрано: ни одного заказа, который ждёт человека.</p>
        )}
      </div>

      {/* Складские сообщения. Каждая строка — отдельное «закончился такой-то
          размер», а не строчка таблицы: так это и читают — как уведомления,
          которые надо разобрать. Размер стоит на видном месте, потому что
          пополняют именно его, а не товар целиком. */}
      <div className="card stock-feed">
        <h2>
          Закончилось на складе
          {data.zero_stock_total ? <span className="badge">{data.zero_stock_total}</span> : null}
        </h2>
        {data.zero_stock.length ? (
          <>
            <ul className="alerts">
              {data.zero_stock.map((row) => (
                <li key={`${row.product_id}-${row.variant}`}>
                  <span className="alert-main">
                    <Link className="strong" to={`/products/${row.product_id}`}>
                      {row.title}
                    </Link>
                    {/* Коротко: в узкой колонке длинная подпись переносится на
                        вторую строку и ломает ровный ряд плашек с размерами. */}
                    <span className="muted small">
                      {row.category ? `${row.category} · ` : ""}нужно пополнить
                    </span>
                  </span>
                  <span className="alert-size">{row.variant || "один вариант"}</span>
                </li>
              ))}
            </ul>
            <p className="actions">
              <Link className="btn" to="/products/stock?status=active">
                Пополнить остатки
              </Link>
              {data.zero_stock_total > data.zero_stock_limit ? (
                <span className="muted small">
                  показаны {data.zero_stock_limit} из {data.zero_stock_total}
                </span>
              ) : null}
            </p>
          </>
        ) : (
          <p className="muted">Пусто: у всех товаров на витрине что-то есть на складе.</p>
        )}
      </div>
      </div>
    </>
  );
}
