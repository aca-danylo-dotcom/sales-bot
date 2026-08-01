/**
 * Выручка по дням — плавная линия с заливкой под ней.
 *
 * Раньше здесь стояли столбики. Владелец нарисовал линию, и для этих данных она
 * честнее: столбик читается как «отдельная величина», а выручка по дням —
 * непрерывный ход, у которого важна форма, а не высота каждого дня.
 *
 * Рисует Recharts через обвязку Chart из реестра shadcn/ui — владелец прислал
 * ссылку на неё. До этого график был написан руками; вид остался прежним, но
 * пропало полсотни строк своей математики: попадание курсора в день, прореживание
 * подписей под узкую карточку и удержание подсказки в границах карточки теперь
 * не наши заботы. Взамен на страницу приезжает библиотека — «Статистика» грузится
 * отдельным куском (см. App.tsx), так что остальных разделов это не касается.
 *
 * Подписей по левому краю нет — это макет. Значит, число должно доставаться
 * иначе: наводишь на график, и день с суммой показываются в подсказке.
 */
import { useId } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceDot, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "../ui/chart";
import type { ChartConfig } from "../ui/chart";

type Point = {
  label: string;
  revenue: number;
  revenue_text: string;
};

const config = {
  revenue: { label: "Выручка", color: "var(--chart-1)" },
} satisfies ChartConfig;

export function RevenueArea({ data }: { data: Point[] }) {
  const gradient = useId().replace(/:/g, "");

  if (data.length === 0) return null;

  const last = data[data.length - 1];

  return (
    <ChartContainer className="chart-area" config={config}>
      <AreaChart data={data} margin={{ top: 16, right: 20, bottom: 0, left: 20 }}>
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-revenue)" stopOpacity={0.24} />
            <stop offset="100%" stopColor="var(--color-revenue)" stopOpacity={0} />
          </linearGradient>
        </defs>

        {/* Ось слева спрятана: цифры даёт подсказка. Нужна она ради делений —
            по ним строятся линии-опоры, а без оси их было бы пять вместо
            четырёх, и сетка спорила бы с самой кривой. */}
        <YAxis hide domain={[0, "dataMax"]} tickCount={4} />
        <CartesianGrid vertical={false} />

        {/* minTickGap прореживает даты сам: на широкой карточке подписей больше,
            на телефоне меньше, склеиться в кашу они не могут. */}
        <XAxis
          dataKey="label"
          tickLine={false}
          axisLine={false}
          tickMargin={10}
          minTickGap={28}
          interval="preserveStartEnd"
        />

        <ChartTooltip
          cursor={{ stroke: "#ccd4e6", strokeWidth: 1, strokeDasharray: "4 4" }}
          content={
            <ChartTooltipContent
              className="area-tip"
              hideIndicator
              formatter={(_value, _name, item) => (
                <span className="area-tip-value">{(item.payload as Point).revenue_text}</span>
              )}
            />
          }
        />

        <Area
          dataKey="revenue"
          type="monotone"
          fill={`url(#${gradient})`}
          stroke="var(--color-revenue)"
          strokeWidth={2.6}
          strokeLinecap="round"
          strokeLinejoin="round"
          dot={false}
          /* Recharts по умолчанию «рисует» линию при каждом появлении данных.
             Здесь это лишнее: период переключают прямо на странице, и график
             заново прочерчивался бы после каждого нажатия. */
          isAnimationActive={false}
          activeDot={{ r: 5, fill: "var(--chart-1)", stroke: "#fff", strokeWidth: 3 }}
        />

        {/* Точка на последнем дне: без курсора взгляд должен видеть, где «сейчас».
            Белое кольцо — иначе на заливке того же цвета точка пропадает. */}
        <ReferenceDot
          x={last.label}
          y={last.revenue}
          r={5}
          fill="var(--chart-1)"
          stroke="#fff"
          strokeWidth={3}
        />
      </AreaChart>
    </ChartContainer>
  );
}
