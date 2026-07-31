/**
 * Результат последнего действия — строка вверху страницы.
 *
 * Раньше он приезжал в адресе (`?ok=confirmed`), а текст брался из словаря на
 * сервере. Теперь текст приходит прямо в ответе, но правило то же: показываем
 * ровно то, что сказал сервер, и различаем три вида сообщений.
 *
 *   ok   — получилось;
 *   warn — «сделано, но с оговоркой»: заказ отправлен, а клиенту не дошло.
 *          Прятать такое в успех нельзя, объявлять ошибкой — тоже;
 *   err  — не получилось.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

type FlashKind = "ok" | "warn" | "err";
type Flash = { kind: FlashKind; text: string; key: number };

type FlashApi = {
  /** Ответ действия: сообщение и, если есть, оговорка. */
  report: (result: { message?: string; warning?: string }) => void;
  fail: (text: string) => void;
  clear: () => void;
};

const FlashContext = createContext<FlashApi | null>(null);
const CurrentFlash = createContext<Flash[]>([]);

// Сколько сообщение висит. Успех прячем сам, ошибку и оговорку — нет: их
// человек должен успеть прочитать, даже если отошёл от стола.
const OK_LIFETIME = 6000;

export function FlashProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Flash[]>([]);
  const counter = useRef(0);
  const timer = useRef<number | undefined>(undefined);

  const show = useCallback((next: Flash[]) => {
    window.clearTimeout(timer.current);
    setItems(next);
    // Таймер ставим только если на экране один успех и ничего тревожного.
    if (next.length === 1 && next[0].kind === "ok") {
      timer.current = window.setTimeout(() => setItems([]), OK_LIFETIME);
    }
  }, []);

  const api = useMemo<FlashApi>(() => {
    const make = (kind: FlashKind, text: string): Flash => ({
      kind,
      text,
      key: ++counter.current,
    });
    return {
      report: ({ message, warning }) => {
        const next: Flash[] = [];
        if (message) next.push(make("ok", message));
        if (warning) next.push(make("warn", warning));
        if (next.length) show(next);
      },
      fail: (text) => show([make("err", text)]),
      clear: () => show([]),
    };
  }, [show]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <FlashContext.Provider value={api}>
      <CurrentFlash.Provider value={items}>{children}</CurrentFlash.Provider>
    </FlashContext.Provider>
  );
}

export function useFlash(): FlashApi {
  const api = useContext(FlashContext);
  if (!api) throw new Error("useFlash вызван вне FlashProvider");
  return api;
}

/** Сами строки. Стоят первыми в `main.page` — как и в прежней панели. */
export function FlashMessages() {
  const items = useContext(CurrentFlash);
  return (
    <>
      {items.map((item) => (
        <p key={item.key} className={`flash ${item.kind}`}>
          {item.text}
        </p>
      ))}
    </>
  );
}
