/**
 * Продажи по дням — столбики с переключателем, что ими мерить.
 *
 * Собрано по демо «Bar Chart · Interactive» из реестра shadcn/ui, владелец
 * прислал снимок именно его: шапка с названием слева и двумя крупными числами
 * справа, каждое из которых переключает ряд, под ней — прямоугольники по дням.
 *
 * У них два ряда про одно и то же (заходы с компьютера и с телефона), у нас
 * два разных измерения одних заказов: сколько денег и сколько заказов. Поэтому
 * подпись под названием объясняет, что значит столбик, а сумма в подсказке
 * приходит от сервера уже словами — «2 400 грн», а не голым числом.
 *
 * Что именно считает сервер (db/queries.py::revenue_by_day): «Принято» — только
 * подтверждённая оплата, «Заказов» — все заказы дня, кроме отменённых. Числа в
 * шапке — итог по видимым столбикам, а не отдельно посчитанная цифра: иначе
 * шапка и график однажды разойдутся, и поверят шапке.
 */
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "../ui/chart";
import type { ChartConfig } from "../ui/chart";

type Point = {
  label: string;
  orders: number;
  revenue: number;
  revenue_text: string;
};

type Series = "revenue" | "orders";

const config = {
  revenue: { label: "Принято", color: "var(--chart-1)" },
  orders: { label: "Заказов", color: "var(--chart-1)" },
} satisfies ChartConfig;

type Props = {
  data: Point[];
  /** Итоги по столбикам: деньги сервер уже сложил и оформил, заказы — число. */
  totals: { revenue_text: string; orders: number };
};

export function RevenueBars({ data, totals }: Props) {
  const [active, setActive] = useState<Series>("revenue");

  if (data.length === 0) return null;

  const total = { revenue: totals.revenue_text, orders: String(totals.orders) };

  return (
    <div className="chart-card">
      <div className="chart-head">
        <div className="chart-head-title">
          <span className="chart-head-name">Продажи по дням</span>
          <span className="chart-head-note">
            Столбик — один день. Нажмите на число справа, чтобы посмотреть другое.
          </span>
        </div>

        <div className="chart-tabs">
          {(["revenue", "orders"] as Series[]).map((series) => (
            <button
              key={series}
              type="button"
              className="chart-tab"
              data-active={active === series}
              aria-pressed={active === series}
              onClick={() => setActive(series)}
            >
              <span className="chart-tab-title">{config[series].label}</span>
              <span className="chart-tab-value">{total[series]}</span>
            </button>
          ))}
        </div>
      </div>

      <ChartContainer className="chart-area aspect-auto h-[250px] w-full" config={config}>
        <BarChart accessibilityLayer data={data} margin={{ left: 12, right: 12 }}>
          {/* Градиент палитры на весь график, а не на каждый столбик: единицы
              `userSpaceOnUse` считают проценты от ширины картинки, поэтому
              переход идёт слева направо через все дни. При заливке по умолчанию
              (`objectBoundingBox`) каждый столбик получил бы свой полный
              переход, и вместе они выглядели бы полосатыми. */}
          <defs>
            <linearGradient id="bars-accent" gradientUnits="userSpaceOnUse" x1="0%" y1="0" x2="100%" y2="0">
              <stop offset="0%" stopColor="#5227ff" />
              <stop offset="55%" stopColor="#b497cf" />
              <stop offset="100%" stopColor="#ff9ffc" />
            </linearGradient>
          </defs>

          <CartesianGrid vertical={false} />

          {/* minTickGap прореживает даты сам: на широкой карточке подписей
              больше, на телефоне меньше, склеиться в кашу они не могут. */}
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            minTickGap={32}
          />

          <ChartTooltip
            content={
              <ChartTooltipContent
                className="w-[170px]"
                formatter={(cell, _name, item) => (
                  <>
                    <div
                      className="h-2.5 w-2.5 shrink-0 rounded-[2px]"
                      style={{ background: `var(--color-${active})` }}
                    />
                    <div className="flex flex-1 items-center justify-between gap-3 leading-none">
                      <span className="text-muted-foreground">{config[active].label}</span>
                      <span className="font-medium tabular-nums">
                        {active === "revenue"
                          ? (item.payload as Point).revenue_text
                          : String(cell)}
                      </span>
                    </div>
                  </>
                )}
              />
            }
          />

          {/* Без роста из нуля: переключатель «Принято ↔ Заказов» перерисовывает
              ряд на каждое нажатие, и столбики каждый раз уезжали бы вниз и
              ползли обратно. Заодно график виден сразу, а не через полсекунды. */}
          <Bar dataKey={active} fill="url(#bars-accent)" isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
    </div>
  );
}
