/**
 * Каркас панели: шапка, место под сообщения, разделы.
 *
 * Адреса те же, что были у серверных страниц (`/orders/5`, `/products/stock`),
 * поэтому сохранённые закладки и ссылки, отправленные коллегам, продолжают
 * работать. Отдавать index.html на любой из этих адресов умеет сам сервер —
 * см. catch-all в web/app.py.
 */
import { NavLink, Link, Route, Routes } from "react-router-dom";

import { FlashMessages } from "./lib/flash";
import { useMeta } from "./lib/meta";
import { NewOrderToasts } from "./notifications/NewOrderToasts";
import OrderCard from "./pages/OrderCard";
import Orders from "./pages/Orders";
import ProductCard from "./pages/ProductCard";
import ProductNew from "./pages/ProductNew";
import Products from "./pages/Products";
import Stock from "./pages/Stock";
import Summary from "./pages/Summary";
import { EmptyState } from "./components/ui";

function NotFound() {
  return (
    <EmptyState
      title="Такой страницы нет."
      hint={<Link to="/">Вернуться на сводку</Link>}
    />
  );
}

export default function App() {
  const meta = useMeta();

  return (
    <>
      <header className="topbar">
        <Link className="brand" to="/">
          {meta?.shop_name ?? "CRM"}
        </Link>
        <nav>
          {/* `end` только у сводки: её адрес — префикс всех остальных.
              «Товары» подсвечиваются и на остатках, и в карточке товара —
              остатки не отдельный раздел, а второй вид тех же товаров. */}
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Сводка
          </NavLink>
          <NavLink to="/orders" className={({ isActive }) => (isActive ? "active" : "")}>
            Заказы
          </NavLink>
          <NavLink to="/products" className={({ isActive }) => (isActive ? "active" : "")}>
            Товары
          </NavLink>
        </nav>
      </header>

      <main className="page">
        <FlashMessages />
        <Routes>
          <Route path="/" element={<Summary />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/orders/:id" element={<OrderCard />} />
          <Route path="/products" element={<Products />} />
          <Route path="/products/new" element={<ProductNew />} />
          <Route path="/products/stock" element={<Stock />} />
          <Route path="/products/:id" element={<ProductCard />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>

      {/* Карточки о новых заказах — поверх всего, в правом нижнем углу. */}
      <NewOrderToasts />
    </>
  );
}
