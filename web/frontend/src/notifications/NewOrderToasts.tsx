/**
 * «Получен новый заказ» — карточка в правом нижнем углу.
 *
 * Ради этого экрана панель и переезжала на React: заказ приходит из бота, а
 * менеджер сидит в другой вкладке и узнаёт о нём через полчаса.
 *
 * Поведение подсмотрено в присланном компоненте (AnimatedList): карточки
 * выезжают снизу и складываются стопкой. Своё здесь — правила, без которых
 * уведомление мешает работать:
 *
 *   * на экране не больше трёх; остальные ждут очереди, а не наваливаются;
 *   * карточка уходит через десять секунд, но таймер встаёт на паузу под
 *     мышью — иначе она исчезает ровно в тот момент, когда в неё целятся;
 *   * клик открывает заказ, крестик — просто закрывает;
 *   * при `prefers-reduced-motion` карточка появляется без движения.
 */
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useNavigate } from "react-router-dom";

import { armSound, chime } from "./sound";
import { useNewOrders, type OrderCardData } from "./useNewOrders";

const ON_SCREEN = 3;
const LIFETIME = 10_000;

export function NewOrderToasts() {
  const { cards, dismiss } = useNewOrders();
  const navigate = useNavigate();
  const reduced = useReducedMotion();
  const heard = useRef(0);

  // Разрешение на звук браузер даёт только после того, как по странице
  // кликнули. Ловим первый же клик и на этом успокаиваемся.
  useEffect(() => {
    const handler = () => armSound();
    window.addEventListener("pointerdown", handler, { once: true });
    return () => window.removeEventListener("pointerdown", handler);
  }, []);

  useEffect(() => {
    if (cards.length > heard.current) chime();
    heard.current = cards.length;
  }, [cards.length]);

  const visible = cards.slice(0, ON_SCREEN);

  return (
    <div className="toasts" aria-live="polite">
      <AnimatePresence initial={false}>
        {visible.map((card) => (
          <motion.div
            key={card.id}
            layout
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 24, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.96 }}
            transition={{ type: "spring", stiffness: 320, damping: 30 }}
          >
            <Toast
              card={card}
              onOpen={() => {
                dismiss(card.id);
                navigate(`/orders/${card.id}`);
              }}
              onClose={() => dismiss(card.id)}
            />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function Toast({
  card,
  onOpen,
  onClose,
}: {
  card: OrderCardData;
  onOpen: () => void;
  onClose: () => void;
}) {
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) return;
    const timer = window.setTimeout(onClose, LIFETIME);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused]);

  return (
    <div
      className="toast"
      role="button"
      tabIndex={0}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <span className="toast-icon" aria-hidden="true">
        🛍
      </span>
      <div className="toast-main">
        <span className="toast-title">Получен новый заказ</span>
        {/* Имя клиента приходит из бота и здесь остаётся обычным текстом:
            React экранирует его сам. */}
        <span className="toast-text">
          №{card.id} · {card.name}
          {card.city ? ` · ${card.city}` : ""}
        </span>
        <span className="toast-sub">
          {card.units_text} на {card.total_text}
        </span>
      </div>
      <button
        className="toast-close"
        type="button"
        aria-label="Закрыть"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
      >
        ×
      </button>
    </div>
  );
}
