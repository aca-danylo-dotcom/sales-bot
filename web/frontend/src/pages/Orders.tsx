/**
 * Список заказов.
 *
 * Фильтры живут в адресной строке, а не в состоянии компонента: менеджер
 * кидает коллеге ссылку на «всё, что ждёт проверки за прошлую неделю», и она
 * должна открыться такой же. По той же причине вкладки и страницы — обычные
 * ссылки, которые можно открыть в новой вкладке браузера.
 */
import { useEffect, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { get, query as buildQuery } from "../api/client";
import {
  EmptyState,
  Head,
  Loading,
  LoadError,
  Pager,
  Tag,
  stamp,
  useRowLink,
} from "../components/ui";
import { usePageTitle } from "../lib/meta";

type Row = {
  id: number;
  created_at: string;
  units_count: number;
  name: string | null;
  phone: string | null;
  city: string | null;
  ttn: string | null;
  np_branch: string | null;
  total_text: string;
  status: string;
  status_short: string;
  assignee: string | null;
};

type Tab = { value: string; title: string; count: number };

type OrdersPage = {
  orders: Row[];
  tabs: Tab[];
  total: number;
  page: number;
  pages: number;
};

/** Что из адреса относится к отбору. `page` живёт отдельно: он сбрасывается. */
function readFilters(params: URLSearchParams) {
  return {
    status: params.get("status") ?? "",
    q: params.get("q") ?? "",
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
  };
}

export default function Orders() {
  usePageTitle("Заказы");
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  const page = Number(params.get("page")) || 1;
  const rowLink = useRowLink();

  // Поля ввода — своё состояние: список перечитывается по кнопке «Показать»,
  // а не на каждую букву. Возврат «назад» меняет адрес, поэтому поля
  // подтягиваются за ним.
  const [search, setSearch] = useState(filters.q);
  const [from, setFrom] = useState(filters.from);
  const [to, setTo] = useState(filters.to);
  useEffect(() => {
    setSearch(filters.q);
    setFrom(filters.from);
    setTo(filters.to);
  }, [filters.q, filters.from, filters.to]);

  const listQuery = buildQuery({ ...filters, page: page > 1 ? page : "" });
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["orders", listQuery],
    queryFn: ({ signal }) => get<OrdersPage>(`/api/orders${listQuery}`, signal),
    // Старые строки остаются на экране, пока едут новые: иначе список мигает
    // пустотой при каждом переключении вкладки.
    placeholderData: keepPreviousData,
  });

  /** Адрес списка с другими параметрами. Пустые поля в адрес не пишем. */
  const listHref = (next: Partial<Record<string, string | number>>) =>
    `/orders${buildQuery({ ...filters, page: page > 1 ? page : "", ...next })}`;

  const applyFilters = (event: React.FormEvent) => {
    event.preventDefault();
    // Новый отбор — всегда с первой страницы: на пятой его результатов может
    // просто не быть.
    setParams(
      new URLSearchParams(
        buildQuery({ status: filters.status, q: search, from, to }).replace("?", "")
      )
    );
  };

  const hasFilters = Boolean(filters.q || filters.from || filters.to);

  if (isPending) return <Loading />;
  if (error) return <LoadError error={error} onRetry={() => refetch()} />;

  return (
    <>
      <Head title="Заказы" lead={`Найдено: ${data.total}`} />

      {/* Цифра на вкладке означает «сколько окажется в списке, если нажать»:
          она посчитана с тем же поиском и периодом, но без фильтра статуса. */}
      <nav className="tabs">
        {data.tabs.map((tab) => (
          <Link
            key={tab.value || "all"}
            to={listHref({ status: tab.value, page: "" })}
            className={`tab ${tab.value === filters.status ? "active" : ""}`}
          >
            {tab.title}
            <span className="count">{tab.count}</span>
          </Link>
        ))}
      </nav>

      <form className="filters card" onSubmit={applyFilters}>
        <input
          type="search"
          value={search}
          autoComplete="off"
          placeholder="Имя, телефон, накладная или номер заказа"
          onChange={(event) => setSearch(event.target.value)}
        />
        <label className="inline">
          <span>с</span>
          <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
        </label>
        <label className="inline">
          <span>по</span>
          <input type="date" value={to} onChange={(event) => setTo(event.target.value)} />
        </label>
        <button className="btn" type="submit">
          Показать
        </button>
        {hasFilters ? (
          <Link className="btn ghost" to={`/orders${buildQuery({ status: filters.status })}`}>
            Сбросить
          </Link>
        ) : null}
      </form>

      {data.orders.length ? (
        <>
          <div className="card table-wrap">
            <table className="data-grid">
              <thead>
                <tr>
                  <th>Заказ</th>
                  <th>Получатель</th>
                  <th>Доставка</th>
                  <th className="num">Сумма</th>
                  <th>Статус</th>
                  <th>Ведёт</th>
                </tr>
              </thead>
              <tbody>
                {data.orders.map((order) => (
                  <tr
                    key={order.id}
                    {...rowLink(
                      `/orders/${order.id}${buildQuery({ ...filters, page: page > 1 ? page : "" })}`,
                    )}
                  >
                    <td>
                      {/* Фильтры едут в карточку, чтобы «← Все заказы» вернул
                          в тот же список, а не в начало. */}
                      <Link className="strong" to={`/orders/${order.id}${buildQuery({ ...filters, page: page > 1 ? page : "" })}`}>
                        №{order.id}
                      </Link>
                      <span className="muted small">
                        {stamp(order.created_at)} · {order.units_count} шт
                      </span>
                    </td>
                    <td>
                      {order.name || "—"}
                      <span className="muted small">{order.phone || ""}</span>
                    </td>
                    <td>
                      {order.city || "—"}
                      <span className="muted small">
                        {order.ttn ? `ТТН ${order.ttn}` : order.np_branch || ""}
                      </span>
                    </td>
                    <td className="num">{order.total_text}</td>
                    <td>
                      <Tag kind={`st-${order.status}`}>{order.status_short}</Tag>
                    </td>
                    <td className="muted small">{order.assignee || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Pager page={data.page} pages={data.pages} to={(next) => listHref({ page: next })} />
        </>
      ) : (
        <EmptyState
          title={
            filters.status || hasFilters
              ? "Под фильтр ничего не подошло."
              : "Заказов пока нет."
          }
          hint="Заказы создаются в боте — здесь их подтверждают, отправляют и закрывают."
        />
      )}
    </>
  );
}
