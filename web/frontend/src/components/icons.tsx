/**
 * Иконки заголовков — мягкие сиреневые глифы в две тональности.
 *
 * Нарисованы здесь, а не взяты файлами: владелец показал набор картинкой, а не
 * исходниками. Стиль повторён — скруглённые формы, светлая заливка и тёмный
 * акцент того же фиолетового, что и в панели.
 *
 * Внутри SVG, а не картинками, по двум причинам: они не тянут отдельных
 * запросов и красятся текущим цветом, если понадобится.
 *
 * Иконок ровно столько, сколько блоков в статистике. Ставить их к каждой
 * строке незачем — владелец просил не увлекаться, и он прав: значок рядом с
 * заголовком помогает найти блок глазами, а десяток значков подряд превращает
 * страницу в витрину пиктограмм.
 */
const LIGHT = "#d5c9fb";
const DARK = "#7b5cf0";

type Props = { className?: string };

function Frame({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <svg
      className={className ?? "sec-icon"}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

/** Продажи — ломаная, идущая вверх: тот же ход, что и на графике под ней. */
export function IconSales({ className }: Props) {
  return (
    <Frame className={className}>
      <path
        d="M4 22l7-7 5 5 5-7 7 7"
        stroke={LIGHT}
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.9"
      />
      <path
        d="M4 26l7-7 5 5 5-7 7 7"
        stroke={DARK}
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Frame>
  );
}

/** Клиенты — двое: один ближе, второй за ним. */
export function IconClients({ className }: Props) {
  return (
    <Frame className={className}>
      <circle cx="21" cy="11" r="5" fill={LIGHT} />
      <path d="M13 27c0-4.4 3.6-8 8-8s8 3.6 8 8H13z" fill={LIGHT} />
      <circle cx="12" cy="12" r="5.5" fill={DARK} />
      <path d="M2 27c0-4.7 3.9-8.5 8.6-8.5h2.8c4.7 0 8.6 3.8 8.6 8.5H2z" fill={DARK} />
    </Frame>
  );
}

/** Путь заказа — коробка в дороге. */
export function IconDelivery({ className }: Props) {
  return (
    <Frame className={className}>
      <path d="M3 12h13v11H3z" fill={LIGHT} />
      <path d="M16 15h6.5l4.5 4.5V23H16z" fill={DARK} opacity="0.8" />
      <circle cx="9" cy="25" r="3" fill={DARK} />
      <circle cx="22" cy="25" r="3" fill={DARK} />
      <path d="M3 8h13v4H3z" fill={DARK} opacity="0.45" />
    </Frame>
  );
}

/** Отмены — круг с чертой: знак «нельзя», как на макете. */
export function IconCancelled({ className }: Props) {
  return (
    <Frame className={className}>
      <circle cx="16" cy="16" r="11.5" stroke={DARK} strokeWidth="2.4" fill={LIGHT} />
      <path d="M10.5 16h11" stroke={DARK} strokeWidth="2.4" strokeLinecap="round" />
    </Frame>
  );
}

/** Что покупают — доля круга. */
export function IconBasket({ className }: Props) {
  return (
    <Frame className={className}>
      <circle cx="16" cy="16" r="12" fill={LIGHT} />
      <path d="M16 4a12 12 0 0 1 11.3 8L16 16V4z" fill={DARK} />
      <circle cx="16" cy="16" r="4.5" fill="#fff" />
    </Frame>
  );
}

/** Лежит без движения — коробка на складе. */
export function IconStock({ className }: Props) {
  return (
    <Frame className={className}>
      <path d="M16 3l12 6-12 6-12-6 12-6z" fill={LIGHT} />
      <path d="M4 9v14l12 6V15L4 9z" fill={DARK} opacity="0.75" />
      <path d="M28 9v14l-12 6V15l12-6z" fill={DARK} opacity="0.45" />
    </Frame>
  );
}
