/**
 * Выручка по дням — плавная линия с заливкой под ней.
 *
 * Раньше здесь стояли столбики. Владелец нарисовал линию, и для этих данных она
 * честнее: столбик читается как «отдельная величина», а выручка по дням —
 * непрерывный ход, у которого важна форма, а не высота каждого дня.
 *
 * Написано руками, а не взято из библиотеки графиков. Причина простая: линия
 * нужна ровно одна и ровно такая, как на макете — сглаженная, с растворяющейся
 * заливкой и точкой на последнем дне. Готовый компонент пришлось бы уговаривать
 * на это настройками, а здесь всё видно в двадцати строках. Считает кривую
 * d3-shape, она уже стоит ради круговой диаграммы.
 *
 * Подписей по левому краю нет — это макет. Значит, число должно доставаться
 * иначе: наводишь на график, и день с суммой показываются в подсказке.
 */
import { useId, useState } from "react";
import { ParentSize } from "@visx/responsive";
import { area, curveMonotoneX, line } from "d3-shape";

type Point = {
  label: string;
  revenue: number;
  revenue_text: string;
};

/** Поле графика, строка с датами под ним и запас сверху под точку. */
const PLOT = 176;
const AXIS = 28;
const PAD_TOP = 16;
/* Отступ по краям — не для красоты: крайние подписи стоят по центру своей
   точки, и без запаса «03.07» обрезалось бы краем графика. */
const PAD_X = 20;

/** Ширина, которую занимает подпись «03.07» с воздухом по бокам. */
const LABEL_WIDTH = 56;

export function RevenueArea({ data }: { data: Point[] }) {
  return (
    <div className="chart-area">
      <ParentSize>
        {({ width }) =>
          width < 80 || data.length === 0 ? null : <Plot width={width} data={data} />
        }
      </ParentSize>
    </div>
  );
}

function Plot({ width, data }: { width: number; data: Point[] }) {
  const gradient = useId();
  const [hovered, setHovered] = useState<number | null>(null);

  const height = PLOT + AXIS;
  const inner = Math.max(width - PAD_X * 2, 1);
  const max = Math.max(...data.map((point) => point.revenue), 1);

  const x = (index: number) =>
    data.length < 2 ? width / 2 : PAD_X + (index / (data.length - 1)) * inner;
  const y = (value: number) => PAD_TOP + (1 - value / max) * (PLOT - PAD_TOP);

  const curve = curveMonotoneX;
  const linePath =
    line<Point>()
      .x((_, index) => x(index))
      .y((point) => y(point.revenue))
      .curve(curve)(data) ?? "";
  const areaPath =
    area<Point>()
      .x((_, index) => x(index))
      .y0(PLOT)
      .y1((point) => y(point.revenue))
      .curve(curve)(data) ?? "";

  /* Сколько дат влезает — считаем от ширины, а не берём числом. На широкой
     карточке это каждый третий день, на телефоне — каждый шестой; фиксированный
     предел на узком экране склеил бы подписи в кашу. */
  const fits = Math.max(2, Math.floor(inner / LABEL_WIDTH));
  const step = Math.max(1, Math.ceil(data.length / fits));
  const last = data.length - 1;
  const marked = hovered ?? last;
  const point = data[marked];

  /* Курсор идёт по горизонтали — день выбирается по ближайшей точке, а не по
     попаданию в неё: иначе между днями подсказка мигала бы. */
  const pick = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const share = (event.clientX - box.left - PAD_X) / inner;
    const index = Math.round(share * (data.length - 1));
    setHovered(Math.min(Math.max(index, 0), data.length - 1));
  };

  return (
    <>
      <svg
        width={width}
        height={height}
        onMouseMove={pick}
        onMouseLeave={() => setHovered(null)}
      >
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: "var(--chart-1)", stopOpacity: 0.24 }} />
            <stop offset="100%" style={{ stopColor: "var(--chart-1)", stopOpacity: 0 }} />
          </linearGradient>
        </defs>

        {/* Четыре светлые линии: взгляду нужна опора по высоте, но считать по
            ним никто не будет — цифры даёт подсказка. */}
        {[0, 1, 2, 3].map((row) => {
          const at = PAD_TOP + (row / 3) * (PLOT - PAD_TOP);
          return (
            <line
              key={row}
              className="area-grid"
              x1={PAD_X}
              x2={width - PAD_X}
              y1={at}
              y2={at}
            />
          );
        })}

        <path d={areaPath} fill={`url(#${gradient})`} />
        <path className="area-line" d={linePath} />

        {hovered !== null ? (
          <line
            className="area-cursor"
            x1={x(hovered)}
            x2={x(hovered)}
            y1={PAD_TOP - 8}
            y2={PLOT}
          />
        ) : null}

        {/* Точка стоит на последнем дне, а под курсором переезжает к нему. */}
        <circle className="area-dot" cx={x(marked)} cy={y(point.revenue)} r="5" />

        {data.map((day, index) =>
          index % step === 0 ? (
            <text key={day.label} className="area-label" x={x(index)} y={height - 8}>
              {day.label}
            </text>
          ) : null,
        )}
      </svg>

      {hovered !== null ? (
        <div
          className="area-tip"
          style={{
            // Подсказка прижимается к краю графика, а не вылезает за карточку.
            left: Math.min(Math.max(x(hovered), 52), width - 52),
            top: y(point.revenue) - 14,
          }}
        >
          <span className="area-tip-day">{point.label}</span>
          <span className="area-tip-value">{point.revenue_text}</span>
        </div>
      ) : null}
    </>
  );
}
