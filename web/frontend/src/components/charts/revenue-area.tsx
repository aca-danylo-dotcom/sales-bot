/**
 * Выручка по дням — областной график.
 *
 * Собран по демо со страницы Chart реестра shadcn/ui, на которую владелец
 * прислал ссылку: те же настройки заливки (плотная сверху, почти прозрачная
 * снизу), та же сетка, тот же вид подсказки — белая карточка с точкой цвета
 * ряда. Отличий от их примера два, и оба вынужденные:
 *
 * 1. Ряд один. У них их два ради красоты примера, у нас на этой оси есть
 *    только деньги: класть рядом число заказов значило бы мерить штуки
 *    гривнами.
 * 2. Сумму в подсказке рисуем сами. Их разметка показывает голое число, а
 *    сервер уже присылает её словами — «2 400 грн». Раскладка строки при этом
 *    ровно их: точка, название ряда, значение справа.
 * 3. Сглаживание `monotone` вместо их `natural`. На ровных данных примера
 *    разницы не видно, а у нас между продажами стоят нули, и `natural`
 *    вылетала за них горбами: график показывал выручку в дни, когда не
 *    продали ничего. `monotone` за крайние точки не выходит.
 */
import { useId } from "react";
import { Area, AreaChart, CartesianGrid, XAxis } from "recharts";

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

  return (
    <ChartContainer className="chart-area aspect-auto h-[250px] w-full" config={config}>
      {/* Поля по бокам — под крайние подписи: они стоят по центру своего дня,
          и без запаса «01.08» срезалось бы краем карточки. */}
      <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 16 }}>
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--color-revenue)" stopOpacity={0.8} />
            <stop offset="95%" stopColor="var(--color-revenue)" stopOpacity={0.1} />
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
          cursor={false}
          content={
            <ChartTooltipContent
              indicator="dot"
              formatter={(_value, _name, item) => (
                <>
                  <div
                    className="h-2.5 w-2.5 shrink-0 rounded-[2px]"
                    style={{ background: "var(--color-revenue)" }}
                  />
                  <div className="flex flex-1 items-center justify-between gap-3 leading-none">
                    <span className="text-muted-foreground">Выручка</span>
                    <span className="font-medium tabular-nums">
                      {(item.payload as Point).revenue_text}
                    </span>
                  </div>
                </>
              )}
            />
          }
        />

        <Area
          dataKey="revenue"
          type="monotone"
          fill={`url(#${gradient})`}
          stroke="var(--color-revenue)"
        />
      </AreaChart>
    </ChartContainer>
  );
}
