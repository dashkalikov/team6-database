import random
import time
from functools import wraps
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from accounts.models import User
from games.engine import spin
from games.models import Game

from .models import Coupon, CouponRedemption, Round, Wallet, WalletOperation


MAX_COINS = 2**63 - 1


class RequestConflict(ValidationError):
    pass


class InsufficientBalance(ValidationError):
    pass


def _retry_transaction(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return function(*args, **kwargs)
            except DatabaseError as error:
                # Retry only known aborted transactions, never ambiguous commits.
                code = getattr(error.__cause__, "sqlstate", None)
                if code not in {"40001", "40P01"} or attempt == 2:
                    raise
                time.sleep(0.01 * (attempt + 1) + random.random() * 0.01)
    return wrapper


def _active_user(user_id):
    user = User.objects.select_for_update().get(pk=user_id)
    if not user.is_active or user.status != "active":
        raise ValidationError("Пользователь заблокирован.")
    return user


def _change_balance(wallet, delta):
    value = wallet.balance + delta
    if not 0 <= value <= MAX_COINS:
        raise ValidationError("Баланс выходит за допустимый диапазон.")
    wallet.balance = value
    wallet.save(update_fields=["balance", "updated_at"])


def _append_operation(wallet, kind, amount, *, round=None, redemption=None):
    return WalletOperation.objects.create(
        wallet=wallet, type=kind, amount=amount, balance_after=wallet.balance,
        round=round, redemption=redemption,
    )


@_retry_transaction
@transaction.atomic(durable=True)
def redeem_coupon(*, user_id, code):
    """Internal trusted service; the caller must authenticate user_id."""
    user = _active_user(user_id)
    if not isinstance(code, str) or not code.strip():
        raise ValidationError("Код купона обязателен.")
    coupon = Coupon.objects.select_for_update().get(code=code.strip().upper())
    wallet = Wallet.objects.select_for_update().get(user=user)
    existing = CouponRedemption.objects.filter(coupon=coupon, user=user).first()
    if existing:
        return existing
    if not coupon.is_active:
        raise ValidationError("Купон отключён.")
    redemption = CouponRedemption.objects.create(coupon=coupon, user=user, wallet=wallet, amount=coupon.amount)
    _change_balance(wallet, coupon.amount)
    _append_operation(wallet, WalletOperation.Type.COUPON, coupon.amount, redemption=redemption)
    return redemption


@_retry_transaction
@transaction.atomic(durable=True)
def play_round(*, user_id, game_id, request_key, bet_amount):
    """Persist the whole spin and return only after the outer COMMIT succeeds."""
    try:
        request_key = UUID(str(request_key))
        game_id = UUID(str(game_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError("Некорректный идентификатор запроса или игры.") from error
    if type(bet_amount) is not int or not 0 < bet_amount <= MAX_COINS:
        raise ValidationError("Ставка должна быть положительным целым числом.")
    user = _active_user(user_id)
    previous = Round.objects.select_related("rule_version").filter(user=user, request_key=request_key).first()
    if previous:
        if previous.rule_version.game_id != game_id or previous.bet_amount != bet_amount:
            raise RequestConflict("Ключ уже использован с другими параметрами.")
        return previous
    # SHARE allows different players to spin concurrently and prevents a rule switch.
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM games WHERE id = %s FOR SHARE", [game_id])
        if cursor.fetchone() is None:
            raise Game.DoesNotExist("Игра не найдена.")
    game = Game.objects.select_related("current_rule_version").get(pk=game_id)
    if not game.is_active or game.current_rule_version_id is None:
        raise ValidationError("Игра недоступна.")
    rules = game.current_rule_version
    if not rules.min_bet <= bet_amount <= rules.max_bet:
        raise ValidationError("Ставка вне лимитов версии игры.")
    wallet = Wallet.objects.select_for_update().get(user=user)
    if wallet.balance < bet_amount:
        raise InsufficientBalance("Недостаточно монет.")
    result, payout = spin(rules.config, bet_amount)
    # Insert the parent before its journal FK references, all within one transaction.
    completed_at = timezone.now()
    round = Round.objects.create(
        user=user, wallet=wallet, rule_version=rules, request_key=request_key,
        bet_amount=bet_amount, payout_amount=payout, result=result,
        created_at=completed_at, settled_at=completed_at,
    )
    _change_balance(wallet, -bet_amount)
    _append_operation(wallet, WalletOperation.Type.BET, bet_amount, round=round)
    if payout:
        _change_balance(wallet, payout)
        _append_operation(wallet, WalletOperation.Type.WIN, payout, round=round)
    return round
