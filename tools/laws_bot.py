"""
Бот базы законов: качает с форума Majestic правила и законы всех штатов и выкладывает
их на GitHub, чтобы программа брала готовую базу выбранного штата без браузера и входа.

Запускается из GitHub Actions (.github/workflows/laws-bot.yml) на раннере владельца.
Вход на форум — служебный аккаунт бота: переменные OTUS_FORUM_LOGIN / OTUS_FORUM_PASSWORD
(секреты репозитория). Выкладка — gh с токеном GH_TOKEN (секрет RELEASE_TOKEN).

Результат в релизе `laws` (пре-релиз — не мешает автообновлению программы):
  laws-<штат>.zip  — laws/<штат>/*.txt + data/codex/<штат>.json (памятки)
  rules.zip        — rules/*.txt (общие правила Majestic)
  index.json       — {"date", "states": {штат: {file, sha256, size, date}}, "rules": {...}}

Защита:
  • штат не скачался → в index остаётся прошлая запись, старый архив на GitHub не трогаем;
  • форум придерживает (код 7) → запуск заканчивается, база уходит в конец очереди (index["tried"]);
    запуски каждый час по 1–2 базы с медленным темпом (OTUS_BOT_GAP) — «не долбить» (владелец);
  • главные базы (PRIORITY: Орландо, правила) — первыми, если их не проверяли 6+ часов;
  • между штатами пауза — не долбим форум.

Локально:  python tools/laws_bot.py --only orlando --no-upload
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import servers  # noqa: E402

REPO = os.environ.get("OTUS_LAWS_REPO", "funnynewguy001/Otus-SKDA")
TAG = "laws"
OUT = os.path.join(ROOT, "laws_out")
GAP_SEC = 300         # 5 мин между штатами: неизменённые темы не качаются, запросов мало
HELD_GAP_SEC = 1800   # форум всё же придержал прошлый штат — ждём 30 минут
THROTTLED = 7         # код update_laws: форум придерживает запросы
# Главные базы — в начале запуска, пока форум «свежий» (владелец 24.11.2026: «главный штат
# для скачивания — Орландо»). Раньше первыми шли 6 ни разу не скачанных штатов: каждую ночь они
# падали первыми, Орландо не обновлялся с 20.11, правила проекта — с 19.11. Переопределяется
# переменной OTUS_LAWS_PRIORITY («orlando,rules»). Запуски теперь каждый час по 1–2 базы
# («делать прям медленно, 16 часов»), поэтому главные идут первыми, только если их не проверяли
# дольше PRIORITY_EVERY_H часов — иначе они забирали бы все места и до остальных штатов не доходило.
PRIORITY = [x.strip() for x in os.environ.get("OTUS_LAWS_PRIORITY", "orlando,rules").split(",") if x.strip()]
try:
    PRIORITY_EVERY_H = float(os.environ.get("OTUS_LAWS_PRIORITY_EVERY_H") or 6)
except ValueError:
    PRIORITY_EVERY_H = 6.0


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip(name: str, entries: list[str]) -> str | None:
    """entries — пути относительно ROOT (файлы и папки). Без единого .txt → None."""
    files = []
    for e in entries:
        p = os.path.join(ROOT, e)
        if os.path.isfile(p):
            files.append(e)
        elif os.path.isdir(p):
            for dp, _, fn in os.walk(p):
                for f in fn:
                    if f.endswith((".txt", ".json")):
                        files.append(os.path.relpath(os.path.join(dp, f), ROOT))
    if not any(f.endswith(".txt") for f in files):
        return None
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, name)
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(files):
            z.write(os.path.join(ROOT, f), f.replace("\\", "/"))
    os.replace(tmp, out)
    return out


def old_index() -> dict:
    url = f"https://github.com/{REPO}/releases/download/{TAG}/index.json"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
        if isinstance(d, dict) and isinstance(d.get("states"), dict):
            return d
    except Exception as e:
        print(f"прошлого index.json нет ({type(e).__name__}) — начинаю с чистого")
    return {"states": {}, "rules": None}


def gh_exe() -> str:
    return shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


def gh(*args) -> subprocess.CompletedProcess:
    return subprocess.run([gh_exe(), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def ensure_release() -> None:
    if gh("release", "view", TAG, "-R", REPO).returncode == 0:
        return
    r = gh("release", "create", TAG, "-R", REPO, "--prerelease", "--latest=false",
           "--title", "Базы законов (бот)",
           "--notes", "Готовые базы законов и правил по штатам. Обновляет бот; программа качает сама.")
    if r.returncode != 0:
        raise SystemExit(f"не создать релиз {TAG}: {r.stderr.strip()[-300:]}")


def fetch(args: list[str]) -> tuple[int, bool, bool]:
    """Тот же загрузчик, что в программе, отдельным процессом (свой браузер на каждый штат).
    Сначала кладём прошлую базу штата с GitHub: рабочая папка раннера каждый раз чистая, и без
    этого неполная загрузка (форум придержал) выкладывала урезанную базу — у Orlando 38 из ~80.
    update_laws при неполной загрузке недокачанное не удаляет (keep_missing)."""
    sid = "rules" if args == ["--rules"] else (args[1] if len(args) == 2 else "")
    if sid:
        try:
            import laws_cloud
            laws_cloud.APP_DIR = ROOT
            if laws_cloud.fetch(sid, log=lambda m: print("  " + m)):
                print(f"  {sid}: прошлая база с GitHub на месте — дополняю с форума")
        except Exception as e:
            print(f"  прошлую базу взять не вышло ({type(e).__name__}) — качаю с нуля")
    held = down = False
    pr = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "update_laws.py"), *args], cwd=ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace")
    for line in pr.stdout:
        print(line, end="", flush=True)
        if "придержал" in line:
            held = True
        if "ERR_CONNECTION" in line or "ERR_TIMED_OUT" in line or "ERR_NAME_NOT_RESOLVED" in line:
            down = True               # форум не открывается вовсе (блок по адресу / лежит)
    return pr.wait(), held, down


def last_touch(job: str, index: dict) -> str:
    """Когда базу в последний раз трогали: позже из двух дат — успешного обновления и НЕУДАЧНОЙ
    попытки (index["tried"]). Без второй даты штат, который форум не отдаёт, стоял бы первым в
    каждом запуске и занимал все места (при 1–2 базах за запуск до остальных бы не доходило).
    Строка ISO; кривая/пустая — «очень давно»."""
    rec = index.get("rules") if job == "rules" else (index.get("states") or {}).get(job)
    d = rec.get("date") if isinstance(rec, dict) else ""
    tried = index.get("tried") if isinstance(index.get("tried"), dict) else {}
    t = tried.get(job)
    return max(d if isinstance(d, str) else "", t if isinstance(t, str) else "")


def hours_since(iso: str) -> float:
    try:
        then = datetime.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return float("inf")
    return (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() / 3600


def pick_oldest(jobs: list, index: dict, n: int) -> list:
    """n баз, которые дольше всех не трогали (см. last_touch); которых нет и не пробовали — первыми."""
    return sorted(jobs, key=lambda j: (last_touch(j, index), jobs.index(j)))[:max(1, n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="один штат (id из servers.py) или rules")
    ap.add_argument("--no-upload", action="store_true", help="не выкладывать на GitHub")
    ap.add_argument("--batch", type=int, default=0,
                    help="сколько баз за запуск (самые старые первыми); 0 — все")
    a = ap.parse_args()

    ids = [s[0] for s in servers.SERVERS]
    jobs = ["rules"] + ids
    index = old_index()
    if a.only:
        if a.only not in jobs:
            print(f"нет такого штата: {a.only}")
            return 2
        jobs = [a.only]
    else:
        # Сначала главные (PRIORITY, по порядку), потом самые устаревшие (которых нет — в самом
        # начале): если форум придержит и за ночь всё не успеется, следующей ночью бот начнёт
        # с недокачанного, но Орландо и правила — всё равно первыми.
        n = a.batch or len(jobs)
        first = [j for j in PRIORITY if j in jobs and hours_since(last_touch(j, index)) >= PRIORITY_EVERY_H]
        rest = pick_oldest([j for j in jobs if j not in first], index, len(jobs))
        jobs = (first + rest)[:max(1, n)]
        print("в этот запуск: " + ", ".join(jobs), flush=True)
    produced = []
    released = [False]

    def publish(z) -> bool:
        """Выложить архив штата (или только index.json, z=None) СРАЗУ после штата: раньше всё
        выкладывалось в самом конце, и остановка бота (или сбой на 15-м штате) теряла всё скачанное."""
        index["date"] = now()
        os.makedirs(OUT, exist_ok=True)
        ip = os.path.join(OUT, "index.json")
        with open(ip, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
        if a.no_upload:
            return True
        if not released[0]:
            ensure_release()
            released[0] = True
        r = gh("release", "upload", TAG, *([z] if z else []), ip, "--clobber", "-R", REPO)
        if r.returncode != 0:
            print(f"выкладка не удалась: {r.stderr.strip()[-300:]}")
            return False
        print(f"  выложено на GitHub: {os.path.basename(z) if z else 'index.json'}", flush=True)
        return True

    def mark_tried(job):
        """Неудачная попытка: время — в index["tried"], база уходит в конец очереди (last_touch).
        Прошлый архив базы на GitHub не трогаем — у людей остаётся рабочая версия."""
        if not isinstance(index.get("tried"), dict):
            index["tried"] = {}
        index["tried"][job] = now()
        publish(None)

    held_last = False
    stopped_gently = False
    for i, job in enumerate(jobs):
        if i:
            # форум придержал прошлый штат — пережидаем дольше, чтобы не закрепить блокировку
            time.sleep(HELD_GAP_SEC if held_last else GAP_SEC)
        print(f"===== {job} =====", flush=True)
        rc, held_last, down = fetch(["--rules"] if job == "rules" else ["--server", job])
        if down and rc != 0:
            # Форум не отвечает совсем (блок по адресу после частых запусков).
            # Стучаться в каждый следующий штат — только продлевать блок: стоп до следующего запуска.
            print("Форум не открывается — останавливаюсь, попробую в следующий запуск.", flush=True)
            mark_tried(job)
            stopped_gently = True
            break
        if rc == THROTTLED or (held_last and rc != 0):
            # Форум придержал. Запуски теперь каждый час по 1–2 базы («не долбить, делать прям
            # медленно» — владелец 24.11.2026), поэтому не пережидаем внутри запуска, а сразу
            # заканчиваем: следующий запуск через час, с другого адреса GitHub. База — в конец очереди.
            print(f"{job}: форум придержал — заканчиваю, продолжу в следующий запуск", flush=True)
            mark_tried(job)
            stopped_gently = True
            break
        if rc != 0:
            print(f"{job}: не скачалось (код {rc}) — на GitHub остаётся прошлая версия", flush=True)
            mark_tried(job)
            continue
        if isinstance(index.get("tried"), dict):
            index["tried"].pop(job, None)          # скачалась — отметка о неудаче больше не нужна
        if job == "rules":
            z = _zip("rules.zip", ["rules"])
        else:
            z = _zip(f"laws-{job}.zip", [f"laws/{job}", f"data/codex/{job}.json"])
        if not z:
            print(f"{job}: пусто — не выкладываю")
            continue
        rec = {"file": os.path.basename(z), "sha256": sha256(z), "size": os.path.getsize(z), "date": now()}
        if job == "rules":
            index["rules"] = rec
        else:
            index["states"][job] = rec
        print(f"{job}: {rec['size'] // 1024} КБ готово", flush=True)
        if publish(z):
            produced.append(z)

    if not produced:
        # Остановились сами (форум придержал) — это не сбой: следующий запуск через час. Красным
        # помечаем только запуск, где ничего не вышло без такой причины.
        print("Ничего не выложено." + (" Форум придержал — следующий запуск продолжит." if stopped_gently else ""))
        return 0 if stopped_gently else 1
    print(f"Выложено за запуск: {len(produced)} архивов в {REPO} / {TAG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
