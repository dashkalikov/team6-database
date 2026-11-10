import re

from django.core.exceptions import ValidationError


def validate_slot_config(config):
    """Validate the version-1 contract for a fixed-line 3x3 slot."""
    def fail(message):
        raise ValidationError(message)

    def keys(value, expected, label):
        if not isinstance(value, dict) or set(value) != set(expected):
            fail(f"{label}: неверный состав полей.")

    def positive_integer(value):
        return type(value) is int and 0 < value <= 1_000_000

    keys(config, ("format_version", "reel_count", "row_count", "symbols", "reels", "paylines", "paytable", "payout"), "config")
    for field, expected in (("format_version", 1), ("reel_count", 3), ("row_count", 3)):
        if type(config[field]) is not int or config[field] != expected:
            fail(f"{field} должен быть равен {expected}.")

    symbols = config["symbols"]
    if not isinstance(symbols, list) or not 2 <= len(symbols) <= 64:
        fail("Нужен список из 2–64 символов.")
    if any(not isinstance(s, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", s) for s in symbols):
        fail("Код символа: 1–32 знака A–Z, 0–9 и _, начиная с буквы.")
    if len(set(symbols)) != len(symbols):
        fail("Коды символов должны быть уникальны.")

    reels = config["reels"]
    if not isinstance(reels, list) or len(reels) != 3:
        fail("Требуются три ленты барабанов.")
    for reel in reels:
        if not isinstance(reel, list) or not 3 <= len(reel) <= 256:
            fail("Лента барабана содержит от 3 до 256 позиций.")
        if any(not isinstance(s, str) or s not in symbols for s in reel):
            fail("Лента ссылается на неизвестный символ.")

    paylines = config["paylines"]
    if not isinstance(paylines, list) or not 1 <= len(paylines) <= 27:
        fail("Требуется от 1 до 27 фиксированных линий.")
    line_ids, paths = set(), set()
    for line in paylines:
        keys(line, ("id", "rows"), "Линия")
        if not isinstance(line["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", line["id"]):
            fail("Некорректный идентификатор линии.")
        rows = line["rows"]
        if not isinstance(rows, list) or len(rows) != 3 or any(type(r) is not int or not 0 <= r <= 2 for r in rows):
            fail("Линия содержит три индекса строки от 0 до 2.")
        if line["id"] in line_ids or tuple(rows) in paths:
            fail("Линии и их идентификаторы не должны повторяться.")
        line_ids.add(line["id"])
        paths.add(tuple(rows))

    paytable = config["paytable"]
    if not isinstance(paytable, list) or not 1 <= len(paytable) <= 1024:
        fail("Таблица выплат содержит от 1 до 1024 комбинаций.")
    rule_ids, combinations = set(), set()
    for rule in paytable:
        keys(rule, ("id", "symbols", "multiplier"), "Комбинация")
        if not isinstance(rule["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", rule["id"]):
            fail("Некорректный идентификатор комбинации.")
        combination = rule["symbols"]
        if not isinstance(combination, list) or len(combination) != 3 or any(not isinstance(s, str) or s not in symbols for s in combination):
            fail("Комбинация содержит три известных символа.")
        if rule["id"] in rule_ids or tuple(combination) in combinations:
            fail("Комбинации и их идентификаторы не должны повторяться.")
        keys(rule["multiplier"], ("numerator", "denominator"), "Коэффициент")
        if not all(positive_integer(v) for v in rule["multiplier"].values()):
            fail("Числитель и знаменатель — целые числа от 1 до 1000000.")
        rule_ids.add(rule["id"])
        combinations.add(tuple(combination))

    keys(config["payout"], ("bet_basis", "includes_stake", "rounding", "aggregation", "line_match"), "payout")
    expected = {
        "bet_basis": "spin_total", "includes_stake": True, "rounding": "floor",
        "aggregation": "sum_then_floor", "line_match": "exact_three",
    }
    if config["payout"] != expected or type(config["payout"]["includes_stake"]) is not bool:
        fail("Неподдерживаемая семантика выплат.")
