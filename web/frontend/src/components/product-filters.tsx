/**
 * Отбор товаров: строка поиска и три списка.
 *
 * Один компонент на две вкладки. «Список товаров» и «Остатки по размерам» —
 * два вида одного каталога с общим отбором, и раньше форма была написана в
 * каждой странице отдельно, слово в слово. Любая правка требовала помнить про
 * второе место; теперь его нет.
 *
 * Списки — компонент Select из реестра shadcn/ui вместо системного <select>:
 * см. причину в components/ui/select.tsx.
 */
import type { ReactNode } from "react";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";

export type ProductFilterValues = {
  q: string;
  category: string;
  status: string;
  stock: string;
};

/* «Не отбирать» в адресе — пустая строка, а ключом пункта она быть не может:
   для React Aria пустой ключ неотличим от «ничего не выбрано», и список
   открывался бы без галочки. Внутри списков вместо неё стоит эта метка. */
const ANY = "__any";

type Option = { value: string; label: string };

const STATUS: Option[] = [
  { value: "", label: "Все" },
  { value: "active", label: "В продаже" },
  { value: "hidden", label: "Скрытые" },
];

const STOCK: Option[] = [
  { value: "", label: "Любой остаток" },
  { value: "in", label: "Есть в наличии" },
  { value: "out", label: "Закончились" },
];

type Props = {
  categories: string[];
  draft: ProductFilterValues;
  onChange: (next: ProductFilterValues) => void;
  onSubmit: () => void;
  /** «Сбросить» — ссылка на свою вкладку, поэтому её даёт страница. */
  reset?: ReactNode;
};

export function ProductFilters({ categories, draft, onChange, onSubmit, reset }: Props) {
  return (
    <form
      className="filters card"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <input
        type="search"
        value={draft.q}
        autoComplete="off"
        placeholder="Название, артикул или id"
        onChange={(event) => onChange({ ...draft, q: event.target.value })}
      />

      <Picker
        label="Категория"
        value={draft.category}
        options={[
          { value: "", label: "Все категории" },
          ...categories.map((item) => ({ value: item, label: item })),
        ]}
        onPick={(category) => onChange({ ...draft, category })}
      />
      <Picker
        label="Статус"
        value={draft.status}
        options={STATUS}
        onPick={(status) => onChange({ ...draft, status })}
      />
      <Picker
        label="Остаток"
        value={draft.stock}
        options={STOCK}
        onPick={(stock) => onChange({ ...draft, stock })}
      />

      <button className="btn" type="submit">
        Показать
      </button>
      {reset}
    </form>
  );
}

function Picker({
  label,
  value,
  options,
  onPick,
}: {
  label: string;
  value: string;
  options: Option[];
  onPick: (value: string) => void;
}) {
  return (
    <Select
      // Подписи над списками нет — её роль играет сам выбранный пункт («Все
      // категории»). Тем, кто читает страницу голосом, подпись нужна всё равно.
      aria-label={label}
      selectedKey={value === "" ? ANY : value}
      onSelectionChange={(key) => onPick(key === null || key === ANY ? "" : String(key))}
    >
      <SelectTrigger>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {options.map((option) => (
            <SelectItem key={option.value} id={option.value === "" ? ANY : option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}
