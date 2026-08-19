/**
 * Мост в Telegram: тот же файл открывается и в мессенджере, и в браузере.
 *
 * Одна сборка обслуживает три случая: витрину у покупателя, ту же CRM у
 * владельца внутри Telegram и её же в браузере на компьютере. Различает их
 * ровно одно — есть ли подписанный initData. Он приходит от клиента Telegram,
 * подделать его нельзя (проверка на сервере, web/auth.py), и в браузере он
 * пустой.
 *
 * Про «нативные» элементы. У мини-приложения есть свои кнопка внизу экрана и
 * стрелка «назад» в шапке — рисовать их самим нельзя: они принадлежат клиенту
 * Telegram и живут ЗА пределами страницы. Поэтому здесь не компоненты, а
 * подписки: React говорит, что должно быть на кнопке, а показывает её мессенджер.
 */

/** То немногое, чем мы пользуемся из API мини-приложений. */
type MainButton = {
  text: string;
  isVisible: boolean;
  setText(text: string): void;
  show(): void;
  hide(): void;
  enable(): void;
  disable(): void;
  showProgress(leaveActive?: boolean): void;
  hideProgress(): void;
  onClick(handler: () => void): void;
  offClick(handler: () => void): void;
  setParams(params: { color?: string; text_color?: string; is_active?: boolean }): void;
};

type BackButton = {
  isVisible: boolean;
  show(): void;
  hide(): void;
  onClick(handler: () => void): void;
  offClick(handler: () => void): void;
};

type WebApp = {
  initData: string;
  colorScheme: "light" | "dark";
  themeParams: Record<string, string>;
  isExpanded: boolean;
  MainButton: MainButton;
  BackButton: BackButton;
  HapticFeedback?: {
    impactOccurred(style: "light" | "medium" | "heavy"): void;
    notificationOccurred(type: "error" | "success" | "warning"): void;
  };
  ready(): void;
  expand(): void;
  close(): void;
  onEvent(event: string, handler: () => void): void;
  offEvent(event: string, handler: () => void): void;
};

declare global {
  interface Window {
    Telegram?: { WebApp?: WebApp };
  }
}

export function webApp(): WebApp | null {
  return window.Telegram?.WebApp ?? null;
}

/**
 * Подписанные данные о том, кто открыл приложение.
 *
 * Пустая строка означает «мы в обычном браузере». Это не ошибка: панель на
 * компьютере так и работает, и заголовок с пустым значением слать не нужно —
 * сервер по его отсутствию и понимает, что перед ним браузер.
 */
export function initData(): string {
  return webApp()?.initData ?? "";
}

/** Открыто ли приложение внутри Telegram. */
export function inTelegram(): boolean {
  return initData().length > 0;
}

/**
 * Подготовка окна: сообщить о готовности и развернуть на весь экран.
 *
 * Без expand() мини-приложение открывается на половину высоты, и витрина
 * начинается с того, что человеку нужно тянуть её вверх.
 */
export function setupWebApp(): void {
  const app = webApp();
  if (!app) return;
  app.ready();
  if (!app.isExpanded) app.expand();
}

/**
 * Тема Telegram → тема панели.
 *
 * Внутри мессенджера выбор темы человеку не принадлежит: он уже сделан в
 * настройках Telegram, и светлая витрина в тёмном клиенте выглядит как чужое
 * окно. Поэтому здесь мы не спрашиваем, а следуем — и следим за сменой на лету
 * (событие themeChanged приходит, когда тему меняют, не закрывая приложение).
 */
export function followTelegramTheme(): () => void {
  const app = webApp();
  if (!app) return () => {};

  const apply = () => {
    document.documentElement.classList.toggle("dark", app.colorScheme === "dark");
  };
  apply();
  app.onEvent("themeChanged", apply);
  return () => app.offEvent("themeChanged", apply);
}

/** Короткий отклик на нажатие. Молчит там, где его нет (старые клиенты). */
export function haptic(kind: "tap" | "ok" | "error" = "tap"): void {
  const feedback = webApp()?.HapticFeedback;
  if (!feedback) return;
  if (kind === "tap") feedback.impactOccurred("light");
  if (kind === "ok") feedback.notificationOccurred("success");
  if (kind === "error") feedback.notificationOccurred("error");
}

export function closeWebApp(): void {
  webApp()?.close();
}
