"""
Готовая база законов с GitHub (её выкладывает бот tools/laws_bot.py в релиз `laws`).

Программа сначала пробует взять отсюда базу выбранного штата: секунды, без браузера, без
входа на форум и без VPN. Если на GitHub базы нет или что-то не так — вызывающий код
идёт на форум по-старому (fail-safe: облако никогда не ломает старый путь).

Защита (все внешние данные считаем сломанными):
  • index.json — только словарь нужной формы, имя файла строго laws-<штат>.zip / rules.zip;
  • размер ограничен, SHA-256 сверяется с index;
  • из архива берём только свои пути (laws/<штат>/…, data/codex/<штат>.json, rules/…),
    «../» и абсолютные пути отбрасываем;
  • папка штата заменяется атомарно: старая база жива, пока новая не распакована целиком.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

import netssl

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LAWS_REPO = "funnynewguy001/Otus-SKDA"          # бот выкладывает базы сюда (для обеих версий)
BASE_URL = f"https://github.com/{LAWS_REPO}/releases/download/laws/"
MAX_ZIP = 150 * 1024 * 1024
UA = {"User-Agent": "Otus (laws download)"}


def _get(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    last = None
    for ctx in netssl.contexts():
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                data = r.read(limit + 1)
            if len(data) > limit:
                raise ValueError("файл больше допустимого")
            return data
        except Exception as e:      # сеть/сертификат — пробуем следующий способ
            last = e
    raise last or RuntimeError("нет связи")


def _record(index, sid: str):
    if not isinstance(index, dict):
        return None
    rec = index.get("rules") if sid == "rules" else (index.get("states") or {}).get(sid)
    if not isinstance(rec, dict):
        return None
    want = "rules.zip" if sid == "rules" else f"laws-{sid}.zip"
    sha, size = rec.get("sha256"), rec.get("size")
    if rec.get("file") != want or not isinstance(sha, str) or len(sha) != 64:
        return None
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ZIP:
        return None
    return {"file": want, "sha256": sha.lower(), "size": size, "date": str(rec.get("date") or "")}


def _allowed(name: str, sid: str) -> bool:
    n = name.replace("\\", "/")
    if n.startswith("/") or ".." in n.split("/") or ":" in n or n.endswith("/"):
        return False
    if sid == "rules":
        return n.startswith("rules/")
    return n.startswith(f"laws/{sid}/") or n == f"data/codex/{sid}.json"


def _swap_dir(new: str, dst: str) -> None:
    """Атомарная замена папки: dst → .old, new → dst; при сбое старое возвращается."""
    old = dst + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(dst):
        os.replace(dst, old)
    try:
        os.replace(new, dst)
    except Exception:
        if os.path.isdir(old) and not os.path.exists(dst):
            os.replace(old, dst)
        raise
    shutil.rmtree(old, ignore_errors=True)


def fetch(sid: str, log=print) -> bool:
    """Скачать базу штата (или sid="rules") с GitHub. True — база на месте; False — идти на форум."""
    try:
        index = json.loads(_get(BASE_URL + "index.json", 2 * 1024 * 1024).decode("utf-8"))
    except Exception as e:
        log(f"Готовой базы на GitHub нет ({type(e).__name__}) — качаю с форума.")
        return False
    rec = _record(index, sid)
    if not rec:
        log("На GitHub нет базы этого штата — качаю с форума.")
        return False
    log(f"Беру готовую базу с GitHub (обновлена {rec['date'][:10] or '?'})...")
    try:
        data = _get(BASE_URL + rec["file"], MAX_ZIP)
        if len(data) != rec["size"] or hashlib.sha256(data).hexdigest() != rec["sha256"]:
            log("Архив с GitHub повреждён (не сходится контрольная сумма) — качаю с форума.")
            return False
        z = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in z.namelist() if _allowed(n, sid)]
        if not any(n.endswith(".txt") for n in names):
            log("Архив с GitHub пустой — качаю с форума.")
            return False
        tmp = tempfile.mkdtemp(prefix="otus_laws_", dir=APP_DIR)
        try:
            for n in names:
                p = os.path.join(tmp, *n.replace("\\", "/").split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with z.open(n) as src, open(p, "wb") as out:
                    shutil.copyfileobj(src, out)
            if sid == "rules":
                _swap_dir(os.path.join(tmp, "rules"), os.path.join(APP_DIR, "rules"))
            else:
                os.makedirs(os.path.join(APP_DIR, "laws"), exist_ok=True)
                _swap_dir(os.path.join(tmp, "laws", sid), os.path.join(APP_DIR, "laws", sid))
                cx = os.path.join(tmp, "data", "codex", f"{sid}.json")
                if os.path.isfile(cx):
                    os.makedirs(os.path.join(APP_DIR, "data", "codex"), exist_ok=True)
                    os.replace(cx, os.path.join(APP_DIR, "data", "codex", f"{sid}.json"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        log(f"Готовую базу взять не вышло ({type(e).__name__}) — качаю с форума.")
        return False
    log("Готово: база взята с GitHub.")
    return True
