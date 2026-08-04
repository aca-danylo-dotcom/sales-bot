/**
 * Кнопка, которую надо подержать.
 *
 * Компонент «Hold Button» из реестра kokonutui (@dorianbaffier, MIT,
 * kokonutui.com) — владелец прислал его код и попросил поставить на действия с
 * заказом. Вид сохранён: плашка в цвет действия, полоса заполняется слева
 * направо, надпись меняется, пока кнопку держат.
 *
 * От оригинала отличается двумя вещами, и обе — по делу:
 *
 * 1. У оригинала нет самого действия: он умеет только рисовать полосу. Здесь
 *    добавлен `onHoldComplete` — он срабатывает, когда полоса дошла до конца,
 *    и НЕ срабатывает, если палец убрали раньше. Ради этого и вся затея:
 *    «Отменить заказ» возвращает товар на склад, и случайный тык по соседней
 *    кнопке не должен этого делать.
 *
 * 2. Подпись и значок задаются снаружи. В оригинале они выведены из цвета
 *    (красная — всегда «удалить» с корзиной), а у нас четыре разных действия,
 *    и «Оплата пришла» с корзиной для мусора выглядела бы странно.
 *
 * Ещё мелочь, без которой кнопка вела бы себя неверно: удержание считается
 * прерванным при уходе курсора, отпускании и отмене касания. Промах пальцем на
 * телефоне — обычное дело, и он обязан отменять действие, а не запускать его.
 */
import { cva, type VariantProps } from "class-variance-authority";
import { motion, useAnimation } from "motion/react";
import { useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const holdButtonVariants = cva("relative min-w-40 touch-none overflow-hidden", {
  variants: {
    variant: {
      red: [
        "bg-red-100 dark:bg-red-200",
        "hover:bg-red-100 dark:hover:bg-red-200",
        "text-red-500 dark:text-red-600",
        "border border-red-200 dark:border-red-300",
      ],
      green: [
        "bg-green-100 dark:bg-green-200",
        "hover:bg-green-100 dark:hover:bg-green-200",
        "text-green-500 dark:text-green-600",
        "border border-green-200 dark:border-green-300",
      ],
      blue: [
        "bg-blue-100 dark:bg-blue-200",
        "hover:bg-blue-100 dark:hover:bg-blue-200",
        "text-blue-500 dark:text-blue-600",
        "border border-blue-200 dark:border-blue-300",
      ],
      orange: [
        "bg-orange-100 dark:bg-orange-200",
        "hover:bg-orange-100 dark:hover:bg-orange-200",
        "text-orange-500 dark:text-orange-600",
        "border border-orange-200 dark:border-orange-300",
      ],
      grey: [
        "bg-gray-100 dark:bg-gray-200",
        "hover:bg-gray-100 dark:hover:bg-gray-200",
        "text-gray-500 dark:text-gray-600",
        "border border-gray-200 dark:border-gray-300",
      ],
    },
  },
  defaultVariants: {
    variant: "red",
  },
});

interface HoldButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onClick">,
    VariantProps<typeof holdButtonVariants> {
  /** Сколько держать, миллисекунды. */
  holdDuration?: number;
  /** Что написано на кнопке в покое. */
  label: ReactNode;
  /** Что написано, пока держат. По умолчанию — «Держите…». */
  holdingLabel?: ReactNode;
  icon?: ReactNode;
  /** Вызывается, только когда держали до конца. */
  onHoldComplete: () => void;
}

export default function HoldButton({
  className,
  variant = "red",
  holdDuration = 1600,
  label,
  holdingLabel = "Держите…",
  icon,
  onHoldComplete,
  disabled,
  ...props
}: HoldButtonProps) {
  const [isHolding, setIsHolding] = useState(false);
  const controls = useAnimation();
  /* Отметка «держим прямо сейчас». Обычной переменной состояния здесь мало:
     обещание анимации разрешается уже после того, как React перерисовал
     кнопку, и в замыкании осталось бы старое значение — действие срабатывало
     бы даже после того, как палец убрали. */
  const holding = useRef(false);

  async function handleHoldStart() {
    if (disabled) return;
    holding.current = true;
    setIsHolding(true);
    controls.set({ width: "0%" });
    await controls.start({
      width: "100%",
      transition: { duration: holdDuration / 1000, ease: "linear" },
    });
    // Сюда попадаем и когда полосу оборвали на середине: `controls.stop()`
    // тоже разрешает обещание. Отличает их отметка выше.
    if (!holding.current) return;
    holding.current = false;
    setIsHolding(false);
    controls.set({ width: "0%" });
    onHoldComplete();
  }

  function handleHoldEnd() {
    if (!holding.current) return;
    holding.current = false;
    setIsHolding(false);
    controls.stop();
    controls.start({ width: "0%", transition: { duration: 0.1 } });
  }

  return (
    <Button
      className={cn(holdButtonVariants({ variant, className }))}
      disabled={disabled}
      onMouseDown={handleHoldStart}
      onMouseLeave={handleHoldEnd}
      onMouseUp={handleHoldEnd}
      onTouchCancel={handleHoldEnd}
      onTouchEnd={handleHoldEnd}
      onTouchStart={handleHoldStart}
      {...props}
    >
      <motion.div
        animate={controls}
        className={cn("absolute top-0 left-0 h-full", {
          "bg-red-200/30 dark:bg-red-300/30": variant === "red",
          "bg-green-200/30 dark:bg-green-300/30": variant === "green",
          "bg-blue-200/30 dark:bg-blue-300/30": variant === "blue",
          "bg-orange-200/30 dark:bg-orange-300/30": variant === "orange",
          "bg-gray-200/30 dark:bg-gray-300/30": variant === "grey",
        })}
        initial={{ width: "0%" }}
      />
      <span className="relative z-10 flex w-full items-center justify-center gap-2">
        {icon}
        {isHolding ? holdingLabel : label}
      </span>
    </Button>
  );
}
