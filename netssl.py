"""
Проверенное TLS-соединение для всех сетевых запросов программы.

Раньше модули при любой ошибке сертификата повторяли запрос с полностью
отключённой проверкой. Это опасно: через обновление скачивается и запускается
.exe, а в запросах к ИИ уходит ключ пользователя. Подменив ответ, посторонний
мог бы подсунуть свой установщик или забрать ключ.

Поэтому проверка больше не отключается. Пробуем два honest-варианта:
системное хранилище сертификатов, а если оно недоступно (так бывает у собранного
exe) — список сертификатов из certifi. Не вышло ни то, ни другое — запрос честно
падает с ошибкой, а не выполняется вслепую.
"""

import ssl

_CONTEXTS = None


def contexts() -> list:
    """Контексты TLS по порядку: системный, затем certifi. Оба с проверкой."""
    global _CONTEXTS
    if _CONTEXTS is not None:
        return _CONTEXTS
    out = []
    try:
        out.append(ssl.create_default_context())
    except Exception:
        pass
    try:
        import certifi
        out.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    _CONTEXTS = out or [ssl.create_default_context()]
    return _CONTEXTS


CERT_HINT = ("не удалось проверить сертификат сайта. "
             "Проверь дату и время на компьютере и настройки антивируса")
