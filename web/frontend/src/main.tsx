import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import { ConfirmProvider } from "./components/confirm";
import { FlashProvider } from "./lib/flash";
import "./styles/app.css";

/**
 * Общие правила для всех запросов.
 *
 * `retry: 1` — одна повторная попытка: панель открыта в браузере продавца, и
 * короткий провал связи не должен выглядеть поломкой. Больше не нужно, иначе
 * настоящая ошибка сервера доходит до человека с задержкой в несколько секунд.
 *
 * `refetchOnWindowFocus` оставляем включённым (это умолчание): менеджер
 * возвращается на вкладку и видит свежие данные, а не то, что было утром.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000 },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <FlashProvider>
          <ConfirmProvider>
            <App />
          </ConfirmProvider>
        </FlashProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
);
