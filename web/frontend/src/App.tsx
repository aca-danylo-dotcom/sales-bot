/**
 * Развилка: кому что показать.
 *
 * Одна сборка обслуживает три случая — панель владельца в браузере, ту же
 * панель внутри Telegram и витрину покупателя. Здесь только выбор, сами
 * разделы лежат в Crm.tsx и shop/Shop.tsx и подгружаются по надобности:
 * покупателю не приезжают таблицы и графики панели, владельцу в браузере —
 * витрина.
 *
 * Решение о роли принимает СЕРВЕР (`role` в /api/shop/meta). Фронт мог бы
 * сравнить id сам, но такую проверку правят в браузере за минуту; данные же
 * панели закрыты подписью Telegram (web/auth.py), и подделанная роль к ним не
 * пустит — здесь она влияет только на то, какие экраны рисовать.
 */
import { Suspense, lazy, useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { get } from "./api/client";
import { Loading } from "./components/ui";
import { followTelegramTheme, inTelegram, setupWebApp } from "./lib/telegram";
import { useShopMeta } from "./shop/api";

const Crm = lazy(() => import("./Crm"));
const Login = lazy(() => import("./Login"));
const Shop = lazy(() => import("./shop/Shop"));

/** Нужен ли пароль в этом браузере и введён ли он уже. */
type Session = { required: boolean; authorized: boolean };

export default function App() {
  const telegram = inTelegram();
  const location = useLocation();

  // Готовим окно один раз: развернуть на весь экран и подхватить тему клиента.
  useEffect(() => {
    if (!telegram) return;
    setupWebApp();
    return followTelegramTheme();
  }, [telegram]);

  const { data: meta, isPending } = useShopMeta(telegram);
  const client = useQueryClient();

  // Спрашиваем про пароль только в браузере: в Telegram вход не при чём.
  const { data: session, isPending: sessionPending } = useQuery({
    queryKey: ["session"],
    queryFn: ({ signal }) => get<Session>("/api/session", signal),
    enabled: !telegram,
    staleTime: Infinity,
  });

  const panel = (element: React.ReactNode) => (
    <Suspense fallback={<Loading />}>{element}</Suspense>
  );

  // Браузер: панель под паролем. Если пароль в настройках не задан, сервер
  // отвечает authorized сразу — панель открывается, как открывалась раньше.
  if (!telegram) {
    if (sessionPending || !session) return <Loading />;
    if (!session.authorized) {
      return panel(
        <Login onDone={() => client.invalidateQueries({ queryKey: ["session"] })} />,
      );
    }
    return panel(<Crm />);
  }

  // Пока не знаем роль — не показываем ничего: мигнуть панелью владельца перед
  // покупателем хуже, чем полсекунды ожидания.
  if (isPending || !meta) return <Loading text="Открываем магазин…" />;

  if (location.pathname.startsWith("/shop")) {
    return panel(
      <Routes>
        <Route path="/shop/*" element={<Shop />} />
      </Routes>,
    );
  }

  // Покупателю вне витрины делать нечего: адрес панели он мог получить только
  // случайно — ссылкой из переписки или прошлым заходом. Владелец же остаётся
  // в панели, а свой магазин смотрит по /shop — глазами покупателя.
  return meta.role === "client" ? <Navigate to="/shop" replace /> : panel(<Crm />);
}
