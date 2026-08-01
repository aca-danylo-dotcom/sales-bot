/**
 * Фон «Статистики»: мягкий свет сверху справа, косые лучи и пакет в углу.
 *
 * Про пакет честно: на макете это трёхмерная отрисовка — стеклянный пакет со
 * своими бликами и преломлениями. Такую картинку рисуют в трёхмерном редакторе
 * и кладут файлом; повторить её пиксель в пиксель руками нельзя, поэтому здесь
 * рисунок: тот же силуэт, тот же наклон, те же лавандовые полутона. Если
 * владелец пришлёт исходную картинку, она встанет на место рисунка одной
 * строкой — разметка под неё уже готова.
 *
 * Свет и лучи, наоборот, повторяются точно: это градиенты, а не картинка, и
 * живут они в app.css.
 *
 * Слой лежит под страницей и ничего не ловит мышью. Он появляется только на
 * «Статистике»: на «Заказах» украшение за таблицей на двести строк мешало бы
 * читать, а не помогало.
 */
export function StatsBackdrop() {
  return (
    <div className="stats-backdrop" aria-hidden="true">
      <svg className="stats-bag" viewBox="0 0 300 400" fill="none">
        <defs>
          {/* Лицевая грань светлее к верху — так падает свет из угла экрана. */}
          <linearGradient id="bag-front" x1="0.1" y1="0" x2="0.9" y2="1">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="52%" stopColor="#f1f3fd" />
            <stop offset="100%" stopColor="#e0e5f7" />
          </linearGradient>
          <linearGradient id="bag-side" x1="0" y1="0" x2="1" y2="0.2">
            <stop offset="0%" stopColor="#dde2f4" />
            <stop offset="100%" stopColor="#c9d1ea" />
          </linearGradient>
          <linearGradient id="bag-rim" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#f8f9fe" />
            <stop offset="100%" stopColor="#e4e9f8" />
          </linearGradient>
          <linearGradient id="bag-handle" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#eef1fb" />
            <stop offset="100%" stopColor="#d5dcf0" />
          </linearGradient>
          {/* Блик по лицевой грани — широкая размытая полоса. */}
          <linearGradient id="bag-glare" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.9" />
            <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Ручки рисуются первыми: их концы должны уходить под кромку. */}
        <path
          d="M86 104 C86 40 158 40 158 104"
          stroke="url(#bag-handle)"
          strokeWidth="9"
          strokeLinecap="round"
        />
        <path
          d="M176 92 C182 34 244 32 240 84"
          stroke="url(#bag-handle)"
          strokeWidth="8"
          strokeLinecap="round"
          opacity="0.75"
        />

        {/* Открытая кромка — параллелограмм между лицевой и боковой гранями. */}
        <path d="M40 104 H198 L262 74 H104 Z" fill="url(#bag-rim)" />

        {/* Боковая грань уходит вглубь, поэтому темнее лицевой. */}
        <path d="M198 104 L262 74 V308 L198 348 Z" fill="url(#bag-side)" />

        {/* Лицевая грань. Низ скруглён: у бумажного пакета там сгиб. */}
        <path d="M40 104 H198 V348 H58 A18 18 0 0 1 40 330 Z" fill="url(#bag-front)" />

        <path
          d="M64 104 L128 104 L74 348 L58 348 A18 18 0 0 1 40 330 L40 200 Z"
          fill="url(#bag-glare)"
          opacity="0.55"
        />
      </svg>
    </div>
  );
}
