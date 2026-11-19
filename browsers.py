"""
Поиск браузеров на компьютере для загрузки законов.

Подходят браузеры на движке Chromium (Chrome, Edge, Thorium, Opera, Brave, Яндекс, Vivaldi...):
ими умеет управлять Playwright. Firefox так не подключить — для него есть встроенный Chromium.
"""

import os
import re
import sys

# Firefox и прочие не-Chromium браузеры Playwright так запустить не может
NOT_CHROMIUM = ("firefox", "iexplore", "librewolf", "waterfox", "floorp", "palemoon", "tor")

# Стандартные места установки (на случай, если браузер не прописан в реестре)
_KNOWN = [
    ("Microsoft Edge", r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ("Microsoft Edge", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ("Google Chrome", r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ("Google Chrome", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    ("Google Chrome", r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ("Thorium", r"%LOCALAPPDATA%\Thorium\Application\thorium.exe"),
    ("Thorium", r"%ProgramFiles%\Thorium\Application\thorium.exe"),
    ("Brave", r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("Brave", r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("Opera", r"%LOCALAPPDATA%\Programs\Opera\opera.exe"),
    ("Opera GX", r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe"),
    ("Яндекс Браузер", r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"),
    ("Vivaldi", r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe"),
    ("Vivaldi", r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe"),
    ("Chromium", r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
    ("Arc", r"%LOCALAPPDATA%\Microsoft\WindowsApps\Arc.exe"),
]


def _is_chromium(path: str) -> bool:
    name = os.path.basename(path).lower()
    return name.endswith(".exe") and not any(bad in name for bad in NOT_CHROMIUM)


def _from_registry() -> list[tuple[str, str]]:
    """Браузеры, зарегистрированные в Windows (то, что видно в «Приложения по умолчанию»)."""
    if sys.platform != "win32":
        return []
    import winreg
    found = []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clients\StartMenuInternet"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Clients\StartMenuInternet")]
    for root, sub in roots:
        try:
            with winreg.OpenKey(root, sub) as key:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(key, name) as bkey:
                            try:
                                label = winreg.QueryValueEx(bkey, None)[0] or name
                            except OSError:
                                label = name
                        with winreg.OpenKey(key, name + r"\shell\open\command") as ckey:
                            cmd = winreg.QueryValueEx(ckey, None)[0]
                        m = re.match(r'\s*"([^"]+)"|\s*(\S+)', cmd)
                        path = (m.group(1) or m.group(2)) if m else ""
                        if path:
                            found.append((label, os.path.expandvars(path)))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def find_browsers() -> list[tuple[str, str]]:
    """Список (название, путь к exe) найденных Chromium-браузеров, без дублей."""
    out, seen = [], set()
    candidates = _from_registry() + [(n, os.path.expandvars(p)) for n, p in _KNOWN]
    for label, path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen or not os.path.isfile(path) or not _is_chromium(path):
            continue
        seen.add(key)
        out.append((label, path))
    return out


def browser_label(path: str) -> str:
    for label, p in find_browsers():
        if os.path.normcase(p) == os.path.normcase(path):
            return label
    return os.path.basename(path)
