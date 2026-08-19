/**
 * Список товаров.
 *
 * Два вида одного и того же каталога — карточки и остатки по размерам — живут
 * вкладками, и отбор при переключении сохраняется: выбрал «куртки, которых нет
 * в наличии» и пошёл править остатки по тем же товарам, а не по всем подряд.
 * Поэтому фильтры, как и в «Заказах», лежат в адресной строке.
 */
import { useEffect, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { get, query as buildQuery } from "../api/client";
import { ProductFilters } from "../components/product-filters";
import {
  EmptyState,
  Head,
  Loading,
  LoadError,
  Pager,
  Tag,
  useRowLink,
} from "../components/ui";
import { usePageTitle } from "../lib/meta";

type Row = {
  id: number;
  title: string;
  sku: string | null;
  category: string | null;
  price: number;
  old_price: number | null;
  price_text: string;
  old_price_text: string;
  is_active: number | boolean;
  main_photo_id: number | null;
  variants_count: number;
  total_stock: number;
};

type ProductsPage = {
  products: Row[];
  categories: string[];
  total: number;
  page: number;
  pages: number;
};

/** Что из адреса относится к отбору. `page` живёт отдельно: он сбрасывается. */
export function readFilters(params: URLSearchParams) {
  return {
    q: params.get("q") ?? "",
    category: params.get("category") ?? "",
    status: params.get("status") ?? "",
    stock: params.get("stock") ?? "",
  };
}

export default function Products() {
  usePageTitle("Товары");
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  const page = Number(params.get("page")) || 1;
  const rowLink = useRowLink();

  const [draft, setDraft] = useState(filters);
  useEffect(() => {
    setDraft(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.q, filters.category, filters.status, filters.stock]);

  const listQuery = buildQuery({ ...filters, page: page > 1 ? page : "" });
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["products", listQuery],
    queryFn: ({ signal }) => get<ProductsPage>(`/api/products${listQuery}`, signal),
    placeholderData: keepPreviousData,
  });

  const filtersQs = buildQuery(filters);
  const hasFilters = Boolean(filtersQs);

  const applyFilters = () => {
    // Новый отбор — с первой страницы: на третьей его результатов может не быть.
    setParams(new URLSearchParams(buildQuery(draft).replace("?", "")));
  };

  if (isPending) return <Loading />;
  if (error) return <LoadError error={error} onRetry={() => refetch()} />;

  return (
    <>
      <Head title="Товары" lead={`Найдено: ${data.total}`}>
        <Link className="btn primary" to="/products/new">
          Новый товар
        </Link>
      </Head>

      <nav className="tabs">
        <Link className="on" to={`/products${filtersQs}`}>
          Список товаров
        </Link>
        <Link to={`/products/stock${filtersQs}`}>Остатки по размерам</Link>
      </nav>

      <ProductFilters
        categories={data.categories}
        draft={draft}
        onChange={setDraft}
        onSubmit={applyFilters}
        reset={
          hasFilters ? (
            <Link className="btn ghost" to="/products">
              Сбросить
            </Link>
          ) : null
        }
      />

      {data.products.length ? (
        <>
          <div className="card table-wrap">
            <table className="data-grid">
              <thead>
                <tr>
                  <th className="thumb-col"></th>
                  <th>Товар</th>
                  <th>Категория</th>
                  <th className="num">Цена</th>
                  <th className="num">Остаток</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {data.products.map((product) => (
                  <tr key={product.id} {...rowLink(`/products/${product.id}${filtersQs}`)}>
                    <td className="thumb-col">
                      {product.main_photo_id ? (
                        <img
                          className="thumb"
                          src={`/media/${product.main_photo_id}`}
                          alt=""
                          loading="lazy"
                        />
                      ) : (
                        <span className="thumb empty">—</span>
                      )}
                    </td>
                    <td>
                      {/* Фильтры едут в карточку: «← Все товары» вернёт в тот же
                          отбор, а не в начало каталога. */}
                      <Link className="strong" to={`/products/${product.id}${filtersQs}`}>
                        {product.title}
                      </Link>
                      <span className="muted small">
                        #{product.id}
                        {product.sku ? ` · ${product.sku}` : ""}
                      </span>
                    </td>
                    <td data-label="Категория">{product.category || "—"}</td>
                    <td className="num" data-label="Цена">
                      {product.price_text}
                      {product.old_price_text ? (
                        <span className="muted small strike">{product.old_price_text}</span>
                      ) : null}
                    </td>
                    <td className="num" data-label="Остаток">
                      {product.variants_count ? (
                        <>
                          <span className={product.total_stock ? "" : "danger"}>
                            {product.total_stock}
                          </span>
                          <span className="muted small">{product.variants_count} вар.</span>
                        </>
                      ) : (
                        <span className="muted small">нет вариантов</span>
                      )}
                    </td>
                    <td>
                      {product.is_active ? (
                        <Tag kind="on">В продаже</Tag>
                      ) : (
                        <Tag kind="off">Скрыт</Tag>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Pager
            page={data.page}
            pages={data.pages}
            to={(next) => `/products${buildQuery({ ...filters, page: next })}`}
          />
        </>
      ) : (
        <EmptyState
          title={hasFilters ? "Под фильтр ничего не подошло." : "Каталог пока пуст."}
          hint="Товар можно завести здесь кнопкой «Новый товар» или прямо в боте — база у них общая."
        />
      )}
    </>
  );
}
