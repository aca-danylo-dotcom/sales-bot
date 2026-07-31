/**
 * «Точно удалить?» — свой диалог вместо браузерного confirm().
 *
 * Спрашиваем ровно там же, где спрашивали раньше: удаление товара и удаление
 * варианта. Причина замены проста — `confirm()` останавливает всю страницу и
 * выглядит чужим окном системы, а панель должна оставаться собой. Поведение
 * сохранено: пока не подтвердили, ничего не происходит.
 *
 * Хук отдаёт функцию, которая возвращает обещание: `if (await ask(…))`. Так
 * место вызова читается тем же порядком, что и прежний `confirm()`.
 */
import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";

type Request = {
  title: string;
  hint?: string;
  confirmText?: string;
  danger?: boolean;
};

type Ask = (request: Request) => Promise<boolean>;

const ConfirmContext = createContext<Ask | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<Request | null>(null);
  const decide = useRef<((answer: boolean) => void) | null>(null);

  const ask = useCallback<Ask>((next) => {
    setRequest(next);
    return new Promise<boolean>((resolve) => {
      decide.current = resolve;
    });
  }, []);

  const answer = (value: boolean) => {
    setRequest(null);
    decide.current?.(value);
    decide.current = null;
  };

  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      {request ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/25 p-4"
          role="dialog"
          aria-modal="true"
          // Клик по затемнению — то же, что «Отмена»: закрыть, ничего не сделав.
          onClick={() => answer(false)}
        >
          <div
            className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="strong">{request.title}</p>
            {request.hint ? <p className="muted small">{request.hint}</p> : null}
            <div className="actions" style={{ marginTop: 18 }}>
              <button
                className={request.danger ? "btn danger-btn" : "btn primary"}
                type="button"
                autoFocus
                onClick={() => answer(true)}
              >
                {request.confirmText ?? "Удалить"}
              </button>
              <button className="btn ghost" type="button" onClick={() => answer(false)}>
                Отмена
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext);
  if (!ask) throw new Error("useConfirm вызван вне ConfirmProvider");
  return ask;
}
