import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import StringIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase

from .models import Game, GameRuleVersion
from .services import add_rule_version, get_catalog, select_rule_version, set_game_active
from .validators import validate_slot_config


def example_config():
    return json.loads((Path(__file__).parent / "examples" / "slot_3x3.json").read_text(encoding="utf-8"))


class GameTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(code="test_slot", name="Тестовый слот")

    def rules(self, game=None, **kwargs):
        return add_rule_version((game or self.game).id, config=example_config(), min_bet=1, max_bet=100, **kwargs)

    def test_game_validation_and_duplicate_code(self):
        game = Game.objects.create(code=" SECOND ", name=" Название ")
        self.assertEqual(game.code, "second")
        self.assertEqual(game.name, "Название")
        for values in ({"code": " TEST_SLOT ", "name": "Дубль"}, {"code": "bad code", "name": "Название"}, {"code": "empty_name", "name": " "}, {"code": "x" * 65, "name": "Название"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Game.objects.create(**values)

    def test_catalog_requires_rules_and_deactivation_keeps_history(self):
        with self.assertRaises(ValidationError):
            set_game_active(self.game.id, True)
        first = self.rules(make_current=True)
        self.assertEqual(list(get_catalog()), [])
        set_game_active(self.game.id, True)
        self.assertEqual(get_catalog().get().current_rule_version_id, first.id)
        set_game_active(self.game.id, False)
        self.assertEqual(list(get_catalog()), [])
        self.assertTrue(GameRuleVersion.objects.filter(pk=first.id).exists())

    def test_new_version_keeps_previous_config(self):
        first = self.rules(make_current=True)
        config = example_config()
        config["paytable"][0]["multiplier"]["numerator"] = 4
        second = add_rule_version(self.game.id, config=config, min_bet=2, max_bet=200, make_current=True)
        self.assertEqual((first.version, second.version), (1, 2))
        first.refresh_from_db()
        self.assertEqual(first.config["paytable"][0]["multiplier"]["numerator"], 2)
        self.game.refresh_from_db()
        self.assertEqual(self.game.current_rule_version_id, second.id)
        select_rule_version(self.game.id, first.id)
        self.game.refresh_from_db()
        self.assertEqual(self.game.current_rule_version_id, first.id)

    def test_cross_game_version_is_rejected_by_model_and_database(self):
        other = Game.objects.create(code="other", name="Другая игра")
        rules = self.rules(other)
        with self.assertRaises(GameRuleVersion.DoesNotExist):
            select_rule_version(self.game.id, rules.id)
        self.game.current_rule_version = rules
        with self.assertRaises(ValidationError):
            self.game.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Game.objects.filter(pk=self.game.pk).update(current_rule_version_id=rules.id)

    def test_immutable_versions_protected_even_with_direct_sql_updates(self):
        rules = self.rules(make_current=True)
        with self.assertRaises(ValidationError):
            rules.save()
        with self.assertRaises(ValidationError):
            rules.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            GameRuleVersion.objects.filter(pk=rules.pk).update(min_bet=2)
        # An unused version must also remain available for historical references.
        unused = self.rules()
        with self.assertRaises(IntegrityError), transaction.atomic():
            GameRuleVersion.objects.filter(pk=unused.pk).delete()
        with self.assertRaises(ProtectedError):
            self.game.delete()

    def test_limits_duplicate_version_and_database_checks(self):
        for minimum, maximum in ((0, 100), (10, 9), (-1, 100)):
            with self.subTest(minimum=minimum, maximum=maximum), self.assertRaises(ValidationError):
                add_rule_version(self.game.id, config=example_config(), min_bet=minimum, max_bet=maximum)
        first = self.rules()
        with self.assertRaises(ValidationError):
            GameRuleVersion.objects.create(game=self.game, version=first.version, config=example_config(), min_bet=1, max_bet=100)
        for changes in ({"name": " "}, {"code": "BAD"}, {"is_active": True}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                Game.objects.filter(pk=self.game.pk).update(**changes)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GameRuleVersion.objects.bulk_create([GameRuleVersion(game=self.game, version=2, config=[], min_bet=1, max_bet=100)])

    def test_config_rejects_invalid_fields_and_references(self):
        variants = []
        base = example_config()
        for key, value in (("format_version", True), ("reel_count", 4), ("symbols", ["A", "A"]), ("reels", [["UNKNOWN"] * 3] * 3), ("paylines", [{"id": "bad", "rows": [0, 1, 3]}]), ("paytable", [])):
            config = deepcopy(base)
            config[key] = value
            variants.append(config)
        for field, value in (("denominator", 0), ("numerator", -1), ("numerator", 1.5), ("numerator", True)):
            config = deepcopy(base)
            config["paytable"][0]["multiplier"][field] = value
            variants.append(config)
        config = deepcopy(base)
        config["paytable"][0]["symbols"][0] = "UNKNOWN"
        variants.append(config)
        config = deepcopy(base)
        config["paylines"].append(deepcopy(config["paylines"][0]))
        variants.append(config)
        config = deepcopy(base)
        config["payout"]["rounding"] = "nearest"
        variants.append(config)
        for config in variants + [None, [], {}]:
            with self.subTest(config=config), self.assertRaises(ValidationError):
                validate_slot_config(config)
        with self.assertRaises(ValidationError):
            add_rule_version(self.game.id, config=base, min_bet=1, max_bet=2**63 - 1)

    def test_seed_is_inactive_and_repeat_does_not_overwrite(self):
        output = StringIO()
        call_command("seed_demo_game", stdout=output)
        game = Game.objects.get(code="demo_slot")
        self.assertFalse(game.is_active)
        self.assertEqual(game.current_rule_version.version, 1)
        call_command("seed_demo_game", stdout=output)
        self.assertEqual(game.rule_versions.count(), 1)


class VersionConcurrencyTests(TransactionTestCase):
    def test_two_connections_allocate_distinct_consecutive_versions(self):
        game = Game.objects.create(code="concurrent", name="Параллельные версии")

        def create_version(_):
            close_old_connections()
            try:
                return add_rule_version(game.id, config=example_config(), min_bet=1, max_bet=100).version
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            versions = list(pool.map(create_version, range(2)))
        self.assertEqual(sorted(versions), [1, 2])
