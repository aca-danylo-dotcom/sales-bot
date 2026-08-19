/**
 * Карточка товара: фото, описание, выбор размера, кнопка «в корзину».
 *
 * Размер выбирается ДО добавления и без варианта по умолчанию — даже когда
 * доступен один. Молча подставленный размер приводит к возвратам: человек
 * уверен, что выбрал свой, а в заказ уехал единственный оставшийся.
 *
 * Исключение — товар без размеров и цветов (гантели, бутылка). У него ровно
 * один безымянный вариант, выбирать не из чего, и лишний шаг был бы работой
 * на пустом месте.
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { LoadError, Loading } from "../components/ui";
import { haptic } from "../lib/telegram";
import { useBackButton, useMainButton } from "../lib/telegram-ui";
import { useCartActions, useProduct } from "./api";

export default function Product() {
  const { id } = useParams();
  const navigate = useNavigate();
  const productId = Number(id);
  const { data, isPending, error, refetch } = useProduct(productId);
  const { add } = useCartActions();

  const [variantId, setVariantId] = useState<number | null>(null);
  const [failure, setFailure] = useState("");
  const [photo, setPhoto] = useState(0);

  const product = data?.product;
  const variants = product?.variants ?? [];
  // Единственный безымянный вариант выбирать не из чего — подставляем сами.
  const single = variants.length === 1 && !variants[0].label.trim().length;
  const chosen = single ? variants[0].id : variantId;

  useEffect(() => setFailure(""), [chosen]);

  useBackButton(() => navigate("/shop"));

  const variant = variants.find((item) => item.id === chosen);
  const canAdd = Boolean(variant && variant.stock > 0);

  useMainButton({
    text: chosen ? "В корзину" : "Выберите размер",
    disabled: !canAdd || add.isPending,
    loading: add.isPending,
    onClick: () => {
      if (!chosen) return;
      add.mutate(
        { variant_id: chosen },
        {
          onSuccess: () => {
            haptic("ok");
            navigate("/shop/cart");
          },
          onError: (reason) => {
            haptic("error");
            setFailure(reason instanceof ApiError ? reason.message : "Не получилось.");
          },
        },
      );
    },
  });

  if (isPending) return <Loading />;
  if (error) return <LoadError error={error} onRetry={() => refetch()} />;
  if (!product) return null;

  return (
    <div className="shop-page shop-product">
      {product.photos.length ? (
        <>
          <div className="shop-photo">
            <img src={product.photos[photo]} alt={product.title} />
          </div>
          {product.photos.length > 1 ? (
            <div className="shop-thumbs">
              {product.photos.map((src, index) => (
                <button
                  key={src}
                  type="button"
                  className={`shop-thumb ${index === photo ? "on" : ""}`}
                  onClick={() => setPhoto(index)}
                >
                  <img src={src} alt="" />
                </button>
              ))}
            </div>
          ) : null}
        </>
      ) : null}

      <h1 className="shop-title">{product.title}</h1>
      <p className="shop-price">
        {product.price_text}
        {product.old_price_text ? (
          <span className="shop-old">{product.old_price_text}</span>
        ) : null}
      </p>

      {product.description ? (
        <p className="shop-description">{product.description}</p>
      ) : null}

      {!single && variants.length ? (
        <div className="shop-variants">
          <p className="shop-label">Размер</p>
          <div className="shop-chips">
            {variants.map((item) => (
              <button
                key={item.id}
                type="button"
                disabled={item.stock === 0}
                className={`shop-chip ${chosen === item.id ? "on" : ""}`}
                onClick={() => {
                  haptic("tap");
                  setVariantId(item.id);
                }}
              >
                {item.label}
                {item.stock === 0 ? " — нет" : ""}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {variant && variant.stock > 0 && variant.stock <= 3 ? (
        <p className="shop-hint">Осталось {variant.stock} шт</p>
      ) : null}
      {variants.length === 0 ? <p className="shop-hint">Товар сейчас недоступен.</p> : null}
      {failure ? <p className="shop-error">{failure}</p> : null}
    </div>
  );
}
