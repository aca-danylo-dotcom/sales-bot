/**
 * Меню разделов — «плавающая» панель из Aceternity UI (FloatingDock).
 *
 * Владелец прислал этот компонент и попросил поставить его сверху по центру.
 * Поведение оригинала сохранено полностью: значок под курсором раздувается, а
 * соседние — тем сильнее, чем ближе к нему; всё на пружинах motion с теми же
 * коэффициентами. Отличий от исходника пять, и каждое вынужденное:
 *
 * 1. Ссылки — через роутер (`NavLink`), а не `<a href>`. С обычной ссылкой
 *    каждый переход перезагружал бы панель целиком: это одностраничное
 *    приложение, весь смысл переезда на React был в том, чтобы этого не было.
 * 2. Текущий раздел подсвечен. У них меню без состояния — там это витрина.
 *    В панели по меню понимают, где находятся, и терять эту метку нельзя.
 * 3. Подпись выезжает ПОД значком, а не над ним. В оригинале меню внизу
 *    экрана (о чём написано в их же комментарии), у нас — сверху, и подпись
 *    над значком уехала бы за край окна.
 * 4. На узком экране список раскрывается вниз, а не вверх — по той же причине.
 * 5. Значки из lucide, который уже стоит, вместо @tabler/icons-react: ставить
 *    второй набор значков ради четырёх картинок незачем.
 *
 * Размеры чуть меньше оригинальных (36→64 вместо 40→80): раздутый значок
 * должен помещаться в шапку, а не наползать на страницу под ней.
 *
 * Оформление — в app.css, как у остального в панели.
 */
import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { Menu } from "lucide-react";
import {
  AnimatePresence,
  motion,
  useMotionValue,
  useSpring,
  useTransform,
} from "motion/react";
import type { MotionValue } from "motion/react";

export type DockItem = {
  title: string;
  icon: ReactNode;
  to: string;
  /** Только для «/»: её адрес — начало всех остальных. */
  end?: boolean;
};

/** Размеры ячейки и значка: спокойный, под курсором, и радиус влияния. */
const SIZE: [number, number] = [36, 64];
const ICON: [number, number] = [18, 30];
const REACH = 150;

const SPRING = { mass: 0.1, stiffness: 150, damping: 12 };

export function FloatingDock({ items }: { items: DockItem[] }) {
  return (
    <>
      <DockRow items={items} />
      <DockMenu items={items} />
    </>
  );
}

function DockRow({ items }: { items: DockItem[] }) {
  /* Пока курсор за пределами меню, расстояние до любой ячейки бесконечно —
     значит, все они спокойного размера. */
  const mouseX = useMotionValue(Infinity);

  return (
    <motion.nav
      className="dock"
      aria-label="Разделы"
      onMouseMove={(event) => mouseX.set(event.clientX)}
      onMouseLeave={() => mouseX.set(Infinity)}
    >
      {items.map((item) => (
        <DockCell key={item.to} mouseX={mouseX} item={item} />
      ))}
    </motion.nav>
  );
}

function DockCell({ mouseX, item }: { mouseX: MotionValue<number>; item: DockItem }) {
  const cell = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);

  /* Курсор мы знаем в координатах окна, поэтому и ячейку меряем в них же:
     в оригинале здесь pageX против clientX — на странице с горизонтальной
     прокруткой значки раздувались бы не под курсором. */
  const distance = useTransform(mouseX, (value) => {
    const box = cell.current?.getBoundingClientRect() ?? { x: 0, width: 0 };
    return value - box.x - box.width / 2;
  });

  /* Размер считается дважды по одной формуле — для ячейки и для значка внутри
     неё. Вынести в общую функцию нельзя: внутри хуки, а их порядок должен быть
     виден на месте. */
  const size = useSpring(
    useTransform(distance, [-REACH, 0, REACH], [SIZE[0], SIZE[1], SIZE[0]]),
    SPRING,
  );
  const icon = useSpring(
    useTransform(distance, [-REACH, 0, REACH], [ICON[0], ICON[1], ICON[0]]),
    SPRING,
  );

  return (
    <NavLink className="dock-item" to={item.to} end={item.end} aria-label={item.title}>
      {({ isActive }) => (
        <motion.div
          ref={cell}
          className="dock-cell"
          data-active={isActive}
          style={{ width: size, height: size }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          <AnimatePresence>
            {hovered ? (
              <motion.span
                className="dock-tip"
                initial={{ opacity: 0, y: -6, x: "-50%" }}
                animate={{ opacity: 1, y: 0, x: "-50%" }}
                exit={{ opacity: 0, y: -2, x: "-50%" }}
              >
                {item.title}
              </motion.span>
            ) : null}
          </AnimatePresence>

          <motion.span className="dock-glyph" style={{ width: icon, height: icon }}>
            {item.icon}
          </motion.span>
        </motion.div>
      )}
    </NavLink>
  );
}

/** Узкий экран: одна кнопка, из неё вниз выпадает столбик разделов. */
function DockMenu({ items }: { items: DockItem[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="dock-menu">
      <button
        type="button"
        className="dock-toggle"
        aria-expanded={open}
        aria-label={open ? "Закрыть меню" : "Открыть меню"}
        onClick={() => setOpen(!open)}
      >
        <Menu />
      </button>

      <AnimatePresence>
        {open ? (
          <motion.nav className="dock-drop" aria-label="Разделы">
            {items.map((item, index) => (
              <motion.div
                key={item.to}
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10, transition: { delay: index * 0.05 } }}
                transition={{ delay: (items.length - 1 - index) * 0.05 }}
              >
                <NavLink
                  className="dock-item"
                  to={item.to}
                  end={item.end}
                  onClick={() => setOpen(false)}
                >
                  {({ isActive }) => (
                    <span className="dock-cell" data-active={isActive}>
                      <span className="dock-glyph">{item.icon}</span>
                      {item.title}
                    </span>
                  )}
                </NavLink>
              </motion.div>
            ))}
          </motion.nav>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
