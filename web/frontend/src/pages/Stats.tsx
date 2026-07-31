/**
 * Статистика — экран для вопроса «что вообще происходит с магазином».
 *
 * От «Сводки» отличается не подробностью, а вопросом. Сводка — про сегодня и
 * про то, за что взяться сейчас; здесь смотрят на отрезок времени: растём или
 * падаем, что покупают, где теряются заказы, окупается ли бот.
 *
 * Период живёт в адресной строке, как фильтры в «Заказах»: ссылку на «прошлый
 * месяц» можно отправить коллеге, и он увидит те же числа.
 *
 * Все суммы приходят с сервера готовыми строками. Считать деньги на клиенте
 * нельзя: «2 400 грн» в панели, в отчёте и в сообщении бота должны совпадать
 * до знака.
 */
import { useEffect, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { get, query as buildQuery } from "../api/client";
import { Head, Loading, LoadError, stamp } from "../components/ui";
import { NumberTicker } from "../components/number-ticker";
import { BarChart } from "../components/charts/bar-chart";
import { Bar } from "../components/charts/bar";
import { BarXAxis } from "../components/charts/bar-x-axis";
import { BarYAxis } from "../components/charts/bar-y-axis";
import { Grid } from "../components/charts/grid";
import { ChartTooltip } from "../components/charts/tooltip";
import { FunnelChart } from "../components/charts/funnel-chart";
import { PieChart } from "../components/charts/pie-chart";
import { PieSlice } from "../components/charts/pie-slice";
import { PieCenter } from "../components/charts/pie-center";
import {
  Legend,
  LegendItem,
  LegendLabel,
  LegendMarker,
  LegendValue,
} from "../components/charts/legend";
import { usePageTitle } from "../lib/meta";

type Day = {
  day: string;
  label: string;
  orders: number;
  revenue: number;
  revenue_text: string;
};

type TopRow = {
  title: string;
  product_id: number | null;
  units: number;
  orders: number;
  revenue: number;
  revenue_text: string;
  share: number;
};

type Stats = {
  date_from: string;
  date_to: string;
  days: number;
  today: string;
  sales: {
    placed: number;
    placed_text: string;
    revenue_text: string;
    paid_revenue_text: string;
    cancelled: number;
    cancelled_share: number;
    average_text: string;
  };
  by_day: Day[];
  funnel: {
    steps: { label: string; value: number; share: number; display: string }[];
    cancelled: number;
    cancelled_text: string;
  };
  delivery: { shipped: number; done: number; avg_hours_text: string };
  cancelled_orders: {
    id: number;
    name: string;
    total_text: string;
    note: string;
    created_at: string;
  }[];
  clients: {
    served_total: number;
    talked: number;
    new_clients: number;
    buyers: number;
    repeat_buyers: number;
    conversion: number;
  };
  products: {
    top: TopRow[];
    total_revenue_text: string;
    total_units: number;
    titles: number;
    idle: { id: number; title: string; category: string | null; price_text: string; stock: number }[];
    zero_stock: number;
  };
};

/** Готовые отрезки: ими меряют торговлю чаще всего. */
const PRESETS = [
  { days: 7, title: "7 дней" },
  { days: 30, title: "30 дней" },
  { days: 90, title: "90 дней" },
];

function shiftDays(from: string, days: number) {
  const date = new Date(`${from}T00:00:00`);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

export default function Stats() {
  usePageTitle("Статистика");
  const [params, setParams] = useSearchParams();
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";

  const listQuery = buildQuery({ from, to });
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["stats", listQuery],
    queryFn: ({ signal }) => get<Stats>(`/api/stats${listQuery}`, signal),
    placeholderData: keepPreviousData,
  });

  // Поля дат правятся руками, поэтому держим их отдельно и отправляем по
  // кнопке: иначе половина набранной даты уже уезжала бы в запрос.
  const [draft, setDraft] = useState({ from, to });
  useEffect(() => setDraft({ from: data?.date_from ?? from, to: data?.date_to ?? to }), [data, from, to]);

  // Круг и легенда подсвечиваются вместе: наводишь на строку — видно долю.
  const [hovered, setHovered] = useState<number | null>(null);

  if (isPending) return <Loading />;
  if (error) return <LoadError error={error} onRetry={() => refetch()} />;

  const { sales, funnel, delivery, clients, products } = data;

  const applyPreset = (days: number) => {
    const end = data.today;
    setParams(new URLSearchParams(buildQuery({ from: shiftDays(end, -(days - 1)), to: end }).replace("?", "")));
  };

  const ordersHref = (extra: Record<string, string> = {}) =>
    `/orders${buildQuery({ from: data.date_from, to: data.date_to, ...extra })}`;

  const slices = products.top.map((row, index) => ({
    label: row.title,
    value: row.revenue,
    // Восемь цветов палитры по кругу: девятая доля («остальные») получит
    // первый цвет снова — она и так подписана словом.
    color: `var(--chart-${(index % 8) + 1})`,
  }));

  return (
    <>
      <Head
        title="Статистика"
        lead={`${data.date_from} — ${data.date_to} · ${data.days} ${
          data.days === 1 ? "день" : data.days < 5 ? "дня" : "дней"
        }`}
      />

      <form
        className="filters card"
        onSubmit={(event) => {
          event.preventDefault();
          setParams(new URLSearchParams(buildQuery(draft).replace("?", "")));
        }}
      >
        <div className="period-presets">
          {PRESETS.map((preset) => (
            <button
              key={preset.days}
              type="button"
              className={`btn ghost ${data.days === preset.days ? "on" : ""}`}
              onClick={() => applyPreset(preset.days)}
            >
              {preset.title}
            </button>
          ))}
        </div>
        <label className="inline">
          <span>с</span>
          <input
            type="date"
            value={draft.from}
            onChange={(event) => setDraft({ ...draft, from: event.target.value })}
          />
        </label>
        <label className="inline">
          <span>по</span>
          <input
            type="date"
            value={draft.to}
            onChange={(event) => setDraft({ ...draft, to: event.target.value })}
          />
        </label>
        <button className="btn" type="submit">
          Показать
        </button>
      </form>

      {/* ─── Продажи ─── */}
      <section className="card">
        <h2>Продажи</h2>
        {/* Четыре числа, и все разные. «Оформлено» — сколько людей дошло до
            конца, «продано» — сколько денег подтвердили. Расхождение между ними
            не ошибка, а неоплаченные заказы, поэтому подписано словами. */}
        <div className="stat-row">
          <Link className="stat" to={ordersHref()}>
            <span className="stat-title">Оформлено</span>
            <span className="stat-value">{sales.revenue_text}</span>
            <span className="stat-sub">{sales.placed_text}</span>
          </Link>
          <Link className="stat" to={ordersHref({ status: "done" })}>
            <span className="stat-title">Продано</span>
            <span className="stat-value">{sales.paid_revenue_text}</span>
            <span className="stat-sub">оплата подтверждена</span>
          </Link>
          <Link className="stat" to={ordersHref({ status: "cancelled" })}>
            <span className="stat-title">Отменено</span>
            <span className="stat-value">{sales.cancelled}</span>
            <span className="stat-sub">{sales.cancelled_share}% от всех заказов</span>
          </Link>
          <div className="stat">
            <span className="stat-title">Средний чек</span>
            <span className="stat-value">{sales.average_text}</span>
            <span className="stat-sub">по оформленным заказам</span>
          </div>
        </div>

        {sales.placed || sales.cancelled ? (
          <div className="chart">
            <BarChart data={data.by_day} xDataKey="label" aspectRatio="3 / 1">
              <Grid horizontal />
              <Bar dataKey="revenue" fill="var(--chart-1)" />
              <BarXAxis maxLabels={10} />
              <BarYAxis />
              <ChartTooltip
                rows={(point) => [
                  {
                    label: "Выручка",
                    value: String(point.revenue_text ?? ""),
                    color: "var(--chart-1)",
                  },
                ]}
              />
            </BarChart>
          </div>
        ) : (
          <p className="muted">За этот период заказов не было.</p>
        )}
      </section>

      {/* ─── Путь заказа ─── */}
      <section className="card">
        <h2>Путь заказа</h2>
        <p className="muted small">
          Где заказы останавливаются. Ступени считаются по пройденному пути, а не
          по нынешнему статусу: заказ, который уже отправлен, засчитан и на
          прежних шагах.
        </p>
        {funnel.steps[0].value ? (
          <FunnelChart
            data={funnel.steps.map((step) => ({
              label: step.label,
              value: step.value,
              displayValue: step.display,
            }))}
            color="var(--chart-1)"
            layers={3}
            className="funnel"
          />
        ) : (
          <p className="muted">Заказов за период нет — рисовать нечего.</p>
        )}

        <div className="stat-row tight">
          <Link className="stat" to={ordersHref({ status: "shipped" })}>
            <span className="stat-title">Отправлено</span>
            <span className="stat-value">{delivery.shipped}</span>
            <span className="stat-sub">закрыто: {delivery.done}</span>
          </Link>
          <div className="stat">
            <span className="stat-title">Собираем в среднем</span>
            <span className="stat-value">{delivery.avg_hours_text}</span>
            <span className="stat-sub">от заказа до накладной</span>
          </div>
          <Link className="stat" to={ordersHref({ status: "cancelled" })}>
            <span className="stat-title">Потеряно на отменах</span>
            <span className="stat-value">{funnel.cancelled_text}</span>
            <span className="stat-sub">
              {funnel.cancelled} {funnel.cancelled === 1 ? "заказ" : "заказов"}
            </span>
          </Link>
        </div>

        {/* Причина отмены — внутренняя заметка менеджера. Она здесь затем,
            чтобы «отменено 7» превратилось в «семь раз не дозвонились». */}
        {data.cancelled_orders.length ? (
          <ul className="notes">
            {data.cancelled_orders.map((order) => (
              <li key={order.id}>
                <span className="text">
                  <Link to={`/orders/${order.id}`}>№{order.id}</Link> · {order.name} ·{" "}
                  {order.total_text}
                  {order.note ? ` — ${order.note}` : ""}
                </span>
                <span className="muted small">{stamp(order.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {/* ─── Клиенты ─── */}
      <section className="card">
        <h2>Клиенты и консультации</h2>
        <div className="served">
          {/* Число накопительное и от периода не зависит: «обслужено» — это
              итог работы бота, а не выработка за неделю. */}
          <NumberTicker value={clients.served_total} className="served-value" />
          <span className="served-label">
            человек поговорили с ботом за всё время
          </span>
        </div>

        <div className="stat-row tight">
          <div className="stat">
            <span className="stat-title">Писали за период</span>
            <span className="stat-value">{clients.talked}</span>
            <span className="stat-sub">новых: {clients.new_clients}</span>
          </div>
          <div className="stat">
            <span className="stat-title">Купили</span>
            <span className="stat-value">{clients.buyers}</span>
            <span className="stat-sub">из них повторно: {clients.repeat_buyers}</span>
          </div>
          <div className="stat">
            <span className="stat-title">Разговор → заказ</span>
            <span className="stat-value">{clients.conversion}%</span>
            <span className="stat-sub">сколько бесед закончились покупкой</span>
          </div>
        </div>
      </section>

      {/* ─── Товары ─── */}
      <section className="card">
        <h2>Что покупают</h2>
        {products.top.length ? (
          <div className="pie-row">
            <PieChart
              data={slices}
              size={260}
              innerRadius={78}
              padAngle={0.02}
              cornerRadius={6}
              hoveredIndex={hovered}
              onHoverChange={setHovered}
            >
              {slices.map((_, index) => (
                <PieSlice key={index} index={index} />
              ))}
              <PieCenter defaultLabel="Продано" />
            </PieChart>

            <Legend
              items={slices.map((slice, index) => ({
                label: slice.label,
                value: products.top[index].revenue,
                color: slice.color,
              }))}
              hoveredIndex={hovered}
              onHoverChange={setHovered}
              className="pie-legend"
            >
              <LegendItem>
                <LegendMarker />
                <LegendLabel />
                <LegendValue />
              </LegendItem>
            </Legend>
          </div>
        ) : (
          <p className="muted">За этот период ничего не продали.</p>
        )}

        {products.top.length ? (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Товар</th>
                  <th className="num">Штук</th>
                  <th className="num">Заказов</th>
                  <th className="num">Выручка</th>
                  <th className="num">Доля</th>
                </tr>
              </thead>
              <tbody>
                {products.top.map((row) => (
                  <tr key={row.title}>
                    <td>
                      {row.product_id ? (
                        <Link className="strong" to={`/products/${row.product_id}`}>
                          {row.title}
                        </Link>
                      ) : (
                        <span className="strong">{row.title}</span>
                      )}
                    </td>
                    <td className="num">{row.units}</td>
                    <td className="num">{row.orders || "—"}</td>
                    <td className="num">{row.revenue_text}</td>
                    <td className="num muted">{row.share}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="card">
        <h2>Лежит без движения</h2>
        <p className="muted small">
          Товары на витрине, которые за период не купили ни разу. Те, у которых
          просто кончился размер, сюда не попадают — их видно в «Остатках».
        </p>
        {products.idle.length ? (
          <ul className="zero-stock">
            {products.idle.map((row) => (
              <li key={row.id}>
                <Link to={`/products/${row.id}`}>{row.title}</Link>
                <span className="muted small">
                  {row.category || "без категории"} · {row.price_text} · на складе{" "}
                  {row.stock}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">Продавалось всё — ни одного залежавшегося товара.</p>
        )}
        {products.zero_stock ? (
          <p className="actions">
            <Link className="btn" to="/products/stock?status=active">
              Пополнить остатки
            </Link>
            <span className="muted small">
              закончилось размеров: {products.zero_stock}
            </span>
          </p>
        ) : null}
      </section>
    </>
  );
}
