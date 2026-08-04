/**
 * Тема панели: светлая или тёмная.
 *
 * Вся тема — один класс `dark` на <html>: цвета берутся из переменных CSS (см.
 * styles/app.css), и подмена класса перекрашивает панель целиком, включая
 * компоненты реестра с их `dark:`-классами.
 *
 * Хранилища два, и это не дублирование: `localStorage` переживает перезагрузку,
 * а класс на <html> — то, что видит браузер прямо сейчас. Первый раз класс
 * ставит скрипт в index.html, до отрисовки; дальше — этот модуль.
 *
 * По настройкам системы панель не темнеет: светлая — вид по умолчанию, тёмную
 * человек включает сам, и выбор запоминается (решение владельца).
 */
import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

/* Тот же ключ читает скрипт в index.html — менять только вместе с ним. */
const STORAGE_KEY = "crm-theme";

function readStored(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    // Приватный режим: хранилище запрещено. Тема просто не запомнится.
    return "light";
  }
}

function apply(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* см. выше */
  }
}

/** Текущая тема и переключатель. Значение общее для всех, кто позвал хук. */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(readStored);

  // Панель открывают в двух вкладках сразу («Заказы» и «Статистика» рядом) —
  // переключение в одной должно догонять вторую, иначе половина окон остаётся
  // светлой и человек думает, что кнопка сработала через раз.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY) return;
      const next: Theme = event.newValue === "dark" ? "dark" : "light";
      setThemeState(next);
      document.documentElement.classList.toggle("dark", next === "dark");
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    apply(next);
    setThemeState(next);
  }, []);

  const toggleTheme = useCallback(
    () => setTheme(readStored() === "dark" ? "light" : "dark"),
    [setTheme],
  );

  return { theme, setTheme, toggleTheme };
}
