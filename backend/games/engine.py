import secrets
from fractions import Fraction

from .validators import validate_slot_config


def spin(config, bet_amount):
    """Trusted server calculation. Call inside the round transaction."""
    validate_slot_config(config)
    stops = [secrets.randbelow(len(reel)) for reel in config["reels"]]
    grid = [[reel[(stop + row) % len(reel)] for reel, stop in zip(config["reels"], stops)] for row in range(3)]
    rules = {tuple(rule["symbols"]): rule for rule in config["paytable"]}
    total = Fraction(0)
    allocated = 0
    winning_lines = []
    for line in config["paylines"]:
        symbols = tuple(grid[row][reel] for reel, row in enumerate(line["rows"]))
        rule = rules.get(symbols)
        if rule is None:
            continue
        multiplier = rule["multiplier"]
        total += bet_amount * Fraction(multiplier["numerator"], multiplier["denominator"])
        # Carry fractional coins forward; line allocations sum to floor(total).
        current = total.numerator // total.denominator
        winning_lines.append({"line_id": line["id"], "rule_id": rule["id"], "amount": current - allocated})
        allocated = current
    return {"format_version": 1, "grid": grid, "stops": stops, "winning_lines": winning_lines}, allocated
