/**
 * Корзина: состав, количество, промокод, итог.
 *
 * Количество меняется кнопками «−/+», а не полем ввода: на телефоне цифровое
 * поле открывает клавиатуру поверх половины экрана, и человек теряет из виду
 * то, что правит.
 *
 * Позиции, которых не хватает на складе, помечаются прямо в строке. Узнать об
 * этом при оформлении было бы поздно: заказ не создастся целиком (см.
 * create_order), а человек уже ввёл адрес.
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { EmptyState, LoadError, Loading } from "../components/ui";
import { haptic } from "../lib/telegram";
import { useBackButton, useMainButton } from "../lib/telegram-ui";
import { useCart, useCartActions } from "./api";

export default function Cart() {
  const navigate = useNavigate();
  const { data: cart, isPending, error, refetch } = useCart();
  const { setQty, promo } = useCartActions();

  const [code, setCode] = useState("");
  const [promoAnswer, setPromoAnswer] = useState("");

  useBackButton(() => navigate("/shop"));

  const empty = !cart || cart.items.length === 0;
  const shortage = cart?.items.some((item) => item.qty > item.stock) ?? false;

  useMainButton({
    text: "Оформить заказ",
    visible: !empty,
    disabled: shortage,
    onClick: () => navigate("/shop/checkout"),
  });

  if (isPending) return <Loading />;
  if (error) return <LoadError error={error} onRetry={() => refetch()} />;

  if (empty) {
    return (
      <div className="shop-page">
        <EmptyState
          title="Корзина пуста."
          hint="Загляните в каталог — или напишите боту, что ищете."
        />
      </div>
    );
  }

  return (
    <div className="shop-page">
      <ul className="shop-cart">
        {cart.items.map((item) => (
          <li key={item.variant_id} className="shop-cart-row">
            <div className="shop-cart-main">
              <p className="shop-cart-title">{item.title}</p>
              <p className="shop-cart-label">{item.label}</p>
              {item.qty > item.stock ? (
                <p className="shop-error">
                  {item.stock === 0
                    ? "Разобрали, пока лежало в корзине"
                    : `Осталось только ${item.stock} шт`}
                </p>
              ) : null}
            </div>
            <div className="shop-cart-side">
              <div className="shop-stepper">
                <button
                  type="button"
                  aria-label="Убрать одну"
                  onClick={() => {
                    haptic("tap");
                    setQty.mutate({ variant_id: item.variant_id, qty: item.qty - 1 });
                  }}
                >
                  −
                </button>
                <span>{item.qty}</span>
                <button
                  type="button"
                  aria-label="Добавить одну"
                  disabled={item.qty >= item.stock}
                  onClick={() => {
                    haptic("tap");
                    setQty.mutate({ variant_id: item.variant_id, qty: item.qty + 1 });
                  }}
                >
                  +
                </button>
              </div>
              <p className="shop-cart-sum">{item.sum_text}</p>
            </div>
          </li>
        ))}
      </ul>

      {/* Промокод именной и одноразовый — его присылает бот в напоминании.
          Поле показываем всегда: человек с кодом в переписке должен видеть,
          куда его вводить, не спрашивая. */}
      {cart.promo ? (
        <p className="shop-promo-on">
          Промокод {cart.promo.code} — скидка {cart.promo.percent}%
        </p>
      ) : (
        <form
          className="shop-promo"
          onSubmit={(event) => {
            event.preventDefault();
            if (!code.trim()) return;
            promo.mutate(code.trim(), {
              onSuccess: () => {
                haptic("ok");
                setCode("");
                setPromoAnswer("");
              },
              onError: (reason) => {
                haptic("error");
                setPromoAnswer(
                  reason instanceof ApiError ? reason.message : "Промокод не подошёл.",
                );
              },
            });
          }}
        >
          <input
            type="text"
            placeholder="Промокод"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <button className="btn" type="submit" disabled={promo.isPending}>
            Применить
          </button>
        </form>
      )}
      {promoAnswer ? <p className="shop-error">{promoAnswer}</p> : null}

      <div className="shop-total">
        <p>
          <span>Товары</span>
          <span>{cart.subtotal_text}</span>
        </p>
        {cart.discount_text ? (
          <p className="shop-total-discount">
            <span>Скидка</span>
            <span>−{cart.discount_text}</span>
          </p>
        ) : null}
        <p className="shop-total-final">
          <span>Итого</span>
          <span>{cart.total_text}</span>
        </p>
      </div>

      {shortage ? (
        <p className="shop-error">
          Уменьшите количество там, где не хватает, — иначе заказ не соберётся.
        </p>
      ) : null}
    </div>
  );
}
