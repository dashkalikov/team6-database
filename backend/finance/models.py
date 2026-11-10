import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q, Value
from django.db.models.lookups import Exact
from django.utils import timezone


class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet")
    balance = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "wallets"
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name="wallets_balance_nonnegative"),
            models.UniqueConstraint(fields=["id", "user"], name="wallets_id_user_unique"),
        ]


class Coupon(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True)
    amount = models.BigIntegerField()
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_coupons", db_column="created_by")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="updated_coupons", db_column="updated_by")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "coupons"
        constraints = [
            models.CheckConstraint(condition=Q(code__regex=r"^[A-Z0-9_-]{1,64}$"), name="coupons_code_format"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="coupons_amount_positive"),
        ]

    def save(self, *args, **kwargs):
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()
        self.full_clean()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"updated_at"}
        return super().save(*args, **kwargs)


class CouponRedemption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT)
    amount = models.BigIntegerField()
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "coupon_redemptions"
        constraints = [
            models.UniqueConstraint(fields=["coupon", "user"], name="redemptions_coupon_user_unique"),
            models.UniqueConstraint(fields=["id", "wallet"], name="redemptions_id_wallet_unique"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="redemptions_amount_positive"),
        ]


class Round(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rounds")
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="rounds")
    rule_version = models.ForeignKey("games.GameRuleVersion", on_delete=models.PROTECT, related_name="rounds")
    request_key = models.UUIDField()
    bet_amount = models.BigIntegerField()
    status = models.CharField(max_length=16, default="completed")
    payout_amount = models.BigIntegerField()
    result = models.JSONField()
    cancellation_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    settled_at = models.DateTimeField()

    class Meta:
        db_table = "rounds"
        indexes = [models.Index(fields=["user", "-created_at", "id"], name="rounds_user_history")]
        constraints = [
            models.UniqueConstraint(fields=["user", "request_key"], name="rounds_user_request_unique"),
            models.UniqueConstraint(fields=["id", "wallet"], name="rounds_id_wallet_unique"),
            models.CheckConstraint(condition=Q(bet_amount__gt=0, payout_amount__gte=0), name="rounds_amounts_valid"),
            models.CheckConstraint(condition=Q(status="completed", cancellation_reason__isnull=True), name="rounds_completed_only"),
            models.CheckConstraint(condition=Q(settled_at__gte=F("created_at")), name="rounds_time_order"),
            models.CheckConstraint(condition=Exact(models.Func("result", function="jsonb_typeof", output_field=models.CharField()), Value("object")), name="rounds_result_object"),
        ]


class WalletOperation(models.Model):
    class Type(models.TextChoices):
        COUPON = "coupon_credit", "Купон"
        BET = "bet_debit", "Ставка"
        WIN = "win_credit", "Выигрыш"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="operations")
    type = models.CharField(max_length=24, choices=Type.choices)
    amount = models.BigIntegerField()
    balance_after = models.BigIntegerField()
    round = models.ForeignKey(Round, on_delete=models.PROTECT, null=True, blank=True, related_name="operations")
    redemption = models.OneToOneField(CouponRedemption, on_delete=models.PROTECT, null=True, blank=True, related_name="operation")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wallet_operations"
        indexes = [models.Index(fields=["wallet", "-created_at", "id"], name="operations_wallet_history")]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0, balance_after__gte=0), name="operations_amounts_valid"),
            models.CheckConstraint(
                condition=Q(type="coupon_credit", round__isnull=True, redemption__isnull=False)
                | Q(type__in=["bet_debit", "win_credit"], round__isnull=False, redemption__isnull=True),
                name="operations_source_valid",
            ),
            models.UniqueConstraint(fields=["round", "type"], condition=Q(round__isnull=False), name="operations_round_type_unique"),
        ]
