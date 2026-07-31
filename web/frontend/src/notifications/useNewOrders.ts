/**
 * «Что нового с прошлого раза» — опрос сервера о свежих заказах.
 *
 * Отметку о последнем увиденном заказе хранит браузер: входа в панель нет,
 * пользователей нет, класть её на сервер негде и не для кого. Отметка — номер
 * заказа, а не время: `id` растёт монотонно и не зависит ни от часов сервера,
 * ни от часов на этой машине.
 *
 * Первый заход не показывает ничего: сервер отдаёт только отсечку «с этого
 * момента». Иначе на новом рабочем месте разом высыпалась бы вся история.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { get, query as buildQuery } from "../api/client";

const POINTER_KEY = "salesbot:lastOrder";

/** Как часто спрашиваем сервер. На скрытой вкладке опрос сам замирает. */
const INTERVAL = 20_000;

export type OrderCardData = {
  id: number;
  name: string;
  city: string;
  total_text: string;
  units_text: string;
  created_at: string;
};

type Feed = { last_id: number; orders: OrderCardData[] };

function readPointer(): number | null {
  try {
    const raw = localStorage.getItem(POINTER_KEY);
    const value = raw ? Number(raw) : NaN;
    return Number.isFinite(value) && value >= 0 ? value : null;
  } catch {
    // Приватный режим и запрет на хранилище: тогда каждая перезагрузка
    // начинается заново — это лучше, чем упавшая панель.
    return null;
  }
}

function writePointer(value: number) {
  try {
    localStorage.setItem(POINTER_KEY, String(value));
  } catch {
    /* см. readPointer */
  }
}

/**
 * Отдаёт заказы, которых человек ещё не видел, и функцию «эту карточку закрыл».
 *
 * Показанные заказы сразу двигают отметку: перезагрузка страницы не должна
 * показывать их второй раз.
 */
export function useNewOrders() {
  const [since, setSince] = useState<number | null>(readPointer);
  const [cards, setCards] = useState<OrderCardData[]>([]);
  // Что уже показывали в этой вкладке: ответ с тем же заказом может прийти
  // дважды, пока отметка не доехала.
  const shown = useRef<Set<number>>(new Set());

  const { data } = useQuery({
    queryKey: ["notifications", since],
    queryFn: ({ signal }) =>
      get<Feed>(`/api/notifications${buildQuery({ since: since ?? "" })}`, signal),
    refetchInterval: INTERVAL,
    // Вкладку открыли снова — спрашиваем сразу, не дожидаясь конца интервала.
    refetchOnWindowFocus: true,
    staleTime: 0,
  });

  useEffect(() => {
    if (!data) return;

    const fresh = data.orders.filter((order) => !shown.current.has(order.id));
    for (const order of fresh) shown.current.add(order.id);
    if (fresh.length) setCards((current) => [...current, ...fresh]);

    // Отметку двигаем всегда: и после показа, и на первом заходе, когда список
    // пуст, — иначе следующий запрос снова уйдёт без `since`.
    if (data.last_id !== since) {
      writePointer(data.last_id);
      setSince(data.last_id);
    }
  }, [data, since]);

  const dismiss = (id: number) => setCards((current) => current.filter((card) => card.id !== id));

  return { cards, dismiss };
}
