/**
 * Действие панели: нажали кнопку — сервер сделал — экран обновился.
 *
 * Одна обёртка на все кнопки, потому что после любого действия происходит одно
 * и то же:
 *
 *   * ответ сервера показывается человеку (сообщение и, если есть, оговорка);
 *   * затронутые данные перечитываются — подтвердил оплату, и список сам
 *     показал новый статус, без «обновите страницу»;
 *   * пока запрос летит, кнопка выключена (`isPending`) — этим закрыт двойной
 *     клик, ради которого раньше жил отдельный скрипт;
 *   * ответ 409 значит «состояние уехало»: кто-то из коллег успел раньше.
 *     Такое тоже перечитываем — человек должен увидеть, как есть на самом деле.
 */
import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";

import { post, type ActionResult, type ApiError } from "../api/client";
import { useFlash } from "./flash";

type Payload = Record<string, unknown> | FormData | undefined;

export function useAction(
  options: {
    invalidate?: QueryKey[];
    onDone?: (result: ActionResult) => void;
  } = {}
) {
  const flash = useFlash();
  const client = useQueryClient();

  const refresh = () => {
    for (const key of options.invalidate ?? []) {
      void client.invalidateQueries({ queryKey: key });
    }
  };

  return useMutation<ActionResult, ApiError, { url: string; data?: Payload }>({
    mutationFn: ({ url, data }) => post<ActionResult>(url, data),
    onSuccess: (result) => {
      flash.report(result);
      refresh();
      options.onDone?.(result);
    },
    onError: (error) => {
      flash.fail(error.message);
      if (error.conflict) refresh();
    },
  });
}
