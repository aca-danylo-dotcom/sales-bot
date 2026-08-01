/**
 * Новый товар — за один заход.
 *
 * Раньше страница спрашивала только название с ценой, а размеры и фото
 * приходилось дозаполнять уже в карточке: заведение товара всегда выходило в
 * два приёма. Здесь всё на одной странице, и строк для размеров можно добавить
 * сколько нужно — сервер читает столько, сколько прислали (до тридцати).
 *
 * Созданный товар открывается в карточке и остаётся скрытым: даже заполненный
 * целиком, его стоит посмотреть глазами, прежде чем показывать клиентам.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { get } from "../api/client";
import { PhotoPicker } from "../components/photo-picker";
import { BackLink, Head, Problems } from "../components/ui";
import { useAction } from "../lib/actions";
import { useMeta, usePageTitle } from "../lib/meta";

/** Столько строк показываем сразу и столько же готов принять сервер. */
const START_ROWS = 3;
const MAX_ROWS = 30;

type Row = { size: string; color: string; stock: string };

const emptyRow: Row = { size: "", color: "", stock: "" };

export default function ProductNew() {
  usePageTitle("Новый товар");
  const navigate = useNavigate();
  const meta = useMeta();

  const { data } = useQuery({
    queryKey: ["categories"],
    queryFn: ({ signal }) => get<{ categories: string[] }>("/api/products/categories", signal),
    staleTime: 60_000,
  });

  const [fields, setFields] = useState({
    title: "",
    price: "",
    old_price: "",
    category: "",
    sku: "",
    sort_order: "",
    description: "",
  });
  const [rows, setRows] = useState<Row[]>(() => Array.from({ length: START_ROWS }, () => emptyRow));
  const [files, setFiles] = useState<File[]>([]);

  const create = useAction({
    invalidate: [["products"], ["summary"]],
    onDone: (result) => {
      const id = result.id;
      if (typeof id === "number") navigate(`/products/${id}`);
    },
  });

  const setRow = (index: number, patch: Partial<Row>) =>
    setRows(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const body = new FormData();
    for (const [key, value] of Object.entries(fields)) body.append(key, value);
    rows.forEach((row, index) => {
      body.append(`new_size_${index}`, row.size);
      body.append(`new_color_${index}`, row.color);
      body.append(`new_stock_${index}`, row.stock);
    });
    for (const file of files) body.append("photo", file);
    create.mutate({ url: "/api/products/new", data: body });
  };

  return (
    <>
      <BackLink to="/products">‹ Все товары</BackLink>

      <Head
        title="Новый товар"
        lead="Всё на одной странице: описание, размеры с остатками и фото."
      />

      <Problems items={create.error?.problems ?? []} title="Не создал:" />

      <form onSubmit={submit}>
        <section className="card">
          <h2>Основное</h2>
          <div className="form">
            <label>
              <span>Название</span>
              <input
                type="text"
                maxLength={120}
                placeholder="Перчатки боксёрские Venum"
                autoFocus
                value={fields.title}
                onChange={(event) => setFields({ ...fields, title: event.target.value })}
              />
            </label>

            <div className="row">
              <label>
                <span>Цена, {meta?.currency ?? ""}</span>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="1200"
                  value={fields.price}
                  onChange={(event) => setFields({ ...fields, price: event.target.value })}
                />
              </label>
              <label>
                <span>Старая цена</span>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="если есть скидка"
                  value={fields.old_price}
                  onChange={(event) => setFields({ ...fields, old_price: event.target.value })}
                />
              </label>
            </div>

            <div className="row">
              <label>
                <span>Категория</span>
                <input
                  type="text"
                  list="categories"
                  maxLength={50}
                  autoComplete="off"
                  value={fields.category}
                  onChange={(event) => setFields({ ...fields, category: event.target.value })}
                />
                <datalist id="categories">
                  {(data?.categories ?? []).map((item) => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
              </label>
              <label>
                <span>Артикул</span>
                <input
                  type="text"
                  maxLength={50}
                  value={fields.sku}
                  onChange={(event) => setFields({ ...fields, sku: event.target.value })}
                />
              </label>
              <label className="narrow">
                <span>Порядок</span>
                <input
                  type="text"
                  inputMode="numeric"
                  placeholder="0"
                  value={fields.sort_order}
                  onChange={(event) => setFields({ ...fields, sort_order: event.target.value })}
                />
              </label>
            </div>

            <label>
              <span>Описание</span>
              <textarea
                rows={5}
                maxLength={1000}
                value={fields.description}
                onChange={(event) => setFields({ ...fields, description: event.target.value })}
              />
            </label>
          </div>
        </section>

        <section className="card">
          <h2>Размеры, цвета и остатки</h2>
          <p className="muted">
            Без вариантов товар нельзя купить — заполните хотя бы одну строку. Размеров
            больше, чем строк? Кнопка «Ещё размер» внизу добавит строку.
          </p>

          {/* Подписи ставим только над первой строкой: ниже такие же поля, и
              повторять «Размер / Цвет / Остаток» трижды — лишний шум. Для тех,
              кто читает страницу голосом, подпись остаётся в aria-label. */}
          <div className="form" id="variant-rows">
            {rows.map((row, index) => (
              <div className="row new-variant" key={index}>
                <label>
                  {index === 0 ? <span>Размер</span> : null}
                  <input
                    type="text"
                    maxLength={50}
                    aria-label={`Размер, строка ${index + 1}`}
                    placeholder="42 или M"
                    value={row.size}
                    onChange={(event) => setRow(index, { size: event.target.value })}
                  />
                </label>
                <label>
                  {index === 0 ? <span>Цвет</span> : null}
                  <input
                    type="text"
                    maxLength={50}
                    aria-label={`Цвет, строка ${index + 1}`}
                    placeholder="чёрный"
                    value={row.color}
                    onChange={(event) => setRow(index, { color: event.target.value })}
                  />
                </label>
                <label className="narrow">
                  {index === 0 ? <span>Остаток</span> : null}
                  <input
                    type="text"
                    inputMode="numeric"
                    aria-label={`Остаток, строка ${index + 1}`}
                    placeholder="0"
                    value={row.stock}
                    onChange={(event) => setRow(index, { stock: event.target.value })}
                  />
                </label>
              </div>
            ))}
          </div>

          <p className="actions">
            <button
              className="btn ghost"
              type="button"
              disabled={rows.length >= MAX_ROWS}
              onClick={() => setRows([...rows, emptyRow])}
            >
              Ещё размер
            </button>
            <span className="muted small">
              Пустые строки не мешают — они просто не сохранятся.
            </span>
          </p>
        </section>

        <section className="card">
          <h2>Фото</h2>
          <PhotoPicker
            files={files}
            onChange={setFiles}
            hint="Можно выбрать сразу несколько — первое станет главным."
          />
        </section>

        <div className="actions sticky">
          <button className="btn primary" type="submit" disabled={create.isPending}>
            Создать товар
          </button>
          <span className="muted small">
            Товар создаётся скрытым: сначала он откроется в карточке, и клиенты увидят его
            после кнопки «Выставить на продажу» внизу карточки.
          </span>
        </div>
      </form>
    </>
  );
}
