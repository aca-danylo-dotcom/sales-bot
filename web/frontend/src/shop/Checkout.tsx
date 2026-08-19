/**
 * Оформление: куда и кому везём.
 *
 * Форма целиком на одном экране, а не по шагу на вопрос, как в чате. В
 * переписке шаги оправданы — там нельзя показать пять полей сразу; здесь можно,
 * и человек видит, сколько всего от него хотят, ещё до того как начал.
 *
 * Поля подставляются из профиля: постоянный покупатель просто нажимает кнопку
 * внизу. Проверяет всё сервер (web/api/shop.py) теми же правилами, что и бот, —
 * здесь только то, что видно без обращения к нему: пустые поля.
 *
 * После успеха окно закрывается. Оплата живёт в чате: счёт Telegram или
 * реквизиты карты приходят туда же, где переписка, и держать поверх них
 * мини-приложение незачем.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { closeWebApp, haptic } from "../lib/telegram";
import { useBackButton, useMainButton } from "../lib/telegram-ui";
import { useCart, useCheckout, useShopMeta } from "./api";

const FIELDS = [
  { name: "name", label: "Получатель", placeholder: "Имя и фамилия" },
  { name: "phone", label: "Телефон", placeholder: "+380…", type: "tel" },
  { name: "city", label: "Город", placeholder: "Куда везём" },
  { name: "np_branch", label: "Отделение Новой Почты", placeholder: "12 или Поштомат 4521" },
  { name: "comment", label: "Комментарий", placeholder: "Не обязательно" },
] as const;

export default function Checkout() {
  const navigate = useNavigate();
  const { data: meta } = useShopMeta();
  const { data: cart } = useCart();
  const checkout = useCheckout();

  const [values, setValues] = useState({
    name: "", phone: "", city: "", np_branch: "", comment: "",
  });
  const [failure, setFailure] = useState("");
  const [done, setDone] = useState("");

  // Профиль приезжает вместе с meta и может опоздать к первому рисованию формы.
  useEffect(() => {
    if (!meta) return;
    setValues((current) => ({
      ...current,
      name: current.name || meta.profile.name,
      phone: current.phone || meta.profile.phone,
      city: current.city || meta.profile.city,
      np_branch: current.np_branch || meta.profile.np_branch,
    }));
  }, [meta]);

  useBackButton(done ? null : () => navigate("/shop/cart"));

  const filled = Boolean(
    values.name.trim() && values.phone.trim() && values.city.trim() && values.np_branch.trim(),
  );

  const submit = () => {
    setFailure("");
    checkout.mutate(values, {
      onSuccess: (result) => {
        haptic("ok");
        setDone(result.message);
        // Пауза, чтобы человек успел прочитать, куда ушёл счёт: закрыть окно
        // сразу — значит вернуть его в чат без единого слова о том, что дальше.
        window.setTimeout(closeWebApp, 2200);
      },
      onError: (reason) => {
        haptic("error");
        setFailure(
          reason instanceof ApiError ? reason.message : "Не получилось оформить заказ.",
        );
      },
    });
  };

  useMainButton({
    text: cart ? `Оформить на ${cart.total_text}` : "Оформить",
    visible: !done,
    disabled: !filled || checkout.isPending,
    loading: checkout.isPending,
    onClick: submit,
  });

  if (done) {
    return (
      <div className="shop-page shop-done">
        <p className="shop-done-mark">✓</p>
        <p className="shop-done-text">{done}</p>
        <p className="muted">Закрываем приложение — продолжим в чате.</p>
      </div>
    );
  }

  return (
    <div className="shop-page">
      <h1 className="shop-title">Доставка</h1>

      <form className="shop-form" onSubmit={(event) => event.preventDefault()}>
        {FIELDS.map((field) => (
          <label key={field.name} className="shop-field">
            <span className="shop-label">{field.label}</span>
            <input
              type={"type" in field ? field.type : "text"}
              placeholder={field.placeholder}
              value={values[field.name]}
              onChange={(event) =>
                setValues({ ...values, [field.name]: event.target.value })
              }
            />
          </label>
        ))}
      </form>

      {cart ? (
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
            <span>К оплате</span>
            <span>{cart.total_text}</span>
          </p>
        </div>
      ) : null}

      {meta ? <p className="shop-hint">{meta.shop.delivery}</p> : null}
      {failure ? <p className="shop-error">{failure}</p> : null}
    </div>
  );
}
