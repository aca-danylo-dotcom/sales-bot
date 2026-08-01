/**
 * Проверка отрисовки: рисуются ли разделы на настоящих данных.
 *
 * Панель без JavaScript не работает, поэтому ошибка вроде «нет такого поля у
 * undefined» видна только в браузере — и обычно менеджеру, а не нам. Этот
 * прогон ловит такие вещи заранее: страницы отрисовываются в node, а данные
 * берутся с живого сервера и кладутся в кеш заранее, чтобы компоненты шли по
 * настоящей разметке, а не по ветке «загружаем».
 *
 * Как запускать (сервер на 8080 должен быть поднят):
 *
 *   npx vite build --ssr check-render.tsx --outDir ssr-check
 *   mv ssr-check/check-render.js ssr-check/check-render.mjs
 *   node ssr-check/check-render.mjs
 *
 * Заодно это единственная автоматическая проверка на экранирование: имя
 * клиента с тегом внутри не должно попасть в разметку как разметка.
 */
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";

import { ConfirmProvider } from "./src/components/confirm";
import { FlashProvider } from "./src/lib/flash";
import App from "./src/App";
import Stats from "./src/pages/Stats";

const BASE = "http://localhost:8080";

async function load(url: string) {
  const response = await fetch(BASE + url);
  return response.json();
}

async function main() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  client.setQueryData(["meta"], await load("/api/meta"));
  client.setQueryData(["summary"], await load("/api/summary"));
  client.setQueryData(["orders", ""], await load("/api/orders"));
  client.setQueryData(["order", "1", "order"], await load("/api/orders/1"));
  client.setQueryData(["order", "2", "client"], await load("/api/orders/2?tab=client"));
  client.setQueryData(["products", ""], await load("/api/products"));
  client.setQueryData(["product", "1"], await load("/api/products/1"));
  client.setQueryData(["stock", ""], await load("/api/products/stock"));
  client.setQueryData(["categories"], await load("/api/products/categories"));
  client.setQueryData(["stats", ""], await load("/api/stats"));

  const wrap = (children: ReactNode, path: string) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <FlashProvider>
          <ConfirmProvider>{children}</ConfirmProvider>
        </FlashProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );

  const pages: [string, string, ReactNode, string[]][] = [
    ["Сводка", "/", <App />, ["Требует внимания", "metric-value"]],
    ["Заказы", "/orders", <App />, ["Ждут оплаты", "table-wrap"]],
    ["Карточка заказа", "/orders/1", <App />, ["Что отправляем", "timeline"]],
    ["Карточка: клиент", "/orders/2?tab=client", <App />, ["Переписка с ботом"]],
    // Списки отбора — не системный <select>, а компонент из реестра: если он
    // перестанет отрисовываться, вкладка останется без фильтров.
    [
      "Товары",
      "/products",
      <App />,
      ["Остатки по размерам", "thumb-col", 'data-slot="select-trigger"', "Все категории"],
    ],
    [
      "Карточка товара",
      "/products/1",
      <App />,
      ["Сохранить всё", "Удалить товар", "photo-picker", "Добавить"],
    ],
    [
      "Новый товар",
      "/products/new",
      <App />,
      // Плитка выбора фото должна быть на месте и в пустом состоянии: без
      // выбранных снимков она единственное, чем товар вообще сфотографируешь.
      ["Ещё размер", "Создать товар", "photo-picker", "или перетащить"],
    ],
    [
      "Остатки",
      "/products/stock",
      <App />,
      ["Сохранить остатки", 'data-slot="select-trigger"', "Любой остаток"],
    ],
    [
      "Статистика",
      "/stats",
      <Stats />,
      [
        "Продажи",
        "Путь заказа",
        "Клиенты",
        "Почему отменяли",
        "Что покупают",
        "Лежит без движения",
        "served-value",
        "Средний чек",
        "Разговор → заказ",
        // Шапка числовых колонок должна нести класс выравнивания, иначе
        // подписи встают не над числами.
        '<th class="num">',
        // Место под график выручки. Саму линию проверить здесь нельзя:
        // Recharts измеряет карточку и рисует уже в браузере, в разметке от
        // него остаётся пустая коробка нужного размера.
        'data-slot="chart"',
        "recharts-responsive-container",
        // Мозаика: блоки должны получить ширину, иначе раскладка развалилась.
        "stats-grid",
        "card span-8",
        "card span-4",
        "card span-7",
        "card span-5",
      ],
    ],
  ];

  let bad = 0;
  for (const [title, path, element, expects] of pages) {
    try {
      const html = renderToString(wrap(element, path));
      const missing = expects.filter((text) => !html.includes(text));
      const raw = html.includes("<img src=x onerror=");
      const tables = checkTables(html);
      if (missing.length || raw || tables.length) {
        bad += 1;
        const notes = [
          missing.length ? `нет ${missing.join(", ")}` : "",
          raw ? "ТЕГ НЕ ЭКРАНИРОВАН" : "",
          ...tables,
        ].filter(Boolean);
        console.log(`  ✗ ${title}: ${notes.join(" | ")}`);
      } else {
        console.log(`  ✓ ${title} (${html.length} символов)`);
      }
    } catch (error) {
      bad += 1;
      console.log(`  ✗ ${title}: ${(error as Error).message}`);
    }
  }
  console.log(bad ? `ПРОВАЛОВ: ${bad}` : "Все страницы отрисованы.");
}

/** Столбцы шапки должны сойтись со столбцами данных, иначе подписи стоят не над
 *  своими числами. Заодно ловим возврат к имени `.grid`: так называется утилита
 *  Tailwind `display: grid`, и таблица под этим именем перестаёт быть таблицей. */
function checkTables(html: string): string[] {
  const problems: string[] = [];
  // Именно `grid`, а не `data-grid`: словарная граница \b считает дефис концом
  // слова, и наш собственный класс попадал бы под запрет.
  if (/<table[^>]*class="[^"]*(?<![\w-])grid(?![\w-])/.test(html)) {
    problems.push("таблица с классом grid — займёт утилиту Tailwind");
  }
  for (const table of html.matchAll(/<table\b[\s\S]*?<\/table>/g)) {
    const heads = (table[0].match(/<th\b/g) ?? []).length;
    const first = table[0].match(/<tbody>\s*<tr\b[\s\S]*?<\/tr>/);
    const cells = (first?.[0].match(/<td\b/g) ?? []).length;
    if (heads && cells && heads !== cells) {
      problems.push(`колонок в шапке ${heads}, в строке ${cells}`);
    }
  }
  return problems;
}

void main();
