"""
Скачивает законы штата или правила Majestic с форума в папки laws/<сервер> и rules.

Открывает обычное окно браузера (Edge / Chrome) со своим профилем в папке browser_profile.
В первый раз нужно войти на форум своим аккаунтом прямо в этом окне — потом вход запоминается.
Пароль программа не видит и не сохраняет: вход делаешь ты сам в браузере.

Запуск из оверлея: Настройки → Базы знаний. Вручную:
    python update_laws.py --server orlando
    python update_laws.py --rules
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from servers import (CHARTER_SKIP, FORUM, RULES_SECTIONS, SERVER_BY_ID, category_id,
                     charters_for)

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(APP_DIR, "browser_profile")
PREFIX = "forum_"            # файлы с форума; свои файлы без этого префикса не трогаются
# ---------- темп запросов к форуму ----------
# Форум следит за частотой обращений и при частых запросах перестаёт отдавать
# страницы всему адресу (не только программе — и браузеру тоже). Поэтому темп
# держим в одном месте: любой запрос проходит через _pace().
MIN_GAP = 2.5                # минимум секунд между запросами к форуму
MAX_GAP = 20.0               # до скольки замедляемся, если форум начал придерживать
# Бот базы: сразу спокойный темп (страниц много, штатов 19). Владелец 24.11.2026: «не долбить,
# делать прям медленно — на скачивание тратить не 8 часов, а 16»: по умолчанию 8 с, расписание бота
# может задать своё (OTUS_BOT_GAP). Программы у людей это не касается — у неё MIN_GAP.
try:
    BOT_GAP = max(MIN_GAP, min(MAX_GAP, float(os.environ.get("OTUS_BOT_GAP") or 8.0)))
except ValueError:
    BOT_GAP = 8.0
SILENT_WAIT = 120            # форум перестал отвечать (таймауты) — столько ждём перед повтором
DELAY = MIN_GAP              # оставлено для совместимости со старым кодом

_last_req = [0.0]            # когда был прошлый запрос
_gap = [MIN_GAP]             # текущий промежуток между запросами


def _pace():
    """Выдержать промежуток до следующего запроса."""
    wait = _gap[0] - (time.time() - _last_req[0])
    if wait > 0:
        time.sleep(wait)
    _last_req[0] = time.time()


def _slow_down() -> float:
    """Форум придержал — дальше идём заметно медленнее и темп назад не возвращаем."""
    _gap[0] = min(round(_gap[0] * 1.8, 1), MAX_GAP)
    return _gap[0]


def reset_pace(gap: float = MIN_GAP):
    """Сбросить темп (нужно для проверок и при новом запуске)."""
    _gap[0], _last_req[0] = gap, 0.0
LOGIN_WAIT_SEC = 300
ACCOUNT_PATH = os.path.join(APP_DIR, "account.json")


LOG_PATH = os.path.join(APP_DIR, "update.log")


def log(msg: str):
    """Пишем и в консоль (если она есть), и в update.log — его читает оверлей."""
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def safe_name(title: str) -> str:
    title = re.sub(r"[\\/:*?\"<>|]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:90] or "без названия"


# ---------- браузер ----------
def _install_chromium() -> bool:
    """Скачиваем браузер для Playwright (~150 МБ) — один раз. Работает и из Python, и из собранного exe."""
    log("Скачиваю браузер для загрузки законов (около 150 МБ, один раз)...")
    cmd, env = None, None
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        drv = compute_driver_executable()
        cmd = [str(x) for x in drv] if isinstance(drv, (tuple, list)) else [str(drv)]
        cmd += ["install", "chromium"]
        env = get_driver_env()
    except Exception:
        if not getattr(sys, "frozen", False):
            cmd = [sys.executable.replace("pythonw.exe", "python.exe"), "-m", "playwright", "install", "chromium"]
    if not cmd:
        log("Не удалось найти установщик браузера. Выбери в настройках свой браузер (Chrome, Edge, Thorium...).")
        return False
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            log("Браузер скачан.")
            return True
        log("Не удалось скачать браузер: " + (r.stderr or r.stdout)[-300:])
    except Exception as e:
        log(f"Не удалось скачать браузер: {e}")
    return False


def _browser_setting() -> str:
    """Что выбрано в настройках: "" — авто, "bundled" — встроенный, иначе путь к exe."""
    try:
        with open(os.path.join(APP_DIR, "config.json"), "r", encoding="utf-8-sig") as f:
            return json.load(f).get("browser_path", "") or ""
    except Exception:
        return ""


def open_browser(p, headless: bool = False):
    """headless=True — без окна: нужно для фоновых проверок, чтобы не мешать игре."""
    from browsers import browser_label, find_browsers

    os.makedirs(PROFILE_DIR, exist_ok=True)
    kw = {"headless": headless, "viewport": {"width": 1100, "height": 800}}
    if not headless and os.environ.get("OTUS_FORUM_LOGIN"):
        # Бот (вход сам, по логину из окружения): окно за пределами экрана — владелец его не
        # видит. Не headless: настоящее окно форум пропускает без лишних проверок.
        kw["args"] = ["--window-position=-32000,-32000", "--window-size=1100,800"]
    errors = []

    def attempt(label, **extra):
        try:
            ctx = p.chromium.launch_persistent_context(PROFILE_DIR, **kw, **extra)
            log(f"Браузер: {label}")
            return ctx
        except Exception as e:
            first = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
            errors.append(f"{label}: {first[:160]}")
            return None

    def bundled():
        ctx = attempt("встроенный Chromium")
        if ctx is None and _install_chromium():
            ctx = attempt("встроенный Chromium")
        return ctx

    choice = _browser_setting()
    if choice == "bundled":
        ctx = bundled()
        if ctx:
            return ctx
    elif choice:
        if os.path.isfile(choice):
            ctx = attempt(browser_label(choice), executable_path=choice)
            if ctx:
                return ctx
            log("Выбранный в настройках браузер не запустился, пробую другие...")
        else:
            log(f"Браузер из настроек не найден: {choice}. Пробую другие...")

    # Авто: все найденные браузеры на движке Chromium, потом встроенный
    for label, path in find_browsers():
        if path == choice:
            continue
        ctx = attempt(label, executable_path=path)
        if ctx:
            return ctx
    if choice != "bundled":
        ctx = bundled()
        if ctx:
            return ctx
    raise RuntimeError("Не удалось открыть браузер:\n" + "\n".join(errors))


def wait_challenge(page, tries=20):
    """Ждём, пока пройдёт защита форума (страница-заглушка со slowAES сама перезагрузится)."""
    for _ in range(tries):
        try:
            if "slowAES" not in page.content():
                return
        except Exception:
            pass
        time.sleep(1.5)


def read_username(page) -> str:
    """Ник вошедшего пользователя из шапки форума XenForo."""
    for sel in (".p-navgroup-link--user .p-navgroup-linkText", "a.p-navgroup-link--user"):
        try:
            el = page.query_selector(sel)
            if el:
                name = el.inner_text().strip()
                if name:
                    return name
        except Exception:
            pass
    return ""


def save_account(page):
    name = read_username(page)
    if name:
        with open(ACCOUNT_PATH, "w", encoding="utf-8") as f:
            json.dump({"username": name, "checked": datetime.now().isoformat(timespec="minutes")},
                      f, ensure_ascii=False)
    return name


class Throttled(Exception):
    """Форум временно перестал отдавать страницы (слишком частые запросы)."""


# Заглушка форума «Page could not be loaded». Открывается по правильному адресу
# и с кодом 200, поэтому проверка reached() её не отличает — ловим по тексту.
BLOCKED_RE = re.compile(r"could not be loaded|не удалось загрузить", re.IGNORECASE)
BLOCK_WAITS = (15, 40, 90)      # сколько ждём перед повторами, секунд


def is_blocked(page) -> bool:
    try:
        if BLOCKED_RE.search(page.title() or ""):
            return True
    except Exception:
        return False
    try:
        return bool(BLOCKED_RE.search((page.inner_text("body") or "")[:800]))
    except Exception:
        return False


def goto(page, url):
    from playwright.sync_api import TimeoutError as PWTimeout
    for attempt in range(3):
        _pace()                       # единственное место, где задаётся темп
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            wait_challenge(page)
            if is_blocked(page):
                # Форум придерживает нас за частые запросы. Молча идти дальше нельзя:
                # раздел выглядел бы пустым, а мы продолжили бы долбить форум.
                # Темп после этого не возвращаем — иначе снова упрёмся в то же самое.
                gap = _slow_down()
                wait = BLOCK_WAITS[attempt]
                log(f"  форум придержал запросы: жду {wait} с, дальше по одной "
                    f"странице в {gap} с")
                time.sleep(wait)
                continue
            return
        except PWTimeout:
            if attempt == 2:
                raise
            time.sleep(4)
    if is_blocked(page):
        raise Throttled(url)


def is_logged_in(page) -> bool:
    try:
        return bool(page.query_selector("a.p-navgroup-link--user, .p-navgroup--member"))
    except Exception:
        return False


def reached(page, url) -> bool:
    """Форум без доступа молча перекидывает на главную. Проверяем, что открылся именно нужный раздел."""
    want = urlsplit(url).path.rstrip("/")
    got = urlsplit(page.url).path.rstrip("/")
    return bool(want) and (got == want or got.startswith(want + "/"))


def _auto_login(page, section_url) -> bool:
    """Вход для бота базы законов (GitHub Actions). Логин и пароль — ТОЛЬКО из переменных
    окружения (секреты репозитория FORUM_LOGIN / FORUM_PASSWORD); в коде и журнале их нет.
    У обычных пользователей переменных нет — функция ничего не делает (вход руками, как раньше)."""
    login = os.environ.get("OTUS_FORUM_LOGIN", "").strip()
    password = os.environ.get("OTUS_FORUM_PASSWORD", "")
    if not login or not password:
        return False
    log("Бот: вхожу на форум под служебным аккаунтом...")
    try:
        goto(page, urljoin(section_url, "/login/"))
        wait_challenge(page)
        page.fill("input[name='login']", login, timeout=20000)
        page.fill("input[name='password']", password, timeout=20000)
        box = page.query_selector("input[name='remember']")
        if box and not box.is_checked():
            box.check()
        page.click("form[action*='login'] button[type='submit'], .block-body button.button--primary",
                   timeout=20000)
        deadline = time.time() + 60
        while time.time() < deadline and not is_logged_in(page):
            time.sleep(2)
    except Exception as e:
        log(f"Бот: вход не удался ({type(e).__name__}).")   # без подробностей — там может быть логин
        return False
    ok = is_logged_in(page)
    log("Бот: вход выполнен." if ok else "Бот: форум не пустил (неверный пароль, капча или защита).")
    if not ok:
        _login_diag(page, login)
    return ok


def _login_diag(page, login: str):
    """Почему не пустило — без логина/пароля в журнале: адрес и заголовок страницы, текст ошибки
    форума (логин из него вырезается), есть ли капча / проверка браузера."""
    try:
        err = ""
        for sel in (".blockMessage--error", ".formRow-explain.is-error", ".js-loginErrorMessage",
                    ".overlay-content .blockMessage"):
            el = page.query_selector(sel)
            if el:
                err = (el.inner_text() or "").strip()
                break
        if login and err:
            err = err.replace(login, "<логин>")
        cap = bool(page.query_selector("iframe[src*='captcha'], iframe[src*='turnstile'], "
                                       ".g-recaptcha, .h-captcha, [data-sitekey]"))
        log(f"  страница: {urlsplit(page.url).path or '/'} · «{(page.title() or '')[:80]}»")
        log(f"  ошибка форума: {err[:200] or '—'} · капча: {'да' if cap else 'нет'}")
    except Exception as e:
        log(f"  подробности недоступны ({type(e).__name__})")


def ensure_login(page, section_url, need_section=True):
    goto(page, section_url)
    if not is_logged_in(page) and _auto_login(page, section_url):
        goto(page, section_url)
    if not is_logged_in(page):
        if os.environ.get("OTUS_FORUM_LOGIN"):
            return False          # бот: ждать человека некому — сразу ошибка, а не 3 минуты
        log(">>> Нужен вход на форум. Войди своим аккаунтом в открывшемся окне браузера.")
        log(">>> Совет: дождись, пока пройдёт «Check your browser», и только потом вводи логин.")
        log(f">>> Жду до {LOGIN_WAIT_SEC // 60} минут...")
        try:
            goto(page, urljoin(section_url, "/login/"))
        except Exception:
            pass
        deadline = time.time() + LOGIN_WAIT_SEC
        while time.time() < deadline and not is_logged_in(page):
            time.sleep(3)
        if not is_logged_in(page):
            return False
        name = save_account(page)
        log(f"Вход выполнен{': ' + name if name else ''}.")
        goto(page, section_url)
    else:
        save_account(page)
    if need_section and not reached(page, section_url):
        log(f"Раздел {section_url} не открылся (форум перекинул на {page.url}).")
        log("Значит, ссылка на раздел устарела или у аккаунта нет к нему доступа. Пришли эту строку в чат.")
        return False
    return True


# ---------- раздел и темы ----------
# Дата последнего сообщения каждой темы из списка тем (data-time у <time> в колонке «последнее»).
# По ней не перекачиваем темы, которые с прошлого раза не менялись (_cached_text).
THREAD_TIME: dict = {}
CACHE_FILE = "_threads.json"
# Дата последнего сообщения темы в списке раздела. 20.11.2026: у всех 48 тем Orlando дата
# оказалась пустой — форум (XenForo 2.3) пишет её в data-timestamp/datetime, а не data-time,
# и «не качать то, что не менялось» не работало: бот каждый раз перекачивал всё и тратил на
# это лимит форума. Берём любой из атрибутов.
_JS_LATEST = ("e => { const it = e.closest('.structItem'); if (!it) return '';"
              " const t = it.querySelector('.structItem-cell--latest time, .structItem-latestDate,"
              " .structItem-cell--latest [data-time], .structItem-cell--latest [data-timestamp]');"
              " if (!t) return '';"
              " return t.getAttribute('data-timestamp') || t.getAttribute('data-time')"
              " || t.getAttribute('datetime') || t.getAttribute('title') || ''; }")


def _note_time(a, url):
    try:
        t = a.evaluate(_JS_LATEST)
        if isinstance(t, str) and 0 < len(t.strip()) <= 64:
            THREAD_TIME[url] = t.strip()
    except Exception:
        pass                      # нет даты — тема просто скачается, как раньше


def _load_cache(out_dir) -> dict:
    """{url: {"t": дата последнего сообщения, "title": заголовок}} с прошлой загрузки. Кривой — пусто."""
    try:
        with open(os.path.join(out_dir, CACHE_FILE), "r", encoding="utf-8") as f:
            d = json.load(f)
        return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(d, dict) else {}
    except Exception:
        return {}


def _cached_text(out_dir, cache, url):
    """Тема не менялась с прошлого раза и её файл на месте → (заголовок, текст) без захода на форум."""
    rec = cache.get(url) or {}
    t, title = rec.get("t"), rec.get("title")
    if not t or t != THREAD_TIME.get(url) or not isinstance(title, str) or not title:
        return None
    path = os.path.join(out_dir, f"{PREFIX}{safe_name(title)}.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except Exception:
        return None
    sep = chr(10) * 2             # файл: «заголовок / Источник: … / пустая строка / текст»
    text = body.split(sep, 1)[1].strip() if sep in body else ""
    return (title, text) if len(text) > 200 else None


def _save_cache(out_dir, results):
    try:
        # Прошлые записи не теряем: при неполной загрузке (форум придержал) недокачанные темы
        # остаются файлами (keep_missing), и в следующий раз их не надо качать заново.
        d = {u: v for u, v in _load_cache(out_dir).items() if isinstance(v, dict)}
        d.update({url: {"t": THREAD_TIME.get(url, ""), "title": title} for title, url, _ in results})
        with open(os.path.join(out_dir, CACHE_FILE), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=0)
    except Exception as e:
        log(f"Список тем для «без изменений» не сохранён ({e}) — в следующий раз скачается всё")


SECTIONS_FILE = "_sections.json"   # разделы уставов: когда последний раз удалось / сколько раз подряд нет


def _load_sections(out_dir) -> dict:
    try:
        with open(os.path.join(out_dir, SECTIONS_FILE), "r", encoding="utf-8") as f:
            d = json.load(f)
        return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict)} \
            if isinstance(d, dict) else {}
    except Exception:
        return {}


def _order_sections(sections: list, state: dict) -> list:
    """Сначала разделы, которые ещё ни разу не удались, потом — давно не обновлявшиеся;
    свежие — в конце; упорно не открывающиеся (2+ раза подряд) — в самом конце.
    19–20.11: бот каждую ночь шёл по разделам в одном порядке и упирался в одно и то же
    место — уставы после пятого не скачались ни разу (Orlando: 39 документов вместо ~80)."""
    def key(item):
        i, u = item
        s = state.get(u) if isinstance(state, dict) else None
        s = s if isinstance(s, dict) else {}            # запись испорчена — как новый раздел
        fail = s.get("fail") if isinstance(s.get("fail"), int) else 0
        ok = s.get("ok") if isinstance(s.get("ok"), str) else ""
        # нетронутые → недавно не открывшиеся (1–2 раза: форум придержал, сам раздел живой) →
        # уже скачанные, давние первыми → упорно молчащие (3+) в самом конце. 20.11: уже скачанные
        # шли раньше недокачанных, бот тратил на них весь лимит, и Минобороны не доходил никогда.
        tier = 0 if (not ok and not fail) else (1 if fail else 2)
        return (fail >= 3, tier, ok, i)
    return [u for _, u in sorted(enumerate(sections), key=key)]


def _save_sections(out_dir, state: dict):
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, SECTIONS_FILE), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=0)
    except Exception as e:
        log(f"Порядок разделов уставов не сохранён ({e})")


def collect_threads(page, section_url) -> list[tuple[str, str]]:
    found, seen = [], set()
    url = section_url
    while url:
        goto(page, url)
        if not reached(page, section_url):
            log(f"  пропускаю {section_url}: форум перекинул на другую страницу")
            break
        for a in page.query_selector_all("div.structItem-title a"):
            href = a.get_attribute("href") or ""
            m = re.search(r"(/threads/[^/?#]*\.(\d+))", href)
            if not m or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            found.append((urljoin(section_url, m.group(1) + "/"), a.inner_text().strip()))
            _note_time(a, found[-1][0])
        nxt = page.query_selector("a.pageNav-jump--next")
        url = urljoin(section_url, nxt.get_attribute("href")) if nxt else None
    return found


# Ветки, которые НЕ нужно качать: там лежат недействующие документы.
# Иначе помощник начнёт ссылаться на отменённые законы.
SKIP_SECTION_RE = re.compile(
    r"отмен|архив|недейств|утратил|устаревш|черновик|законопроект|предложени|обсуждени|корзин",
    re.IGNORECASE)


def collect_subforums(page, section_url) -> list[str]:
    """Подразделы («Кодексы», «Законы» и т.п.). Отменённые/архивные ветки пропускаем."""
    goto(page, section_url)
    if not reached(page, section_url):
        return []
    subs = []
    for a in page.query_selector_all(".node-title a[href*='/forums/']"):
        href = urljoin(section_url, a.get_attribute("href"))
        title = (a.inner_text() or "").strip()
        if title and SKIP_SECTION_RE.search(title):
            log(f"  пропускаю раздел (недействующее): {title}")
            continue
        if href.rstrip("/") != section_url.rstrip("/") and href not in subs:
            subs.append(href)
    return subs


def gdoc_text(context, doc_id: str) -> str:
    try:
        r = context.request.get(f"https://docs.google.com/document/d/{doc_id}/export?format=txt",
                                timeout=30000)
        if r.ok:
            return r.text().strip()
    except Exception:
        pass
    return ""


def parse_thread(context, page, url, only_author: bool,
                 max_pages: int = 0, min_post: int = 0) -> tuple[str, str]:
    """Текст темы. max_pages — предел страниц (0 = все), min_post — не брать
    короткие посты вроде «+» и «принято» (первый пост берём всегда)."""
    goto(page, url)
    h1 = page.query_selector("h1.p-title-value")
    title = h1.inner_text().strip() if h1 else url
    author = None
    parts = []
    first = True
    pages = 0
    cur = url
    while cur:
        if not first:
            goto(page, cur)
        for post in page.query_selector_all("article.message"):
            cls = post.get_attribute("class") or ""
            dc = post.get_attribute("data-content") or ""
            if "message--highlighted" in cls or not dc.startswith("post-"):
                continue
            name_el = post.query_selector("a.username, span.username")
            name = name_el.inner_text().strip() if name_el else ""
            body = post.query_selector(".message-body .bbWrapper")
            body_text = body.inner_text().strip() if body else ""
            if author is None:
                author = name
                g = post.query_selector('a[href*="docs.google.com/document/d/"]')
                if g and len(body_text) < 1500:   # закон лежит в Google Документе
                    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", g.get_attribute("href") or "")
                    if m:
                        txt = gdoc_text(context, m.group(1))
                        if txt:
                            return title, txt
            if only_author and name != author:
                continue
            # болтовню в теме («+», «принято», «согласен») в документ не тащим
            if min_post and parts and len(body_text) < min_post:
                continue
            if body_text:
                parts.append(body_text)
        first = False
        pages += 1
        if max_pages and pages >= max_pages:
            break
        nxt = page.query_selector("a.pageNav-jump--next")
        cur = urljoin(url, nxt.get_attribute("href")) if nxt else None
    return title, "\n\n".join(parts)


# ---------- главное ----------
CHARTER_SKIP_RE = re.compile(CHARTER_SKIP, re.IGNORECASE)
CHARTER_TITLE_RE = re.compile(r"устав|регламент|положени", re.IGNORECASE)
# Явно мёртвые версии: лидер сменился, устав переписан, старая тема осталась висеть.
# «отмен» только как «отменён» (не «отмена вызова»), «old» только отдельным словом
# (иначе под фильтр попал бы, например, «Устав Gold Coast PD»).
CHARTER_DEAD_RE = re.compile(r"устарел|недейств|не\s*актуал|отмен[её]н|архив|старая\s*верси|"
                             r"\bold\b|черновик|проект\s+устава", re.IGNORECASE)
# Темы-ловушки: при открытии форум рвёт соединение (ERR_FAILED) и закрывает доступ всему
# адресу. 20.11.2026 «Регламент проведения плановых финансовых проверок…» (тема 3611761):
# и бот на GitHub, и загрузка с ПК владельца каждый раз ломались ровно на ней, после чего
# форум переставал пускать. Это не устав ведомства — не открываем вовсе.
CHARTER_TRAP_RE = re.compile(r"финансов\w*\s+провер", re.IGNORECASE)
CHARTER_TRAP_IDS = {"3611761"}


def _cut_off(e) -> bool:
    """Форум рвёт соединение (а не просто медлит): ERR_FAILED / ERR_CONNECTION_* / сброс.
    После такого любой следующий запрос только продлевает блок адреса."""
    s = str(e)
    return any(k in s for k in ("net::ERR_FAILED", "ERR_CONNECTION_", "ERR_EMPTY_RESPONSE",
                                "ERR_TIMED_OUT", "ERR_NAME_NOT_RESOLVED"))


def _node_threads(link) -> int:
    """Сколько тем в разделе — число видно прямо на главной, рядом со ссылкой.

    Нужно, чтобы не открывать заведомо пустые разделы: каждый лишний запрос
    приближает момент, когда форум перестаёт отдавать страницы всему адресу.
    Возвращает -1, если счётчик не нашёлся — такой раздел оставляем, чтобы
    не потерять уставы из-за непривычной вёрстки.
    """
    try:
        return link.evaluate("""el => {
            const node = el.closest('.node');
            if (!node) return -1;                       // ссылка не из списка разделов
            const stats = node.querySelector('.node-stats');
            if (!stats) return -1;
            const dd = stats.querySelector('dl dd');    // первая пара — «Темы»
            if (!dd) return -1;
            const n = parseInt((dd.textContent || '').replace(/[^0-9]/g, ''), 10);
            return Number.isFinite(n) ? n : -1;
        }""")
    except Exception:
        return -1


def discover_charter_sections(page, cat_id: int) -> list[str]:
    """Разделы ведомств штата — ищем на главной форума в блоке его категории.

    Руками списки не держим: у каждого штата своя структура (где-то LSPD/EMS/Sheriff,
    где-то министерства), лидеры создают новые разделы. Поэтому берём все разделы
    категории, кроме потока жалоб/исков/заявлений (CHARTER_SKIP).
    """
    goto(page, FORUM + "/")
    try:
        blocks = page.query_selector_all("div.block--category")
    except Exception as e:
        log(f"  уставы: главная форума не прочиталась ({e})")
        return []
    # Без этой проверки обрыв связи или защита форума выглядели бы как «нет категории»
    if not blocks:
        log("  уставы: главная форума не отдала список категорий (защита форума "
            "или обрыв связи) — уставы пропущены, законы не тронуты")
        return []
    for b in blocks:
        head = b.query_selector(".block-header a[href]")
        if not head or not re.search(r"\.%d$" % cat_id, (head.get_attribute("href") or "").strip()):
            continue
        urls, seen, empty = [], set(), 0
        for a in b.query_selector_all("a[href*='/forums/']"):
            href = a.get_attribute("href") or ""
            m = re.search(r"(/forums/[^/?#]*\.(\d+))", href)
            if not m or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            # text_content, а не inner_text: у скрытых ссылок inner_text пуст,
            # и раздел с жалобами прошёл бы фильтр
            name = (a.text_content() or "").strip()
            if CHARTER_SKIP_RE.search(name):
                continue
            if _node_threads(a) == 0:
                empty += 1      # на главной видно, что тем нет — открывать незачем
                continue
            urls.append(urljoin(FORUM + "/", m.group(1) + "/"))
        log(f"Разделов ведомств найдено: {len(urls)}"
            + (f" (ещё {empty} пустых пропущено)" if empty else ""))
        return urls
    log(f"  уставы: категория штата (id {cat_id}) не найдена на главной — проверь servers.py")
    return []


def _charter_key(title: str) -> str:
    """Название устава без версий и дат — чтобы узнать две версии одного документа."""
    t = title.lower()
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)              # [Актуально], (ред. 2025)
    t = re.sub(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}", " ", t)  # даты
    t = re.sub(r"\bv\.?\s*\d+(\.\d+)*|\bверси\w*\s*\d+(\.\d+)*|\bред\w*\.?", " ", t)
    t = re.sub(r"\bот\b|\bновый\b|\bновая\b|\bактуальн\w*", " ", t)
    t = re.sub(r"\s\d+(\.\d+)*\s*$", " ", t)   # «Устав LSPD 2.0» — номер версии в конце
    t = re.sub(r"[^а-яёa-z0-9]+", "", t)
    return t


def collect_charter_threads(page, section_url) -> list[tuple[str, str]]:
    """Темы-уставы в разделе ведомства: темы со словом «устав/регламент/положение».

    Лидеры переписывают уставы и часто вешают новую тему, не убирая старую. Поэтому
    из нескольких версий одного документа берём самую новую (больший номер темы),
    а помеченные «устарел/не актуален/отменён» не берём вовсе.
    """
    best, seen = {}, set()
    goto(page, section_url)
    if not reached(page, section_url):
        log(f"  уставы: раздел {section_url} не открылся")
        return []
    for a in page.query_selector_all("div.structItem-title a"):
        href = a.get_attribute("href") or ""
        text = (a.inner_text() or "").strip()
        m = re.search(r"(/threads/[^/?#]*\.(\d+))", href)
        if not m or m.group(2) in seen:
            continue
        if not CHARTER_TITLE_RE.search(text) or CHARTER_DEAD_RE.search(text):
            # в журнал — чтобы было видно, какие документы ведомств не попадают в базу
            # (владелец, 20.11.2026: «раньше было 80, сейчас 47 — почему не все»)
            why = "устарел" if CHARTER_DEAD_RE.search(text) else "не устав по названию"
            log(f"    (пропуск: {why}: {text[:100]})")
            continue
        if m.group(2) in CHARTER_TRAP_IDS or CHARTER_TRAP_RE.search(text):
            log(f"    (тема-ловушка, не открываю: {text})")
            continue
        seen.add(m.group(2))
        tid = int(m.group(2))
        key = _charter_key(text) or m.group(2)
        url = urljoin(section_url, m.group(1) + "/")
        _note_time(a, url)
        old = best.get(key)
        if old and old[0] >= tid:
            log(f"    (старая версия, пропускаю: {text})")
            continue
        if old:
            log(f"    (нашлась версия новее: {text})")
        best[key] = (tid, url, text)
    return [(url, text) for _tid, url, text in sorted(best.values())]


_ART_HEAD = re.compile(r"^\s*(?:Статья|Ст\.)\s*(\d+(?:[.\-]\d+)*)", re.IGNORECASE)
MAX_DETAILS = 60          # не раздувать _changes.json на огромных переписываниях
MAX_SNIPPET = 700


def split_articles(text: str) -> dict:
    """Текст документа → {номер статьи: (заголовок, тело)}. Без «Статья N» — пусто."""
    arts, cur, head, buf = {}, None, "", []
    for ln in (text or "").splitlines():
        m = _ART_HEAD.match(ln)
        if m:
            if cur is not None:
                arts.setdefault(cur, (head, "\n".join(buf).strip()))
            cur, head, buf = m.group(1).replace("-", "."), ln.strip()[:160], []
        elif cur is not None:
            buf.append(ln.rstrip())
    if cur is not None:
        arts.setdefault(cur, (head, "\n".join(buf).strip()))
    return arts


def article_changes(doc: str, old_text: str, new_text: str) -> list[dict]:
    """Что поменялось внутри документа по статьям (для «было / стало»)."""
    def norm(t):
        return re.sub(r"\s+", " ", t or "").strip()
    old, new = split_articles(old_text), split_articles(new_text)
    out = []
    for num in sorted(set(old) | set(new), key=lambda n: [int(x) if x.isdigit() else 0 for x in n.split(".")]):
        o, n = old.get(num), new.get(num)
        if o and n and norm(o[1]) == norm(n[1]):
            continue
        kind = "added" if not o else "removed" if not n else "changed"
        out.append({"doc": doc, "num": num, "kind": kind, "head": (n or o)[0],
                    "old": (o[1][:MAX_SNIPPET] if o else ""), "new": (n[1][:MAX_SNIPPET] if n else "")})
    return out


def save_results(out_dir: str, results: list, keep_missing: bool = False) -> int:
    """Заменяет базу законов новыми текстами. Возвращает 0 или код ошибки.

    keep_missing — загрузка была неполной (форум придержал, браузер закрылся): документы,
    которых в этот раз нет, НЕ удаляем — иначе неполная загрузка уменьшала базу
    (так у Orlando из ~80 документов осталось 38: уставы не докачались и были стёрты).

    Вынесено из run() отдельно, чтобы это можно было проверить без браузера:
    именно здесь когда-то терялась вся база при сбое записи.
    """
    if not results:
        # Пустой список стёр бы всю базу. run() отсекает это раньше, но проверяем
        # и здесь — чтобы случайный вызов не оставил человека без законов.
        log("Нечего сохранять — старая база оставлена как есть.")
        return 4
    # запоминаем старые тексты, чтобы показать, что поменялось (поправки)
    def body_of(t):
        return "\n".join(ln for ln in t.splitlines()[2:]).strip()

    old_docs = {}
    old_files = []
    for f in os.listdir(out_dir):
        if f.startswith(PREFIX) and f.endswith(".txt"):
            old_files.append(f)
            try:
                with open(os.path.join(out_dir, f), "r", encoding="utf-8") as fh:
                    old_docs[f[len(PREFIX):-4]] = body_of(fh.read())
            except Exception:
                pass

    # Сначала пишем всё новое рядом, и только когда всё легло — убираем старое.
    # Иначе обрыв записи (нет места, антивирус, выключили свет) оставил бы
    # человека и без старой базы, и без новой.
    used, total, written = set(), 0, []
    new_docs = {}
    try:
        for title, url, text in results:
            name = base = safe_name(title)
            n = 2
            while name.lower() in used:
                name, n = f"{base} ({n})", n + 1
            used.add(name.lower())
            # имя запоминаем здесь же: раньше для одинаковых заголовков оно
            # считалось заново без «(2)», и такой файл каждый раз числился удалённым
            new_docs[name] = text.strip()
            tmp = os.path.join(out_dir, f"{PREFIX}{name}.txt.new")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(f"{title}\nИсточник: {url}\n\n{text}\n")
            written.append(tmp)
            total += len(text)
    except Exception as e:
        for tmp in written:                       # откат: старая база осталась на месте
            try:
                os.remove(tmp)
            except Exception:
                pass
        log(f"Запись не удалась ({e}). Старая база осталась нетронутой.")
        return 5
    # Сначала переносим новое на место старого, и только потом убираем файлы,
    # которых в новой базе нет. При обратном порядке сбой переименования оставлял
    # бы человека с пустой базой.
    finals = {os.path.basename(t[:-4]) for t in written}
    failed = []
    for tmp in written:
        try:
            os.replace(tmp, tmp[:-4])             # убираем «.new»
        except Exception as e:
            # Файл занят (антивирус, открыт в блокноте) — оставляем старую версию
            # этого документа и убираем недописанный .new. Удалять старое нельзя:
            # иначе документ исчез бы вообще.
            failed.append(os.path.basename(tmp)[:-4])
            log(f"Не удалось заменить {os.path.basename(tmp)[:-4]}: {e} "
                f"(осталась прошлая версия документа)")
            try:
                os.remove(tmp)
            except Exception:
                pass
    for f in old_files:
        if f in finals:
            continue        # либо перезаписан новой версией, либо остался старым при сбое
        if keep_missing:
            continue        # неполная загрузка: недокачанное остаётся прошлой версией
        try:
            os.remove(os.path.join(out_dir, f))
        except Exception:
            pass
    if failed and len(failed) == len(written):
        log("Ни один документ заменить не удалось — база осталась прошлой. "
            "Закрой программы, которые могли открыть файлы законов, и попробуй снова.")
        return 6
    if old_docs:
        changes = {
            "date": datetime.now().isoformat(timespec="minutes"),
            "added": sorted(set(new_docs) - set(old_docs)),
            "removed": sorted(set(old_docs) - set(new_docs)),
            "changed": sorted(k for k in set(new_docs) & set(old_docs) if new_docs[k] != old_docs[k]),
        }
        details = []
        for k in changes["changed"]:
            try:
                details.extend(article_changes(k, old_docs[k], new_docs[k]))
            except Exception as e:          # разбор статей — бонус, базу он не должен ронять
                log(f"Не смог разобрать поправки в «{k}»: {e}")
            if len(details) >= MAX_DETAILS:
                break
        changes["details"] = details[:MAX_DETAILS]
        with open(os.path.join(out_dir, "_changes.json"), "w", encoding="utf-8") as f:
            json.dump(changes, f, ensure_ascii=False, indent=1)
        n = len(changes["added"]) + len(changes["removed"]) + len(changes["changed"])
        log(f"Изменений с прошлого раза: {n}" if n else "С прошлого раза ничего не изменилось.")
    n_files = len([f for f in os.listdir(out_dir) if f.startswith(PREFIX) and f.endswith(".txt")])
    with open(os.path.join(out_dir, "_updated.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now().isoformat(timespec="minutes"),
                   "files": n_files if keep_missing else len(results), "chars": total},
                  f, ensure_ascii=False)
    log(f"Готово: {len(results)} документов, ~{total // 1000} тыс. символов "
        f"(≈{total // 3000} тыс. токенов на запрос).")
    return 0


def run(sections: list[str], out_dir: str, only_author: bool, charters: list[str] | None = None,
        cat_id: int | None = None) -> int:
    from playwright.sync_api import sync_playwright

    os.makedirs(out_dir, exist_ok=True)
    # каждый запуск начинаем со спокойного темпа; бот (вход из окружения) — ещё медленнее:
    # он качает все штаты подряд, и при 2,5 с форум начинал его придерживать уже на 2-м штате
    reset_pace(BOT_GAP if os.environ.get("OTUS_FORUM_LOGIN") else MIN_GAP)
    with sync_playwright() as p:
        context = open_browser(p)
        page = context.pages[0] if context.pages else context.new_page()

        if not ensure_login(page, sections[0]):
            log("Вход не выполнен — обновление отменено, старые файлы не тронуты.")
            context.close()
            return 2

        queue, all_sections = list(sections), []
        while queue:
            s = queue.pop(0)
            if s in all_sections:
                continue
            all_sections.append(s)
            queue += collect_subforums(page, s)

        threads, seen = [], set()
        for s in all_sections:
            for url, t in collect_threads(page, s):
                if url not in seen:
                    seen.add(url)
                    threads.append((url, t))
        log(f"Найдено тем: {len(threads)} (дата последнего сообщения известна у "
            f"{sum(1 for u, _ in threads if THREAD_TIME.get(u))})")
        if not threads:
            log("Темы не найдены — старые файлы не тронуты. Возможно, ссылка на раздел устарела (servers.py).")
            context.close()
            return 3

        results = []
        stopped = False
        cache = _load_cache(out_dir)
        reused = 0
        for i, (url, t) in enumerate(threads, 1):
            hit = _cached_text(out_dir, cache, url)
            if hit:
                results.append((hit[0], url, hit[1]))
                reused += 1
                continue                  # с прошлого раза тема не менялась — на форум не идём
            log(f"[{i}/{len(threads)}] {t}")
            try:
                title, text = parse_thread(context, page, url, only_author)
                if len(text) > 200:
                    results.append((title, url, text))
                else:
                    log("    (пусто, пропускаю)")
            except Throttled:
                # Дальше идти бессмысленно: форум нас придерживает и будет отдавать
                # заглушки. Останавливаемся, старую базу не трогаем.
                log("Форум перестал отдавать страницы — слишком частые запросы. "
                    "Загрузка остановлена, старая база осталась на месте. "
                    "Подожди 10-15 минут и запусти обновление снова.")
                stopped = True
                break
            except Exception as e:
                log(f"    ошибка: {e}")
        if stopped:
            context.close()
            return 7

        # Уставы ведомств (LSPD, EMS, Sheriff, SANG, министерства, USSS, Government...).
        # Разделы находим на главной по категории штата — работает для всех штатов.
        charter_sections = list(charters or [])
        if cat_id:
            done = {urlsplit(u).path.rstrip("/") for u in all_sections}
            try:
                for u in discover_charter_sections(page, cat_id):
                    if u in charter_sections or urlsplit(u).path.rstrip("/") in done:
                        continue        # законодательные разделы уже обошли выше
                    charter_sections.append(u)
            except Exception as e:
                log(f"  уставы: разделы ведомств найти не удалось ({e})")
        held = False        # форум начал придерживать запросы — уставы добираем позже
        partial = False     # что-то не скачалось — старые документы не удаляем (save_results)

        def reopen(why="браузер закрылся"):
            """Браузер/вкладка закрылись (или вкладка зависла) посреди загрузки — открываем
            заново и идём дальше. Раньше после этого КАЖДЫЙ следующий раздел падал
            «browser has been closed» / «Timeout 45000ms»."""
            nonlocal context, page
            old = page
            try:
                page = context.new_page()
                try:
                    old.close()     # зависшую вкладку закрываем, чтобы не копились
                except Exception:
                    pass
            except Exception:
                try:
                    context.close()
                except Exception:
                    pass
                context = open_browser(p)
                page = context.pages[0] if context.pages else context.new_page()
            log(f"  {why} — открыл заново, продолжаю")

        sec_state = _load_sections(out_dir)
        charter_sections = _order_sections(charter_sections, sec_state)
        if charter_sections and os.environ.get("OTUS_FORUM_LOGIN"):
            # бот: после законов даём форуму передышку, уставы — отдельной «порцией»
            log(f"  передышка перед уставами: {SILENT_WAIT} с")
            time.sleep(SILENT_WAIT)
        silent = 0          # сколько разделов подряд форум не ответил (таймаут)
        for cs in charter_sections:
            if held:
                break
            ct = []
            for attempt in range(2):
                try:
                    ct = collect_charter_threads(page, cs)
                    silent = 0
                    sec_state[cs] = {"ok": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "fail": 0}
                    break
                except Throttled:
                    held = True
                    break
                except Exception as e:
                    msg = str(e).lower()
                    if _cut_off(e):
                        log(f"  форум оборвал соединение — останавливаю уставы: {str(e).splitlines()[0][:160]}")
                        partial = held = True
                        break
                    if attempt == 0 and "closed" in msg:
                        reopen()
                        continue
                    if attempt == 0 and "timeout" in msg:
                        # Форум перестал отвечать (19.11: после 5 уставов все 7 разделов
                        # подряд — таймаут, база вышла урезанной). Это та же «придержка»,
                        # только молчаливая: пережидаем, замедляемся, свежая вкладка.
                        gap = _slow_down()
                        log(f"  форум не отвечает: жду {SILENT_WAIT} с, дальше по одной "
                            f"странице в {gap} с")
                        time.sleep(SILENT_WAIT)
                        reopen("вкладка зависла")
                        continue
                    log(f"  уставы, раздел пропущен: {str(e).splitlines()[0][:200]}")
                    partial = True
                    ct = []
                    prev = sec_state.get(cs) or {}
                    sec_state[cs] = {"ok": prev.get("ok", "") if isinstance(prev.get("ok"), str) else "",
                                     "fail": (prev.get("fail") if isinstance(prev.get("fail"), int) else 0) + 1}
                    if "timeout" in msg:
                        silent += 1
                    break
            if silent >= 2:
                # Два раздела подряд молчат даже после паузы — дальше только продлевать
                # блок. Скачанное сохраняем, остальные уставы доберём следующей ночью.
                held = True
                break
            for url, t in ct:
                if url in seen:
                    continue
                seen.add(url)
                hit = _cached_text(out_dir, cache, url)
                if hit:
                    results.append((hit[0], url, hit[1]))
                    reused += 1
                    continue
                log(f"[устав] {t}")
                try:
                    # Уставы: берём посты любого автора (лидер меняется и правит устав
                    # ответом в теме), но без болтовни и не глубже 3 страниц.
                    title, text = parse_thread(context, page, url, False,
                                               max_pages=3, min_post=120)
                    if len(text) > 200:
                        results.append((title, url, text))
                except Throttled:
                    held = True
                    break
                except Exception as e:
                    log(f"    ошибка: {str(e).splitlines()[0][:200]}")
                    partial = True
                    if _cut_off(e):
                        # форум оборвал соединение — дальше только продлевать блок: стоп,
                        # скачанное сохраняем, остальное доберём в следующий раз
                        held = True
                        break
                    if "closed" in str(e).lower():
                        reopen()
            # отдельная пауза не нужна: темп держит _pace() на каждом запросе
        if held:
            # Законы уже скачаны — их сохраняем, уставы доберём при следующем обновлении.
            log("Форум придержал запросы — уставы в этот раз добраны не полностью. "
                "Законы сохранены. Повтори обновление позже, уставы дозагрузятся.")
        context.close()

    if not results:
        log("Ничего не скачано — старые файлы не тронуты.")
        return 4

    if reused:
        log(f"Без изменений с прошлого раза (не открывал): {reused} тем")
    rc = save_results(out_dir, results, keep_missing=held or partial)
    if rc == 0:
        _save_cache(out_dir, results)
        if sec_state:
            _save_sections(out_dir, sec_state)
    return rc


def login_only() -> int:
    """Только войти на форум и запомнить вход (кнопка «Войти» в настройках)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = open_browser(p)
        page = context.pages[0] if context.pages else context.new_page()
        ok = ensure_login(page, RULES_SECTIONS[0], need_section=False)
        if ok:
            name = save_account(page) or "аккаунт"
            log(f"Готово: вошёл как {name}. Вход запомнен, окно браузера можно не трогать — оно закроется.")
            time.sleep(2)
        else:
            log("Вход не выполнен.")
        context.close()
    return 0 if ok else 2


def main(argv=None) -> int:
    try:
        return _main(argv)
    except Throttled as e:
        # Форум придержал уже на списке тем раздела (а не на самих темах, где это ловится в run):
        # раньше это падало в общее «Ошибка: <адрес>» с кодом 1, и бот не понимал, что надо
        # остановиться, — спал 30 мин и бил следующий штат (ночь 22–23.11: 8 штатов из 11 впустую).
        log(f"Форум придержал запросы (страница {e}) — загрузка остановлена, старая база на месте. "
            "Подожди 10–15 минут и запусти обновление снова.")
        return 7
    except Exception as e:
        log(f"Ошибка: {e}")
        return 1


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", help="id сервера из servers.py, например orlando")
    ap.add_argument("--rules", action="store_true", help="скачать общие правила Majestic")
    ap.add_argument("--login", action="store_true", help="только войти на форум")
    args = ap.parse_args(argv)
    if args.login:
        log("Открываю форум для входа...")
        return login_only()
    if args.rules:
        log("Скачиваю правила Majestic...")
        return run(RULES_SECTIONS, os.path.join(APP_DIR, "rules"), only_author=True)
    sid = args.server or "orlando"
    if sid not in SERVER_BY_ID:
        log(f"Неизвестный сервер: {sid}")
        return 1
    _, name, num, urls, only_author = SERVER_BY_ID[sid]
    log(f"Скачиваю законы штата RU{num} {name}...")
    rc = run(urls, os.path.join(APP_DIR, "laws", sid), only_author,
             charters=charters_for(sid), cat_id=category_id(sid))
    if rc == 0:
        # Собираем памятки (справочник) по всем разделам из только что скачанных законов.
        try:
            import codex_build
            n = codex_build.build(sid)
            log(f"Памятки обновлены: {n} разделов." if n else "Памятки: разделы не распознаны.")
        except Exception as e:
            log(f"Памятки собрать не удалось: {e}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
