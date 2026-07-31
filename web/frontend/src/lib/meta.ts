/**
 * Название магазина и заголовок вкладки.
 *
 * Название приходит с сервера (`/api/meta`), а не вшито в сборку: сборка одна,
 * а магазин у каждой установки свой. Запрос вечный — эти два поля меняются
 * разве что при перенастройке бота.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { get } from "../api/client";

type Meta = { shop_name: string; currency: string };

export function useMeta() {
  const { data } = useQuery({
    queryKey: ["meta"],
    queryFn: ({ signal }) => get<Meta>("/api/meta", signal),
    staleTime: Infinity,
  });
  return data;
}

/** Заголовок вкладки: по нему панель находят среди десятка открытых. */
export function usePageTitle(title: string) {
  const meta = useMeta();
  useEffect(() => {
    document.title = meta?.shop_name ? `${title} — ${meta.shop_name}` : title;
  }, [title, meta?.shop_name]);
}
