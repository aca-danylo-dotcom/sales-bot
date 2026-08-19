/**
 * Каркас витрины: шапка со счётчиком корзины и нижние вкладки.
 *
 * Вкладки внизу, а не сверху: экран телефона держат одной рукой, и верх у него
 * — самое неудобное место. Их три, потому что покупателю здесь и нужно три
 * вещи: посмотреть товар, забрать корзину, проверить свои заказы.
 *
 * Главной кнопкой Telegram (той, что внизу) вкладки НЕ управляют: она занята
 * действием текущего экрана — «в корзину», «оформить». Одна кнопка не может
 * быть одновременно навигацией и действием.
 */
import { NavLink, Route, Routes } from "react-router-dom";

import { EmptyState } from "../components/ui";
import { useShopMeta } from "./api";
import Cart from "./Cart";
import Catalog from "./Catalog";
import Checkout from "./Checkout";
import Orders from "./Orders";
import Product from "./Product";

const TABS = [
  { to: "/shop", label: "Каталог", end: true },
  { to: "/shop/cart", label: "Корзина" },
  { to: "/shop/orders", label: "Заказы" },
];

export default function Shop() {
  const { data: meta } = useShopMeta();

  return (
    <div className="shop">
      <header className="shop-head">
        <span className="shop-name">{meta?.shop.name ?? "Магазин"}</span>
        {meta?.cart_count ? (
          <span className="shop-count">{meta.cart_count}</span>
        ) : null}
      </header>

      <main className="shop-body">
        <Routes>
          <Route path="/" element={<Catalog />} />
          <Route path="product/:id" element={<Product />} />
          <Route path="cart" element={<Cart />} />
          <Route path="checkout" element={<Checkout />} />
          <Route path="orders" element={<Orders />} />
          <Route path="*" element={<EmptyState title="Такой страницы нет." />} />
        </Routes>
      </main>

      <nav className="shop-tabs">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) => `shop-tab ${isActive ? "on" : ""}`}
          >
            {tab.label}
            {tab.label === "Корзина" && meta?.cart_count ? (
              <span className="shop-tab-dot" />
            ) : null}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
