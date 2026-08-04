/**
 * Остатки по размерам — второй вид того же каталога.
 *
 * Ради этого экрана панель и заводили: подправить три десятка остатков после
 * ревизии в карточках товаров — работа на полчаса, здесь это одна таблица и
 * одна кнопка. Отбор общий со списком товаров, поэтому переключение вкладок
 * сохраняет фильтр.
 *
 * Страниц тут нет специально: пришли все варианты, подошедшие под отбор, —
 * править склад постранично неудобно, а сузить выборку можно фильтром.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { get, query as buildQuery } from "../api/client";
import { ProductFilters } from "../components/product-filters";
import { EmptyState, Head, Loading, LoadError } from "../components/ui";
import { useAction } from "../lib/actions";
import { usePageTitle } from "../lib/meta";
import { readFilters } from "./Products";

type Row = {
  id: number;
  product_id: number;
  title: string;
  category: string | null;
  is_active: number | boolean;
  main_photo_id: number | null;
  variant: string;
  price_text: string;
  stock: number;
};

type StockPage = { rows: Row[]; categories: string[] };

export default function Stock() {
  usePageTitle("Остатки");
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  const filtersQs = buildQuery(filters);

  const [draft, setDraft] = useState(filters);
  useEffect(() => {
    setDraft(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.q, filters.category, filters.status, filters.stock]);

  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["stock", filtersQs],
    queryFn: ({ signal }) => get<StockPage>(`/api/products/stock${filtersQs}`, signal),
  });

  // Значения полей — строками: пустое поле и «0» это разное, а число сделало бы
  // из одного другое ещё до отправки.
  const [values, setValues] = useState<Record<number, string>>({});
  useEffect(() => {
    if (data) setValues(Object.fromEntries(data.rows.map((row) => [row.id, String(row.stock)])));
  }, [data]);

  const save = useAction({ invalidate: [["stock"], ["products"], ["summary"]] });

  if (isPending) return <Loading />;
  if (error) return <LoadError error={error} onRetry={() => refetch()} />;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const body: Record<string, string> = {};
    for (const [id, value] of Object.entries(values)) body[`stock_${id}`] = value;
    save.mutate({ url: "/api/products/stock", data: body });
  };

  return (
    <>
      <Head title="Товары">
        <Link className="btn primary" to="/products/new">
          Новый товар
        </Link>
      </Head>

      <nav className="tabs">
        <Link to={`/products${filtersQs}`}>Список товаров</Link>
        <Link className="on" to={`/products/stock${filtersQs}`}>
          Остатки по размерам
        </Link>
      </nav>

      <ProductFilters
        categories={data.categories}
        draft={draft}
        onChange={setDraft}
        onSubmit={() => setParams(new URLSearchParams(buildQuery(draft).replace("?", "")))}
        reset={
          filtersQs ? (
            <Link className="btn ghost" to="/products/stock">
              Сбросить
            </Link>
          ) : null
        }
      />

      {data.rows.length ? (
        <form onSubmit={submit}>
          <div className="card table-wrap">
            <table className="data-grid">
              <thead>
                <tr>
                  <th className="thumb-col"></th>
                  <th>Товар</th>
                  <th>Вариант</th>
                  <th className="num">Цена</th>
                  <th className="num">Остаток</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.id}>
                    <td className="thumb-col">
                      {row.main_photo_id ? (
                        <img
                          className="thumb"
                          src={`/media/${row.main_photo_id}`}
                          alt=""
                          loading="lazy"
                        />
                      ) : (
                        <span className="thumb empty">—</span>
                      )}
                    </td>
                    <td>
                      <Link className="strong" to={`/products/${row.product_id}`}>
                        {row.title}
                      </Link>
                      <span className="muted small">
                        {row.category || "без категории"}
                        {row.is_active ? "" : " · скрыт"}
                      </span>
                    </td>
                    <td>{row.variant}</td>
                    <td className="num muted">{row.price_text}</td>
                    <td className="num">
                      {/* Нулевые остатки подсвечены: их и приходят пополнять. */}
                      <input
                        className={`stock ${values[row.id] === "0" ? "zero" : ""}`}
                        type="text"
                        inputMode="numeric"
                        value={values[row.id] ?? ""}
                        onChange={(event) =>
                          setValues({ ...values, [row.id]: event.target.value })
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="actions sticky">
            <button className="btn primary" type="submit" disabled={save.isPending}>
              Сохранить остатки
            </button>
            <span className="muted small">Строк на экране: {data.rows.length}</span>
          </div>
        </form>
      ) : (
        <EmptyState
          title={filtersQs ? "Под фильтр не попал ни один вариант." : "Вариантов пока нет."}
          hint="Размеры и цвета заводятся в карточке товара."
        />
      )}
    </>
  );
}
