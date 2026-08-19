/**
 * Кнопки Telegram как React-хуки.
 *
 * Главная кнопка внизу экрана и стрелка «назад» в шапке принадлежат клиенту
 * Telegram, а не странице: их нельзя отрисовать, можно только попросить
 * показать. Отсюда форма хуков — они описывают, что должно быть на кнопке
 * сейчас, и убирают её за собой при уходе со страницы.
 *
 * Почему обработчик хранится в ref. Telegram помнит переданную функцию до
 * offClick, а React пересоздаёт её на каждом рендере. Подписывайся мы напрямую,
 * на кнопке копилось бы по обработчику на рендер — и одно нажатие оформляло бы
 * три заказа. Подписка живёт одна, а внутрь смотрит всегда свежий ref.
 */
import { useEffect, useRef } from "react";

import { webApp } from "./telegram";

type MainButtonOptions = {
  text: string;
  onClick: () => void;
  /** Кнопка видна. Скрытая не мешает: место внизу экрана освобождается. */
  visible?: boolean;
  /** Нажать нельзя — форма не заполнена, корзина пуста. */
  disabled?: boolean;
  /** Крутилка вместо текста: заказ уже отправляется. */
  loading?: boolean;
};

export function useMainButton({
  text,
  onClick,
  visible = true,
  disabled = false,
  loading = false,
}: MainButtonOptions): void {
  const handler = useRef(onClick);
  handler.current = onClick;

  useEffect(() => {
    const button = webApp()?.MainButton;
    if (!button) return;

    const listener = () => handler.current();
    button.onClick(listener);
    return () => {
      button.offClick(listener);
      button.hide();
      button.hideProgress();
    };
  }, []);

  useEffect(() => {
    const button = webApp()?.MainButton;
    if (!button) return;

    button.setText(text);
    if (visible) button.show();
    else button.hide();

    // Порядок важен: showProgress сам гасит кнопку, и enable() после него
    // вернул бы её в рабочий вид с крутилкой — вид «нажми ещё раз».
    if (disabled) button.disable();
    else button.enable();
    if (loading) button.showProgress(false);
    else button.hideProgress();
  }, [text, visible, disabled, loading]);
}

/**
 * Стрелка «назад» в шапке Telegram.
 *
 * Нужна не для красоты: внутри мини-приложения нет адресной строки и жеста
 * назад — без неё с карточки товара можно уйти только закрыв приложение.
 */
export function useBackButton(onBack: (() => void) | null): void {
  const handler = useRef(onBack);
  handler.current = onBack;

  useEffect(() => {
    const button = webApp()?.BackButton;
    if (!button) return;

    const listener = () => handler.current?.();
    button.onClick(listener);
    return () => {
      button.offClick(listener);
      button.hide();
    };
  }, []);

  useEffect(() => {
    const button = webApp()?.BackButton;
    if (!button) return;
    if (onBack) button.show();
    else button.hide();
  }, [onBack]);
}
