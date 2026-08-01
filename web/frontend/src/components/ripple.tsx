/**
 * Круг, разбегающийся из-под пальца при нажатии на кнопку.
 *
 * Владелец прислал компонент `RippleButton` из magicui и попросил такую
 * анимацию на всём сайте. Компонентом это делать нельзя: он приносит свой
 * `<button>` со своим оформлением, а кнопок в панели три с лишним десятка, и
 * половина из них — не кнопки вовсе, а ссылки (`<Link className="btn">`),
 * которым `<button>` сломал бы переход. Поэтому взято само поведение —
 * размеры, скорость и ход анимации повторяют оригинал: круг диаметром в
 * бо́льшую сторону кнопки, центр под курсором, за 600 мс он вырастает вдвое и
 * растворяется.
 *
 * Слушаем нажатия на всём документе одним обработчиком и рисуем круги здесь, а
 * не внутри кнопки. Так ни одна из существующих кнопок не переписывается, и
 * любая новая получает анимацию сама, без напоминаний. Круги живут в портале
 * над страницей: класть чужой узел внутрь кнопки, которой распоряжается React,
 * — верный способ однажды получить перепутанный порядок детей.
 *
 * Цвет круга задаётся в стилях переменной `--ripple`: у синей кнопки он свой, у
 * красной свой. Логика цветов — дело темы, а не этого файла.
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

/** Всё, что в панели выглядит и работает как кнопка: пилюли и вкладки. */
const BUTTONS = ".btn, .tab, .tabs a";

/** Столько же, сколько у оригинала. Совпадает с длительностью в app.css. */
const RIPPLE_COLOR = "#add8e6";

type Ripple = {
  key: number;
  /** Рамка кнопки на экране — по ней круг обрезается. */
  box: { left: number; top: number; width: number; height: number; radius: string };
  /** Круг: положение внутри рамки и диаметр. */
  size: number;
  x: number;
  y: number;
  color: string;
};

let counter = 0;

export function RippleLayer() {
  const [ripples, setRipples] = useState<Ripple[]>([]);

  useEffect(() => {
    // Уважаем настройку системы «поменьше движения»: там, где её включили,
    // кнопка просто меняет фон, как и раньше.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const add = (button: HTMLElement, pointer: { x: number; y: number } | null) => {
      const box = button.getBoundingClientRect();
      const style = getComputedStyle(button);
      const size = Math.max(box.width, box.height);
      // С клавиатуры точки нажатия нет — пускаем круг из середины кнопки.
      const x = pointer ? pointer.x - box.left : box.width / 2;
      const y = pointer ? pointer.y - box.top : box.height / 2;

      counter += 1;
      setRipples((list) => [
        ...list,
        {
          key: counter,
          box: {
            left: box.left,
            top: box.top,
            width: box.width,
            height: box.height,
            radius: style.borderRadius,
          },
          size,
          x: x - size / 2,
          y: y - size / 2,
          color: style.getPropertyValue("--ripple").trim() || RIPPLE_COLOR,
        },
      ]);
    };

    /** Кнопка под курсором — или ничего, если нажали мимо или по погашенной. */
    const target = (event: Event): HTMLElement | null => {
      const node = event.target;
      if (!(node instanceof Element)) return null;
      const button = node.closest<HTMLElement>(BUTTONS);
      if (!button) return null;
      // Кнопка, пока запрос в пути, не нажимается — и не отзывается.
      if (button.matches(":disabled") || button.getAttribute("aria-disabled") === "true") {
        return null;
      }
      return button;
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return; // правая кнопка мыши ничего не запускает
      const button = target(event);
      if (button) add(button, { x: event.clientX, y: event.clientY });
    };

    // Нажатие с клавиатуры (Enter или пробел на кнопке в фокусе) даёт click без
    // pointerdown. Без этой строки клавиатура осталась бы вовсе без отклика.
    const onClick = (event: MouseEvent) => {
      if (event.detail !== 0) return; // мышь уже отработала выше
      const button = target(event);
      if (button) add(button, null);
    };

    // Слушаем на погружении: обработчик кнопки может остановить всплытие.
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("click", onClick, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("click", onClick, true);
    };
  }, []);

  if (ripples.length === 0) return null;

  const forget = (key: number) =>
    setRipples((list) => list.filter((ripple) => ripple.key !== key));

  return createPortal(
    <div className="ripple-layer" aria-hidden="true">
      {ripples.map((ripple) => (
        <span
          key={ripple.key}
          className="ripple-clip"
          style={{
            left: ripple.box.left,
            top: ripple.box.top,
            width: ripple.box.width,
            height: ripple.box.height,
            borderRadius: ripple.box.radius,
          }}
        >
          <span
            className="ripple"
            style={{
              left: ripple.x,
              top: ripple.y,
              width: ripple.size,
              height: ripple.size,
              background: ripple.color,
            }}
            onAnimationEnd={() => forget(ripple.key)}
          />
        </span>
      ))}
    </div>,
    document.body,
  );
}
