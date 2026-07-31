/**
 * Разговор с сервером — одно место на всю панель.
 *
 * Сервер отвечает в одном формате (см. web/api/helpers.py): успех — объект с
 * данными, отказ — HTTP 4xx и `{"error": "текст"}`. Тексты уже написаны
 * по-русски и по-человечески, поэтому клиент их не сочиняет заново, а
 * показывает как есть.
 *
 * Отдельно разобраны два случая:
 *   * 409 — не ошибка ввода, а разъехавшееся состояние: заказ уже подтвердил
 *     кто-то другой. Панель на такое молча перечитывает карточку.
 *   * 400 с полем `problems` — проверка формы. Список претензий показываем
 *     рядом с формой, а не тостом.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly problems: string[];

  constructor(message: string, status: number, problems: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problems = problems;
  }

  /** Состояние изменилось у кого-то другого — надо просто перечитать данные. */
  get conflict(): boolean {
    return this.status === 409;
  }
}

/** Разбор ответа: и успех, и отказ приходят как JSON. */
async function parse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (response.ok) {
    return (data ?? {}) as T;
  }

  const payload = (data ?? {}) as { error?: string; problems?: string[] };
  // Сети и прокси иногда отдают свой HTML вместо нашего JSON — тогда текста
  // от сервера нет и приходится сказать хоть что-то осмысленное.
  const message =
    payload.error || `Сервер ответил ошибкой ${response.status}. Попробуйте ещё раз.`;
  throw new ApiError(message, response.status, payload.problems ?? []);
}

async function send<T>(url: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    // Сюда попадаем при обрыве связи: ответа нет вообще, статуса тоже.
    throw new ApiError("Нет связи с сервером. Проверьте интернет.", 0);
  }
  return parse<T>(response);
}

export function get<T>(url: string, signal?: AbortSignal): Promise<T> {
  return send<T>(url, { signal, headers: { Accept: "application/json" } });
}

/**
 * Действие. Тело — либо объект (уедет JSON), либо FormData (фотографии).
 *
 * FormData отдаём браузеру как есть и Content-Type не трогаем: его должен
 * проставить сам браузер вместе с разделителем multipart, иначе сервер не
 * сможет разобрать файлы.
 */
export function post<T>(url: string, data?: Record<string, unknown> | FormData): Promise<T> {
  if (data instanceof FormData) {
    return send<T>(url, { method: "POST", body: data });
  }
  return send<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(data ?? {}),
  });
}

/** Ответ на действие: что сказать человеку. */
export type ActionResult = {
  message?: string;
  warning?: string;
  [key: string]: unknown;
};

/** Строка запроса без пустых значений — чтобы в адресе не висело `?q=&from=`. */
export function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
