/**
 * Карточка товара — всё про один товар на одном экране.
 *
 * Главное решение осталось прежним: карточка сохраняется целиком, одной
 * кнопкой. Раньше блоков было три, у каждого своя кнопка, и продавец, заполнив
 * всё подряд, сохранял только один — остальное молча терялось. Поэтому
 * основное, остатки, новый вариант и выбранные фото уезжают на сервер вместе,
 * а панель с кнопками липнет к низу экрана: карточка длинная.
 *
 * Фото копятся в состоянии массивом. В старой версии для этого приходилось
 * пересобирать `DataTransfer`, потому что второй выбор файлов в `input`
 * заменял первый; здесь такой заботы нет — список наш, а `input` только
 * пополняет его.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { get, query as buildQuery } from "../api/client";
import { useConfirm } from "../components/confirm";
import { PhotoPicker } from "../components/photo-picker";
import { BackLink, Head, Loading, LoadError, Problems, Tag } from "../components/ui";
import { useAction } from "../lib/actions";
import { useMeta, usePageTitle } from "../lib/meta";
import { readFilters } from "./Products";

type Variant = { id: number; size: string; color: string; stock: number };
type Photo = { id: number; is_main: number | boolean };

type Product = {
  id: number;
  title: string;
  description: string;
  category: string;
  sku: string | null;
  price: number;
  old_price: number | null;
  price_input: string;
  old_price_input: string;
  price_text: string;
  sort_order: number;
  is_active: number | boolean;
  total_stock: number;
  variants: Variant[];
  photos: Photo[];
};

type Card = { product: Product; categories: string[] };

type Fields = {
  title: string;
  price: string;
  old_price: string;
  category: string;
  sku: string;
  sort_order: string;
  description: string;
};

export default function ProductCard() {
  const { id } = useParams();
  const navigate = useNavigate();
  const ask = useConfirm();
  const meta = useMeta();

  const [params] = useSearchParams();
  const filtersQs = buildQuery(readFilters(params));
  const backTo = `/products${filtersQs}`;

  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["product", id],
    queryFn: ({ signal }) => get<Card>(`/api/products/${id}`, signal),
  });
  usePageTitle(data?.product.title ?? "Товар");

  const invalidate = [["product", id], ["products"], ["stock"], ["summary"]];
  const save = useAction({ invalidate });
  const variants = useAction({ invalidate });
  const photos = useAction({ invalidate });
  const remove = useAction({
    invalidate: [["products"], ["stock"], ["summary"]],
    onDone: () => navigate(backTo),
  });

  const [fields, setFields] = useState<Fields>({
    title: "",
    price: "",
    old_price: "",
    category: "",
    sku: "",
    sort_order: "0",
    description: "",
  });
  // Остатки по вариантам — строками: пустое поле и «0» должны различаться, а
  // число превратило бы одно в другое.
  const [stock, setStock] = useState<Record<number, string>>({});
  const [fresh, setFresh] = useState({ size: "", color: "", stock: "" });
  const [files, setFiles] = useState<File[]>([]);

  useEffect(() => {
    if (!data) return;
    const product = data.product;
    setFields({
      title: product.title,
      price: product.price_input,
      old_price: product.old_price_input,
      category: product.category ?? "",
      sku: product.sku ?? "",
      sort_order: String(product.sort_order ?? 0),
      description: product.description ?? "",
    });
    setStock(Object.fromEntries(product.variants.map((v) => [v.id, String(v.stock)])));
  }, [data]);

  if (isPending) return <Loading />;
  if (error || !data) return <LoadError error={error} onRetry={() => refetch()} />;

  const product = data.product;

  /** Всё, что набрано в карточке, — одним телом: поля вместе с файлами. */
  const buildBody = (extra?: Record<string, string>) => {
    const body = new FormData();
    for (const [key, value] of Object.entries(fields)) body.append(key, value);
    for (const [variantId, value] of Object.entries(stock)) body.append(`stock_${variantId}`, value);
    body.append("new_size", fresh.size);
    body.append("new_color", fresh.color);
    body.append("new_stock", fresh.stock);
    for (const file of files) body.append("photo", file);
    for (const [key, value] of Object.entries(extra ?? {})) body.append(key, value);
    return body;
  };

  const submit = (extra?: Record<string, string>, then?: () => void) =>
    save.mutate(
      { url: `/api/products/${product.id}`, data: buildBody(extra) },
      {
        onSuccess: () => {
          // Что уехало на сервер, из формы убираем: иначе второе «Сохранить»
          // отправит те же файлы и тот же вариант ещё раз.
          setFiles([]);
          setFresh({ size: "", color: "", stock: "" });
          then?.();
        },
      }
    );


  const deleteVariant = async (variant: Variant) => {
    const label = [variant.size, variant.color].filter(Boolean).join(" · ") || "без названия";
    if (!(await ask({ title: "Удалить вариант?", hint: label, danger: true }))) return;
    variants.mutate({
      url: `/api/products/${product.id}/variants`,
      data: { delete_variant: variant.id },
    });
  };

  const deleteProduct = async () => {
    const answer = await ask({
      title: "Удалить товар вместе с вариантами и фото?",
      hint: "Это не отменяется. Если товар просто закончился — скройте его с витрины.",
      confirmText: "Удалить товар",
      danger: true,
    });
    if (answer) remove.mutate({ url: `/api/products/${product.id}/delete` });
  };

  const busy = save.isPending || variants.isPending || photos.isPending || remove.isPending;

  return (
    <>
      <BackLink to={backTo}>‹ Все товары</BackLink>

      <Head
        title={product.title}
        lead={
          <>
            #{product.id} ·{" "}
            {product.is_active ? <Tag kind="on">В продаже</Tag> : <Tag kind="off">Скрыт</Tag>} ·
            остаток {product.total_stock}
          </>
        }
      />

      <Problems items={save.error?.problems ?? []} />

      <section className="card">
        <h2>Основное</h2>
        <div className="form">
          <label>
            <span>Название</span>
            <input
              type="text"
              value={fields.title}
              maxLength={120}
              onChange={(event) => setFields({ ...fields, title: event.target.value })}
            />
          </label>

          <div className="row">
            <label>
              <span>Цена, {meta?.currency ?? ""}</span>
              <input
                type="text"
                inputMode="decimal"
                value={fields.price}
                onChange={(event) => setFields({ ...fields, price: event.target.value })}
              />
            </label>
            <label>
              <span>Старая цена</span>
              <input
                type="text"
                inputMode="decimal"
                value={fields.old_price}
                placeholder="если есть скидка"
                onChange={(event) => setFields({ ...fields, old_price: event.target.value })}
              />
            </label>
          </div>

          <div className="row">
            <label>
              <span>Категория</span>
              {/* Подсказка, но своё вписать можно: категории в этом магазине не
                  справочник, а привычка продавца. */}
              <input
                type="text"
                list="categories"
                maxLength={50}
                autoComplete="off"
                value={fields.category}
                onChange={(event) => setFields({ ...fields, category: event.target.value })}
              />
              <datalist id="categories">
                {data.categories.map((item) => (
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
        {!product.variants.length ? (
          <p className="muted">Вариантов нет — купить товар нельзя. Добавьте хотя бы один.</p>
        ) : null}

        <div className="form">
          {product.variants.length ? (
            <div className="table-wrap">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Размер</th>
                    <th>Цвет</th>
                    <th className="num">Остаток</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {product.variants.map((variant) => (
                    <tr key={variant.id}>
                      <td>{variant.size || "—"}</td>
                      <td>{variant.color || "—"}</td>
                      <td className="num">
                        <input
                          className="stock"
                          type="text"
                          inputMode="numeric"
                          value={stock[variant.id] ?? ""}
                          onChange={(event) =>
                            setStock({ ...stock, [variant.id]: event.target.value })
                          }
                        />
                      </td>
                      <td className="num">
                        <button
                          className="btn ghost small"
                          type="button"
                          disabled={busy}
                          onClick={() => void deleteVariant(variant)}
                        >
                          Удалить
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          <div className="row new-variant">
            <label>
              <span>Новый размер</span>
              <input
                type="text"
                maxLength={50}
                placeholder="42 или M"
                value={fresh.size}
                onChange={(event) => setFresh({ ...fresh, size: event.target.value })}
              />
            </label>
            <label>
              <span>Цвет</span>
              <input
                type="text"
                maxLength={50}
                placeholder="чёрный"
                value={fresh.color}
                onChange={(event) => setFresh({ ...fresh, color: event.target.value })}
              />
            </label>
            <label className="narrow">
              <span>Остаток</span>
              <input
                type="text"
                inputMode="numeric"
                placeholder="0"
                value={fresh.stock}
                onChange={(event) => setFresh({ ...fresh, stock: event.target.value })}
              />
            </label>
          </div>
          <p className="muted small">Заполните эту строку — вариант добавится при сохранении.</p>
        </div>
      </section>

      <section className="card">
        <h2>Фото</h2>
        {product.photos.length ? (
          <div className="photos">
            {product.photos.map((photo) => (
              <figure key={photo.id} className={`photo ${photo.is_main ? "main" : ""}`}>
                <a href={`/media/${photo.id}`} target="_blank" rel="noopener">
                  <img src={`/media/${photo.id}`} alt="" loading="lazy" />
                </a>
                <figcaption>
                  {photo.is_main ? (
                    <Tag kind="on">Главное</Tag>
                  ) : (
                    <button
                      className="btn ghost small"
                      type="button"
                      disabled={busy}
                      onClick={() => photos.mutate({ url: `/api/photos/${photo.id}/main` })}
                    >
                      Сделать главным
                    </button>
                  )}
                  <button
                    className="btn ghost small"
                    type="button"
                    disabled={busy}
                    onClick={() => photos.mutate({ url: `/api/photos/${photo.id}/delete` })}
                  >
                    Удалить
                  </button>
                </figcaption>
              </figure>
            ))}
          </div>
        ) : (
          <p className="muted">Фото пока нет. Клиенту такой товар уйдёт текстом.</p>
        )}

        <PhotoPicker
          files={files}
          onChange={setFiles}
          hint="Файлы загрузятся при сохранении. Одно и то же фото второй раз не добавится."
        />
      </section>

      {/* Удаление внизу, а не в шапке: сверху оно попадалось под палец при
          заходе в карточку, а нужно в самом конце — когда товар посмотрели. */}
      <section className="card card-actions">
        <h2>Удалить товар</h2>
        <div className="row">
          <button
            className="btn danger-btn"
            type="button"
            disabled={busy}
            onClick={() => void deleteProduct()}
          >
            Удалить товар
          </button>
        </div>
        <p className="muted small">
          Уносит варианты, фото и всю карточку — это не отменяется. Если товар просто
          закончился, не удаляйте его: скройте с витрины кнопкой внизу, остатки и история
          останутся.
        </p>
      </section>

      {/* Панель липнет к низу: карточка длинная, и мотать её до кнопки после
          каждой правки — та самая работа, ради которой панель и заводили.
          Кнопка витрины здесь же и отправляет то же самое, поэтому «выставить»
          после правки полей больше не теряет набранное. */}
      <div className="actions sticky">
        <button className="btn primary" type="button" disabled={busy} onClick={() => submit()}>
          Сохранить всё
        </button>
        {product.is_active ? (
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={() => submit({ hide: "1" })}
          >
            Скрыть с витрины
          </button>
        ) : (
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={() => submit({ publish: "1" })}
          >
            Выставить на продажу
          </button>
        )}
        <button
          className="btn"
          type="button"
          disabled={busy}
          onClick={() => submit(undefined, () => navigate(backTo))}
        >
          Готово
        </button>
        <span className="muted small">
          {product.is_active
            ? "«Готово» сохранит и вернёт в список товаров."
            : "Товар скрыт — клиенты его не видят. «Выставить на продажу» сохранит правки и сразу покажет его в боте."}
        </span>
      </div>
    </>
  );
}
