"""
Серверы Majestic RP и разделы форума, откуда качаются законы и правила.

Ссылки взяты из открытого проекта TurgovBot. Если на каком-то сервере форум переехал,
просто поправь ссылку здесь (в кавычках) и заново скачай законы.
"""

FORUM = "https://forum.majestic-rp.ru"

# id, название, разделы законодательной базы, брать только посты автора темы
SERVERS = [
    ("new_york", "New York", 1, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.84/"], False),
    ("detroit", "Detroit", 2, ["https://forum.majestic-rp.ru/forums/kodeksy.353/",
                               "https://forum.majestic-rp.ru/forums/zakony.354/"], True),
    ("chicago", "Chicago", 3, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.255/"], False),
    ("san_francisco", "San Francisco", 4, ["https://forum.majestic-rp.ru/forums/odobrennyye-zakonoproyekty.344/"], True),
    ("atlanta", "Atlanta", 5, ["https://forum.majestic-rp.ru/forums/odobrennyye-zakonoproyekty.562/"], True),
    ("san_diego", "San Diego", 6, ["https://forum.majestic-rp.ru/forums/normativno-pravovyye-akty.580/"], True),
    ("los_angeles", "Los Angeles", 7, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.693/"], True),
    ("miami", "Miami", 8, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.773/"], True),
    ("las_vegas", "Las Vegas", 9, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.820/"], True),
    ("washington", "Washington", 10, ["https://forum.majestic-rp.ru/forums/odobrennyye-zakonoproyekty.895/"], True),
    ("dallas", "Dallas", 11, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.954/"], True),
    ("boston", "Boston", 12, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.1017/"], True),
    ("houston", "Houston", 13, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.1104/"], True),
    ("seattle", "Seattle", 14, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.1163/"], True),
    ("phoenix", "Phoenix", 15, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.1213/"], True),
    ("denver", "Denver", 16, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.1276/"], True),
    ("portland", "Portland", 17, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.1338/"], True),
    ("orlando", "Orlando", 18, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.1405/"], True),
    ("memphis", "Memphis", 19, ["https://forum.majestic-rp.ru/forums/zakonodatel-naya-baza.1471/"], True),
]

SERVER_BY_ID = {s[0]: s for s in SERVERS}

# ID подраздела форума «Жалобы на игроков» для каждого сервера.
# Собрано со структуры forum.majestic-rp.ru (узлы /forums/zhaloby-na-igrokov.<id>/).
# Используется и для мониторинга жалоб, и для подачи жалобы из приложения.
COMPLAINTS_FORUM = {
    "new_york": 37,
    "detroit": 169,
    "chicago": 247,
    "san_francisco": 318,
    "atlanta": 474,
    "san_diego": 540,
    "los_angeles": 652,
    "miami": 762,
    "las_vegas": 859,
    "washington": 936,
    "dallas": 994,
    "boston": 1059,
    "houston": 1085,
    "seattle": 1148,
    "phoenix": 1253,
    "denver": 1318,
    "portland": 1380,
    "orlando": 1446,
    "memphis": 1513,
}


def complaints_post_url(server_id):
    """Ссылка на форму создания жалобы (post-thread) для сервера."""
    fid = COMPLAINTS_FORUM.get(server_id)
    return "%s/forums/zhaloby-na-igrokov.%d/post-thread" % (FORUM, fid) if fid else None


# Имена структурных полей XenForo в форме жалобы (одинаковы на всех серверах).
COMPLAINT_FIELDS = {
    "nick": "custom_fields[1]",       # Ваш игровой никнейм
    "static": "custom_fields[2]",     # Ваш статический ID #
    "offender": "custom_fields[8]",   # Статический #ID нарушителя
    "datetime": "custom_fields[4]",   # Дата и время нарушения
    "desc": "custom_fields[5]",       # Краткое описание ситуации
    "proof": "custom_fields[6]",      # Доказательства
}

# Номер категории штата на главной форума (блок «Majestic РП | <штат>»).
# Внутри категории лежат все разделы ведомств этого штата — из них качаются уставы.
# Структура у штатов разная (где-то LSPD/EMS/Sheriff, где-то министерства),
# поэтому разделы не перечисляем руками: update_laws читает главную и находит их сам.
# Плюс этого: лидеры меняют уставы и создают новые разделы — всё подхватится само.
CATEGORY = {
    "new_york": 62,
    "detroit": 125,
    "chicago": 214,
    "san_francisco": 277,
    "atlanta": 457,
    "san_diego": 507,
    "los_angeles": 649,
    "miami": 728,
    "las_vegas": 804,
    "washington": 878,
    "dallas": 945,
    "boston": 1008,
    "houston": 1081,
    "seattle": 1141,
    "phoenix": 1204,
    "denver": 1267,
    "portland": 1329,
    "orlando": 1396,
    "memphis": 1462,
}

# Разделы внутри категории штата, которые НЕ содержат уставов: жалобы, иски,
# заявления, архивы, журналы и прочий поток. Их не открываем — экономим время.
CHARTER_SKIP = (r"рассмотрен|отказан|отклон|отмен|принят|жалоб|заявлени|обращени|\bиск|архив|"
                r"журнал|медиа|ваканси|отч[её]т|data\s*base|политическ|разбан|новост|"
                r"объявлен|предложени|набор|анкет|отстран|выговор|увольнен|амнист|"
                r"помилован|тюрьм|розыск|ордер|мероприяти|суд")
# «суд» в списке намеренно: в судебных разделах лежат иски и приговоры, а не уставы
# ведомств. Лишние разделы — лишние запросы к форуму, за которые он придерживает.

# Если у какого-то штата разделы с уставами лежат вне его категории — добавь их сюда.
# Эти ссылки берутся ДОПОЛНИТЕЛЬНО к найденным автоматически.
CHARTERS: dict[str, list[str]] = {}


def category_id(server_id):
    """Номер категории штата на главной форума (или None)."""
    return CATEGORY.get(server_id)


def charters_for(server_id):
    """Дополнительные разделы с уставами, заданные вручную (обычно пусто)."""
    return CHARTERS.get(server_id, [])


# Общие правила проекта (за что баны, деморган и т.д.) — одни на все серверы
RULES_SECTIONS = [
    "https://forum.majestic-rp.ru/forums/general-server-rules/",
    "https://forum.majestic-rp.ru/forums/rules-of-criminal-organizations/",
    "https://forum.majestic-rp.ru/forums/rules-of-state-organizations/",
]
