/**
 * Каркас панели владельца: шапка, место под сообщения, разделы.
 *
 * Адреса те же, что были у серверных страниц (`/orders/5`, `/products/stock`),
 * поэтому сохранённые закладки и ссылки, отправленные коллегам, продолжают
 * работать. Отдавать index.html на любой из этих адресов умеет сам сервер —
 * см. catch-all в web/app.py.
 *
 * Файл отделён от App не ради порядка, а ради веса: панель тянет за собой
 * таблицы, графики и работу с фотографиями — почти всю сборку. Покупателю,
 * который открыл витрину с телефона, всё это качать незачем, поэтому App
 * подключает панель отложенно, и в чужой чанк она не попадает.
 */
import { Suspense, lazy } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { ChartColumn, LayoutDashboard, Package, Receipt } from "lucide-react";

import { FloatingDock } from "./components/ui/floating-dock";
import type { DockItem } from "./components/ui/floating-dock";
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
import { RippleLayer } from "./components/ripple";
import ThemeSwitch from "./components/theme-switch";
import { EmptyState, Loading } from "./components/ui";

/* Статистика грузится отдельным куском. Её открывают раз в неделю, а тянет она
   за собой библиотеку графиков — незачем задерживать из-за неё «Заказы»,
   которые открывают каждые пять минут. */
const Stats = lazy(() => import("./pages/Stats"));

/* Разделы панели. `end` только у сводки: её адрес — начало всех остальных.
   «Товары» подсвечиваются и на остатках, и в карточке товара — остатки не
   отдельный раздел, а второй вид тех же товаров. */
const SECTIONS: DockItem[] = [
  { title: "Сводка", to: "/", end: true, icon: <LayoutDashboard /> },
  { title: "Заказы", to: "/orders", icon: <Receipt /> },
  { title: "Товары", to: "/products", icon: <Package /> },
  { title: "Статистика", to: "/stats", icon: <ChartColumn /> },
];

function NotFound() {
  return (
    <EmptyState
      title="Такой страницы нет."
      hint={<Link to="/">Вернуться на сводку</Link>}
    />
  );
}

export default function Crm() {
  const meta = useMeta();

  return (
    <>
      <header className="topbar">
        <Link className="brand" to="/">
          {meta?.shop_name ?? "CRM"}
        </Link>
        {/* Кнопка темы стоит перед меню и прижата вправо: меню разделов висит
            по центру шапки отдельным слоем, а на узком экране сворачивается в
            свою кнопку — переключатель должен оказаться слева от неё, а не за
            краем экрана. Подпись Light/Dark на телефоне убирается: там рядом
            название магазина и кнопка меню. */}
        <ThemeSwitch className="theme-switch" size="sm" />
        <FloatingDock items={SECTIONS} />
      </header>

      <main className="page">
        <FlashMessages />
        <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Summary />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/orders/:id" element={<OrderCard />} />
          <Route path="/products" element={<Products />} />
          <Route path="/products/new" element={<ProductNew />} />
          <Route path="/products/stock" element={<Stock />} />
          <Route path="/products/:id" element={<ProductCard />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
        </Suspense>
      </main>

      {/* Карточки о новых заказах — поверх всего, в правом нижнем углу. */}
      <NewOrderToasts />

      {/* Круг, разбегающийся при нажатии на любую кнопку панели. Стоит один раз
          здесь: кнопки о нём не знают и ничего для него не делают. */}
      <RippleLayer />
    </>
  );
}
