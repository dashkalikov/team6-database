import uuid
from fractions import Fraction

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.functions import Trim
from django.db.models.lookups import Exact

from .validators import validate_slot_config


class Game(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True, validators=[RegexValidator(r"\A[a-z0-9_]{1,64}\Z")])
    name = models.CharField(max_length=120)
    description = models.TextField(null=True, blank=True)
    theme = models.CharField(max_length=64, null=True, blank=True)
    is_active = models.BooleanField(default=False)
    current_rule_version = models.ForeignKey(
        "GameRuleVersion", null=True, blank=True, on_delete=models.PROTECT,
        related_name="current_for_games", db_constraint=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "games"
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(code__regex=r"^[a-z0-9_]{1,64}$"), name="games_code_format"),
            models.CheckConstraint(condition=~Q(name="") & Exact(F("name"), Trim("name")), name="games_name_nonempty_trimmed"),
            models.CheckConstraint(condition=Q(is_active=False) | Q(current_rule_version__isnull=False), name="games_active_has_rules"),
        ]

    def clean(self):
        super().clean()
        if self.current_rule_version_id and not GameRuleVersion.objects.filter(pk=self.current_rule_version_id, game_id=self.id).exists():
            raise ValidationError({"current_rule_version": "Версия принадлежит другой игре."})

    def save(self, *args, **kwargs):
        if isinstance(self.code, str):
            self.code = self.code.strip().lower()
        if isinstance(self.name, str):
            self.name = self.name.strip()
        self.full_clean()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"updated_at"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class GameRuleVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    game = models.ForeignKey(Game, on_delete=models.PROTECT, related_name="rule_versions")
    version = models.PositiveIntegerField()
    config = models.JSONField(validators=[validate_slot_config])
    min_bet = models.BigIntegerField()
    max_bet = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "game_rule_versions"
        ordering = ["game_id", "version"]
        constraints = [
            models.UniqueConstraint(fields=["game", "version"], name="rules_game_version_unique"),
            models.UniqueConstraint(fields=["game", "id"], name="rules_game_id_unique"),
            models.CheckConstraint(condition=Q(version__gt=0), name="rules_version_positive"),
            models.CheckConstraint(condition=Q(min_bet__gt=0) & Q(max_bet__gte=F("min_bet")), name="rules_bet_limits"),
            models.CheckConstraint(condition=Exact(models.Func("config", function="jsonb_typeof", output_field=models.CharField()), Value("object")), name="rules_config_object"),
        ]

    def clean(self):
        super().clean()
        validate_slot_config(self.config)
        # A conservative bound avoids storing rules that can overflow coin amounts.
        if type(self.max_bet) is int and self.max_bet > 0:
            largest = max(Fraction(r["multiplier"]["numerator"], r["multiplier"]["denominator"]) for r in self.config["paytable"])
            if self.max_bet * len(self.config["paylines"]) * largest > 2**63 - 1:
                raise ValidationError({"max_bet": "Потенциальная выплата превышает bigint."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Версия правил неизменяема. Создайте новую версию.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Удаление версии правил запрещено.")
