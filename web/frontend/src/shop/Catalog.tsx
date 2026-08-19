/**
 * Витрина: поиск, категории, плитка товаров.
 *
 * Плитка в две колонки — на телефоне это предел, при котором на карточке ещё
 * читается название и видно фото. Распроданное не прячем, а помечаем: товар,
 * исчезнувший из каталога, читается как «такого у них не бывает», и человек
 * уходит искать в другом месте вместо того, чтобы спросить, когда завоз.
 */
import { useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState, LoadError, Loading } from "../components/ui";
import { haptic } from "../lib/telegram";
import { useCatalog, type ProductBrief } from "./api";

function Tile({ product }: { product: ProductBrief }) {
  return (
    <Link
      className="shop-tile"
      to={`/shop/product/${product.id}`}
      onClick={() => haptic("tap")}
    >
      <div className="shop-tile-photo">
        {product.photo ? (
          <img src={product.photo} alt="" loading="lazy" />
        ) : (
          <span className="shop-tile-nophoto">нет фото</span>
        )}
        {!product.in_stock ? <span className="shop-tile-out">нет в наличии</span> : null}
      </div>
      <p className="shop-tile-title">{product.title}</p>
      <p className="shop-tile-price">
        {product.price_text}
        {product.old_price_text ? (
          <span className="shop-tile-old">{product.old_price_text}</span>
        ) : null}
      </p>
    </Link>
  );
}

export default function Catalog() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const { data, isPending, error, refetch } = useCatalog(search, category);

  return (
    <div className="shop-page">
      <input
        className="shop-search"
        type="search"
        placeholder="Что ищете?"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />

      {/* Категории лентой вбок: их немного, но на узком экране в строку они
          не помещаются, а перенос на вторую строку съедает первый экран. */}
      {data?.categories.length ? (
        <div className="shop-chips">
          <button
            type="button"
            className={`shop-chip ${category === "" ? "on" : ""}`}
            onClick={() => setCategory("")}
          >
            Всё
          </button>
          {data.categories.map((name) => (
            <button
              key={name}
              type="button"
              className={`shop-chip ${category === name ? "on" : ""}`}
              onClick={() => {
                haptic("tap");
                setCategory(category === name ? "" : name);
              }}
            >
              {name}
            </button>
          ))}
        </div>
      ) : null}

      {isPending ? <Loading /> : null}
      {error ? <LoadError error={error} onRetry={() => refetch()} /> : null}

      {data && data.products.length === 0 ? (
        <EmptyState
          title={search ? "По такому запросу ничего нет." : "Товары ещё не выложены."}
          hint={search ? "Попробуйте другое слово или откройте всё." : undefined}
        />
      ) : null}

      <div className="shop-grid">
        {data?.products.map((product) => (
          <Tile key={product.id} product={product} />
        ))}
      </div>
    </div>
  );
}
