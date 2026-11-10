from copy import deepcopy

from django.db import transaction
from django.db.models import Max

from .models import Game, GameRuleVersion


@transaction.atomic
def add_rule_version(game_id, *, config, min_bet, max_bet, make_current=False):
    game = Game.objects.select_for_update().get(pk=game_id)
    previous = game.rule_versions.aggregate(value=Max("version"))["value"] or 0
    rules = GameRuleVersion.objects.create(
        game=game, version=previous + 1, config=deepcopy(config), min_bet=min_bet, max_bet=max_bet,
    )
    if make_current:
        game.current_rule_version = rules
        game.save(update_fields=["current_rule_version"])
    return rules


@transaction.atomic
def select_rule_version(game_id, rule_version_id):
    game = Game.objects.select_for_update().get(pk=game_id)
    game.current_rule_version = GameRuleVersion.objects.get(pk=rule_version_id, game=game)
    game.save(update_fields=["current_rule_version"])
    return game


@transaction.atomic
def set_game_active(game_id, is_active):
    if type(is_active) is not bool:
        raise ValueError("is_active должен быть boolean.")
    game = Game.objects.select_for_update().get(pk=game_id)
    game.is_active = is_active
    game.save(update_fields=["is_active"])
    return game


def get_catalog():
    return Game.objects.filter(is_active=True, current_rule_version__isnull=False).select_related("current_rule_version")
