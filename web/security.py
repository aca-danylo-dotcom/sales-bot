"""Пароли и сессии веб-CRM.

Два независимых механизма, оба на stdlib:

  * пароль в базе лежит только как scrypt-хеш с индивидуальной солью — из него
    исходный пароль не восстановить, поэтому утечка базы не даёт войти;
  * сессия — подписанная HMAC cookie: сервер ничего о ней не хранит, а подделать
    её нельзя, не зная WEB_SECRET.

Хранить сессии в памяти процесса было бы проще, но тогда каждый рестарт бота
выкидывал бы менеджеров из панели — а бот перезапускается при любом деплое.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

import config

# Параметры scrypt. n=2**14 — примерно 50–100 мс на проверку: незаметно для
# входящего человека и дорого для перебора. Менять их у работающей базы нельзя:
# в хеше записаны те, с которыми он посчитан (см. verify_password).
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

# Хеш заведомо несуществующего пароля. Нужен, чтобы вход с несуществующим
# логином занимал столько же времени, сколько с существующим: иначе по времени
# ответа перебирается список логинов.
_DUMMY_HASH = ""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """Хеш пароля в виде строки для базы: scrypt$n$r$p$соль$ключ."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_BYTES,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Проверяет пароль против хеша из базы.

    Параметры берём из самой строки, а не из констант модуля: если завтра n
    вырастет, старые хеши обязаны продолжать проверяться — иначе все, кто завёл
    пароль раньше, разом потеряют вход.
    """
    try:
        algo, n, r, p, salt_b64, key_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt, expected = _b64d(salt_b64), _b64d(key_b64)
        actual = hashlib.scrypt(
            password.encode(), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        # Битая или чужого формата строка — это «пароль не подошёл», а не сбой:
        # 500-я на странице логина сказала бы атакующему, что логин существует.
        return False
    return hmac.compare_digest(actual, expected)


def waste_password_time() -> None:
    """Считает хеш впустую — чтобы неверный логин стоил столько же, сколько верный.

    Без этого страница логина отвечает на несуществующий логин мгновенно, а на
    существующий — с задержкой scrypt, и по разнице во времени чужие логины
    перебираются, даже не зная ни одного пароля.
    """
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    verify_password("", _DUMMY_HASH)


# ─────────────────────────── Сессия в cookie ───────────────────────────

COOKIE_NAME = "sales_crm_session"


def _sign(payload: str) -> str:
    digest = hmac.new(config.WEB_SECRET.encode(), payload.encode(), hashlib.sha256)
    return _b64e(digest.digest())


def make_session(user_id: int, *, issued_at: int | None = None) -> str:
    """Собирает значение cookie: id пользователя, время выпуска и подпись."""
    issued = issued_at if issued_at is not None else int(time.time())
    payload = f"{user_id}.{issued}"
    return f"{payload}.{_sign(payload)}"


def read_session(cookie: str | None) -> int | None:
    """Достаёт id пользователя из cookie или None, если ей нельзя верить.

    Отказ во всех сомнительных случаях: испорченная, чужая, просроченная — всё
    это «не залогинен». Молчаливо пропустить хоть один такой случай означает
    пустить в CRM с подделанной cookie.
    """
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    user_part, issued_part, signature = parts

    # Сравниваем в байтах: compare_digest на строках падает, если внутри есть
    # не-ASCII, а cookie приходит из браузера и содержать может что угодно —
    # исключение здесь означало бы 500-ю вместо «не залогинен».
    expected = _sign(f"{user_part}.{issued_part}")
    if not hmac.compare_digest(signature.encode("utf-8", "replace"), expected.encode()):
        return None

    try:
        user_id, issued = int(user_part), int(issued_part)
    except ValueError:
        return None

    age = time.time() - issued
    # Отрицательный возраст — метка из будущего: либо часы уехали, либо cookie
    # подделали вместе с ключом. В обоих случаях доверять ей нельзя.
    if age < 0 or age > config.WEB_SESSION_DAYS * 86400:
        return None
    return user_id
