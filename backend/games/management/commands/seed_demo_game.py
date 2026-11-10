import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from games.models import Game
from games.services import add_rule_version


class Command(BaseCommand):
    help = "Создать неактивный демонстрационный слот 3x3 без перезаписи существующих правил."

    @transaction.atomic
    def handle(self, *args, **options):
        if Game.objects.filter(code="demo_slot").exists():
            self.stdout.write("Демонстрационная игра уже существует; данные не изменены.")
            return
        config_path = Path(__file__).resolve().parents[2] / "examples" / "slot_3x3.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CommandError("Не удалось прочитать демонстрационные правила.") from error
        game = Game.objects.create(code="demo_slot", name="Демонстрационный слот", theme="classic")
        add_rule_version(game.id, config=config, min_bet=1, max_bet=100, make_current=True)
        self.stdout.write(self.style.SUCCESS("Создан demo_slot: версия 1, ставки 1–100, игра неактивна."))
