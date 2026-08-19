/**
 * Вход в панель с компьютера.
 *
 * Показывается только в браузере и только если пароль задан в настройках
 * (см. web/api/session.py). Внутри Telegram этого экрана не бывает: там
 * владельца опознаёт мессенджер, и спрашивать пароль было бы работой на
 * пустом месте.
 *
 * Одно поле и никакого логина: панель принадлежит одному человеку.
 */
import { useState } from "react";

import { ApiError, post } from "./api/client";

export default function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!password || busy) return;

    setBusy(true);
    setFailure("");
    try {
      await post("/api/login", { password });
      onDone();
    } catch (reason) {
      setFailure(reason instanceof ApiError ? reason.message : "Не получилось войти.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit}>
        <h1>Панель магазина</h1>
        <p className="muted">Введите пароль, чтобы увидеть заказы и товары.</p>

        <input
          type="password"
          autoFocus
          autoComplete="current-password"
          placeholder="Пароль"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        {failure ? <p className="login-error">{failure}</p> : null}

        <button className="btn primary" type="submit" disabled={busy || !password}>
          {busy ? "Проверяем…" : "Войти"}
        </button>
      </form>
    </main>
  );
}
