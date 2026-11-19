"""
Строит памятки (справочник) по ВСЕМ разделам законодательной базы штата
из уже скачанных с форума файлов laws/<сервер>/forum_*.txt.

Результат — data/codex/<сервер>.json, который читает handbook.html:
  {
    "server": "new_york",
    "source": "forum",
    "updated": "2026-11-12T18:20",
    "codes": [
       {"id": "...", "name": "Уголовный кодекс", "kind": "code",
        "items": [{"art": "6.1", "disp": "...", "fine": "..."}, ...]},
       {"id": "...", "name": "Конституция штата", "kind": "doc",
        "blocks": [{"t": "heading"|"art"|"p", "s": "текст"}, ...]},
       ...
    ]
  }

Разметка адаптивная, не по жёсткому списку названий:
  * раздел, где у заметной доли статей уверенно находится наказание (штраф/срок),
    показывается КАРТОЧКАМИ (kind=code);
  * всё остальное (Конституция, процессуальный/гражданский кодексы, законы, уставы)
    — как ДОКУМЕНТ с полнотекстовым поиском (kind=doc). Так мы не рискуем переврать штраф.

Запуск вручную:  python codex_build.py --server new_york
Из приложения вызывается build(server) после скачивания законов.
"""

import json
import os
import re
import sys
from datetime import datetime

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LAWS_DIR = os.path.join(APP_DIR, "laws")
CODEX_DIR = os.path.join(APP_DIR, "data", "codex")

# Порядок разделов в справочнике: сначала кодексы, затем Конституция и
# конституционные законы, затем остальные законы. Ключи — по вхождению в название.
ORDER_HINTS = [
    "уголовн", "администрат", "дорожн", "процессуал", "гражданск",
    "налогов", "трудов", "этическ", "кодекс",          # прочие кодексы
    "конституц",                                        # конституция и конст. законы
    "закон",                                            # законы
]

# Слова, по которым узнаём наказание в тексте статьи.
PUNISH_RE = re.compile(
    r"(наказыва[ею]тся|наказание|влеч[её]т|лишени[ея]\s+свобод|"
    r"обязательн\w+\s+работ|исправительн\w+\s+работ|"
    r"уголовн\w+\s+штраф|административн\w+\s+штраф|штраф\w*)",
    re.IGNORECASE,
)
MONEY_RE = re.compile(r"\d[\d\s. ]*\$")
TERM_RE = re.compile(r"срок\w*\s+(?:до\s+)?\d+|\d+\s+(?:лет|год\w*|мес\w*|час\w*|сут\w*)", re.IGNORECASE)

ART_RE = re.compile(r"(?m)^\s*Статья\s+(\d+(?:\.\d+)*)\.?\s*(.*)$")
HEAD_RE = re.compile(r"(?m)^\s*(Раздел|Глава|Часть|Общая часть|Особенная часть|Подраздел)\b.*$")
PART_RE = re.compile(r"(?m)^\s*ч\.?\s*\d+")


def _read_doc(path):
    """Возвращает (title, text). Файл: 1-я строка — заголовок, 2-я — 'Источник: ...'."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    lines = raw.splitlines()
    title = (lines[0].strip() if lines else "") or os.path.basename(path)
    # тело — всё после первой пустой строки (пропускаем заголовок и 'Источник:')
    body = raw
    nl = raw.find("\n\n")
    if nl >= 0:
        body = raw[nl + 2:]
    return title, body.strip()


def _slug(name):
    s = re.sub(r"[^\w]+", "-", name.lower(), flags=re.UNICODE).strip("-")
    return s[:48] or "razdel"


def _order_key(name):
    low = name.lower()
    for i, h in enumerate(ORDER_HINTS):
        if h in low:
            return i
    return len(ORDER_HINTS) + 1


def _split_articles(text):
    """Делит текст на статьи: [(art_no, title_line, body), ...]."""
    marks = list(ART_RE.finditer(text))
    arts = []
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        arts.append((m.group(1), (m.group(2) or "").strip(), text[start:end].strip()))
    return arts


def _extract_fine(body):
    """Пытается вытащить наказание из текста статьи. Возвращает строку или ''."""
    # Берём предложение/часть, где впервые встречается слово про наказание.
    m = PUNISH_RE.search(body)
    if not m:
        return ""
    tail = body[m.start():]
    # до конца «части» (следующая 'ч. N') или абзаца
    cut = PART_RE.search(tail, 1)
    seg = tail[:cut.start()] if cut else tail
    seg = seg.split("\n\n")[0]
    seg = re.sub(r"[★★]", "", seg)            # ★-уровень уходит в отдельное поле
    seg = re.sub(r"\s+", " ", seg).strip(" .;")
    # Убираем вводное «наказывается/наказание» — его заменит подпись «Наказание:».
    seg = re.sub(r"^(наказыва\w+|наказани\w+)\s*[-—:]?\s*", "", seg, flags=re.IGNORECASE)
    if seg:
        seg = seg[0].upper() + seg[1:]
    # Если есть деньги или срок — это точно санкция; обрезаем до разумной длины.
    if len(seg) > 220:
        # оставляем до первой денежной/сроковой части, если есть
        mm = MONEY_RE.search(seg)
        if mm:
            seg = seg[:mm.end() + 40]
        seg = seg[:220].rstrip() + "…"
    return seg


# Канонические кодексы. По ним «Памятка» строит ровно три колонки (УК / АК / ПК),
# и по ним же схлопываются дубли: форум нередко отдаёт один кодекс двумя темами
# («Уголовный Кодекс» и «Уголовный кодекс») — остаётся самая полная версия.
CANON_PATTERNS = [
    ("uk", r"уголовн"),
    ("ak", r"администрат"),
    ("pk", r"процессуальн"),
    ("dk", r"дорожн"),
    ("gk", r"гражданск"),
    ("nk", r"налогов"),
]
# Это не сам кодекс, а документ вокруг него — каноном не считаем.
NOT_CODE_RE = re.compile(r"(о\s+внесении|изменени|поправк|проект|утратил|дополнени)", re.IGNORECASE)

# Статьи процессуального кодекса, которые нужны игроку чаще всего, — наверх памятки.
MEMO_FIRST_RE = re.compile(
    r"задержан|задержив|освобожд|обыск|досмотр|допрос|арест|миранд|ордер|изъят|розыск",
    re.IGNORECASE)


def _canon(name):
    """Ключ кодекса ('uk'/'ak'/'pk'/…) или '' — если это не кодекс."""
    low = name.lower()
    if "кодекс" not in low or NOT_CODE_RE.search(low):
        return ""
    for key, pat in CANON_PATTERNS:
        if re.search(pat, low):
            return key
    return ""


def _dedup_key(name, canon):
    """Один ключ на документ: кодексы — по канону, остальное — по имени без регистра."""
    if canon:
        return "canon:" + canon
    return "name:" + re.sub(r"[^а-яёa-z0-9]+", "", name.lower())


PARTNUM_RE = re.compile(r"(?m)^\s*ч\.?\s*(\d+)")


def _split_parts(body):
    """Делит тело статьи на части [(pnum, ptext), ...]. Если частей нет — []."""
    marks = list(PARTNUM_RE.finditer(body))
    if not marks:
        return []
    out = []
    for i, m in enumerate(marks):
        s = m.end()
        e = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        out.append(("ч." + m.group(1), body[s:e].strip()))
    return out


def _disp_before_fine(ptext):
    """Краткая суть части — текст до слова о наказании."""
    m = PUNISH_RE.search(ptext)
    head = ptext[:m.start()] if m else ptext
    head = re.sub(r"\s+", " ", head).strip(" ,.;—-")
    if len(head) > 170:
        head = head[:170].rstrip() + "…"
    return head or "—"


def _marks(src):
    """Вытаскивает ★-уровень и признак Фед/Рег, если они размечены в тексте."""
    lvl = len(re.findall(r"[★★]", src))
    fr = ""
    if re.search(r"\b(F\s*/\s*R|Ф\s*/\s*Р)\b", src) or (
            re.search(r"федеральн", src, re.I) and re.search(r"региональн", src, re.I)):
        fr = "F/R"
    elif re.search(r"\bF\b", src) or re.search(r"федеральн", src, re.I):
        fr = "F"
    elif re.search(r"\bR\b", src) or re.search(r"региональн", src, re.I):
        fr = "R"
    return (str(lvl) if lvl else ""), fr


def _parse_code(text):
    """Иерархический разбор кодекса (как у docossum): родительская статья → части.
    Возвращает (items, heads) или (None, None), если это не «карательный» кодекс.
      items: [{"art":"6.1 ч.1","disp":...,"fine":...,"lvl":"3","fr":"F/R"}, ...]
      heads: {"6.1": "Нанесение телесных повреждений", ...}  — названия статей.
    """
    arts = _split_articles(text)
    if len(arts) < 4:
        return None, None
    items, heads, with_fine = [], {}, 0
    for art_no, title_line, body in arts:
        parts = _split_parts(body)
        _fp = PARTNUM_RE.search(body)
        pre = body[:_fp.start()] if _fp else body
        hd = {}
        if title_line and len(title_line) > 2:
            hd["name"] = title_line.rstrip(" .")
        _, pfr = _marks((title_line or "") + " " + (pre or ""))
        if pfr:
            hd["fr"] = pfr
        if hd:
            heads[art_no] = hd
        for pnum, ptext in (parts or [("", body)]):
            fine = _extract_fine(ptext)
            if fine:
                with_fine += 1
            lvl, fr = _marks(ptext)
            art = (art_no + (" " + pnum if pnum else "")).strip()
            it = {"art": art, "disp": _disp_before_fine(ptext)}
            if fine:
                it["fine"] = fine
            if lvl:
                it["lvl"] = lvl
            if fr and "fr" not in hd:
                it["fr"] = fr
            items.append(it)
    if with_fine >= max(3, int(len(items) * 0.3)):
        return items, heads
    return None, None


def _as_doc_blocks(text):
    """Режет документ на блоки для читалки: заголовки/статьи/абзацы."""
    blocks = []
    for para in re.split(r"\n{2,}", text):
        p = para.strip()
        if not p:
            continue
        one = re.sub(r"\s+", " ", p)
        if ART_RE.match(p):
            blocks.append({"t": "art", "s": one})
        elif HEAD_RE.match(p):
            blocks.append({"t": "heading", "s": one})
        else:
            blocks.append({"t": "p", "s": one})
    return blocks


def _richness(sec):
    """Насколько раздел «полный» — по нему выбираем победителя среди дублей."""
    return (len(sec.get("items") or []), len(sec.get("memo") or []),
            len(sec.get("blocks") or []), sec.get("_len", 0))


def _memo_cards(text):
    """Карточки-подсказки (как у docossum): заголовок статьи + её части списком.

    Из процессуального кодекса так получаются «Порядок задержания»,
    «Основания для освобождения» и т.п. — самое нужное идёт первым.
    """
    cards = []
    for _art_no, title_line, body in _split_articles(text):
        title = (title_line or "").rstrip(" .")
        if len(title) < 3:
            continue
        lines = []
        for _pnum, ptext in _split_parts(body):
            s = re.sub(r"\s+", " ", ptext).strip(" .;")
            if s:
                lines.append(s if len(s) <= 300 else s[:300].rstrip() + "…")
        if not lines:                      # частей нет — берём абзацы статьи
            for raw in re.split(r"\n+", body):
                s = re.sub(r"\s+", " ", raw).strip(" .;")
                if len(s) > 2:
                    lines.append(s if len(s) <= 300 else s[:300].rstrip() + "…")
        if not lines:
            continue
        cards.append({"t": title, "l": lines[:16],
                      "_p": 0 if MEMO_FIRST_RE.search(title) else 1})
    cards.sort(key=lambda c: c["_p"])      # сортировка стабильная — порядок статей сохраняется
    for c in cards:
        c.pop("_p", None)
    return cards


def build(server):
    """Собирает data/codex/<server>.json из laws/<server>/. Возвращает число разделов."""
    src = os.path.join(LAWS_DIR, server)
    if not os.path.isdir(src):
        return 0
    best = {}
    for fn in sorted(os.listdir(src)):
        if not (fn.startswith("forum_") and fn.endswith(".txt")):
            continue
        title, text = _read_doc(os.path.join(src, fn))
        if len(text) < 120:
            continue
        name = re.sub(r"\s*штата?\s+(сан[- ]?андреас|san[- ]?andreas).*$", "",
                      title, flags=re.IGNORECASE).strip() or title
        canon = _canon(name)
        items, heads = _parse_code(text)
        blocks = _as_doc_blocks(text)   # полный текст — для вкладки «Законы» (у всех разделов)
        sec = {"id": _slug(name), "name": name, "blocks": blocks}
        if canon:
            sec["canon"] = canon
        if canon == "pk":
            # процессуальный — всегда памятка-карточками, а не простынёй текста
            sec["kind"] = "doc"
            sec["memo"] = _memo_cards(text)
        elif items:                      # карательный кодекс — карточки со штрафами
            sec["kind"] = "code"; sec["items"] = items; sec["heads"] = heads
        else:
            sec["kind"] = "doc"
        sec["_len"] = len(text)
        # Схлопываем только кодексы: форум отдаёт их двумя темами («Уголовный Кодекс»
        # и «Уголовный кодекс»). Обычные законы не трогаем — у двух разных законов
        # названия бывают похожими, и склейка просто потеряла бы документ.
        if canon:
            key = _dedup_key(name, canon)
            old = best.get(key)
            if old is None or _richness(sec) > _richness(old):
                best[key] = sec
        else:
            best["file:" + fn] = sec
    sections = list(best.values())
    seen_ids = {}
    for s in sections:
        s.pop("_len", None)
        base = s["id"]                     # id участвует в избранном — он должен быть уникален
        if base in seen_ids:
            seen_ids[base] += 1
            s["id"] = f"{base}-{seen_ids[base]}"
        else:
            seen_ids[base] = 1
    # сортировка: кодексы → конституция → законы, внутри по алфавиту
    sections.sort(key=lambda s: (_order_key(s["name"]), s["name"].lower()))
    out = {
        "server": server,
        "source": "forum",
        "updated": datetime.now().isoformat(timespec="minutes"),
        "codes": sections,
    }
    os.makedirs(CODEX_DIR, exist_ok=True)
    with open(os.path.join(CODEX_DIR, f"{server}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return len(sections)


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    args = ap.parse_args(argv)
    n = build(args.server)
    print(f"Готово: {n} разделов -> data/codex/{args.server}.json" if n
          else f"Нет скачанных законов в laws/{args.server}/")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(_main())
