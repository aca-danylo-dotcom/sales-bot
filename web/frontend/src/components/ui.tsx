/**
 * Мелкие детали, из которых собраны все разделы.
 *
 * Классы здесь — те же, что были в шаблонах Jinja: вид панели переносится один
 * в один, и стили для них уже описаны в styles/app.css. Поэтому компоненты
 * почти ничего не решают сами — они лишь избавляют от повторения разметки.
 */
import type { MouseEvent, ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

/** Заголовок раздела и подпись под ним. */
export function Head({
  title,
  lead,
  children,
}: {
  title: ReactNode;
  lead?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="head">
      <div>
        <h1>{title}</h1>
        {lead ? <p className="lead">{lead}</p> : null}
      </div>
      {children ? <div className="head-actions">{children}</div> : null}
    </div>
  );
}

/** Пока данные едут. Специально скромно: мигать всей страницей незачем. */
export function Loading({ text = "Загружаем…" }: { text?: string }) {
  return <p className="muted">{text}</p>;
}

/** Данные не пришли. Показываем текст сервера и даём повторить. */
export function LoadError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const text = error instanceof Error ? error.message : "Не удалось загрузить данные.";
  return (
    <div className="card empty-state">
      <p>{text}</p>
      {onRetry ? (
        <p className="actions">
          <button className="btn" type="button" onClick={onRetry}>
            Повторить
          </button>
        </p>
      ) : null}
    </div>
  );
}

/** Статус заказа или состояние товара — цветной ярлык. */
export function Tag({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`tag ${kind}`}>{children}</span>;
}

/** Пустой список: что случилось и что с этим делать. */
export function EmptyState({ title, hint }: { title: ReactNode; hint?: ReactNode }) {
  return (
    <div className="card empty-state">
      <p>{title}</p>
      {hint ? <p className="muted">{hint}</p> : null}
    </div>
  );
}

/**
 * Страницы списка. Ссылки настоящие: их можно открыть в новой вкладке и
 * отправить коллеге — как и раньше, когда страницу рисовал сервер.
 */
export function Pager({
  page,
  pages,
  to,
}: {
  page: number;
  pages: number;
  to: (page: number) => string;
}) {
  if (pages <= 1) return null;
  return (
    <nav className="pager">
      {page > 1 ? (
        <Link className="btn ghost" to={to(page - 1)}>
          Назад
        </Link>
      ) : null}
      <span className="muted">
        Страница {page} из {pages}
      </span>
      {page < pages ? (
        <Link className="btn ghost" to={to(page + 1)}>
          Вперёд
        </Link>
      ) : null}
    </nav>
  );
}

/** Ссылка «← назад», первой строкой карточки. */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <p className="back">
      <Link to={to}>{children}</Link>
    </p>
  );
}

/** Список претензий к форме — там же, где был на серверной странице. */
export function Problems({ items, title = "Не сохранил:" }: { items: string[]; title?: string }) {
  if (!items.length) return null;
  return (
    <div className="card problems">
      <p className="strong">{title}</p>
      <ul>
        {items.map((text) => (
          <li key={text}>{text}</li>
        ))}
      </ul>
    </div>
  );
}

/** Первые 16 символов даты из базы: «2026-07-31 14:05». */
export function stamp(value?: string | null): string {
  return value ? value.slice(0, 16) : "—";
}

/**
 * Строка таблицы, которая открывается вся целиком.
 *
 * Целиться в название товара или в номер заказа — работа, которой быть не
 * должно: строка и так про один заказ, значит нажимать можно куда угодно
 * в её пределах.
 *
 * Ссылка в первой ячейке при этом ОСТАЁТСЯ, и не для красоты: это
 * единственный способ открыть заказ в новой вкладке средней кнопкой,
 * скопировать адрес и пройти по списку с клавиатуры. Строка сама по себе
 * ничего этого не умеет — она лишь расширяет область нажатия.
 *
 * Два случая, когда нажатие не считается переходом:
 *
 * 1. Нажали по ссылке или другому управлению внутри строки — там своё
 *    действие, и подменять его переходом в карточку нельзя.
 * 2. В строке что-то выделено мышью. Копирование телефона протяжкой — обычное
 *    дело, и прыгать из-за него на другую страницу значит терять выделенное.
 */
export function useRowLink() {
  const navigate = useNavigate();

  return (to: string) => ({
    className: "row-link",
    onClick: (event: MouseEvent<HTMLTableRowElement>) => {
      if ((event.target as HTMLElement).closest("a, button, input, label, select, textarea")) {
        return;
      }
      if (window.getSelection()?.toString()) return;
      navigate(to);
    },
  });
}
