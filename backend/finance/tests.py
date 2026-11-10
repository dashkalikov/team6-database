import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase

from accounts.models import User
from accounts.services import set_status
from games.engine import spin
from games.models import Game
from games.services import add_rule_version, set_game_active

from . import services
from .models import Coupon, CouponRedemption, Round, Wallet, WalletOperation
from .services import InsufficientBalance, RequestConflict, play_round, redeem_coupon


def rules_config(win=True):
    path = Path(__file__).parents[1] / "games" / "examples" / "slot_3x3.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["paylines"] = [{"id": "top", "rows": [0, 0, 0]}]
    config["reels"] = [["CHERRY"] * 3] * 3 if win else [["CHERRY"] * 3, ["LEMON"] * 3, ["SEVEN"] * 3]
    return config


class RoundTransactionTests(TransactionTestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin_user", "River-7624-Admin", is_staff=True)
        self.user = self.funded_user(100)
        self.game = Game.objects.create(code="atomic_slot", name="Атомарный слот")
        add_rule_version(self.game.id, config=rules_config(), min_bet=1, max_bet=100, make_current=True)
        set_game_active(self.game.id, True)

    def funded_user(self, amount):
        suffix = uuid4().hex[:10]
        user = User.objects.create_user("player_" + suffix, "River-7624-Player")
        coupon = Coupon.objects.create(code="TEST_" + suffix.upper(), amount=amount, is_active=True, created_by=self.admin, updated_by=self.admin)
        redeem_coupon(user_id=user.id, code=coupon.code)
        return user

    def play(self, **changes):
        values = dict(user_id=self.user.id, game_id=self.game.id, request_key=uuid4(), bet_amount=10)
        values.update(changes)
        return play_round(**values)

    def assert_rolled_back(self):
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 100)
        self.assertEqual(Round.objects.count(), 0)
        self.assertEqual(WalletOperation.objects.filter(wallet__user=self.user).count(), 1)

    def test_win_commits_round_wallet_and_both_operations(self):
        round = self.play()
        self.assertEqual((round.status, round.bet_amount, round.payout_amount), ("completed", 10, 20))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 110)
        operations = {op.type: op for op in round.operations.all()}
        self.assertEqual(operations["bet_debit"].balance_after, 90)
        self.assertEqual(operations["win_credit"].balance_after, 110)
        self.assertEqual(len(round.result["grid"]), 3)
        self.assertEqual(round.result["winning_lines"][0]["amount"], 20)

    def test_loss_commits_only_debit(self):
        add_rule_version(self.game.id, config=rules_config(False), min_bet=1, max_bet=100, make_current=True)
        round = self.play()
        self.assertEqual(round.payout_amount, 0)
        self.assertEqual(round.result["winning_lines"], [])
        self.assertEqual(list(round.operations.values_list("type", flat=True)), ["bet_debit"])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 90)

    def test_retry_returns_same_result_after_rule_switch_and_deactivation(self):
        key = uuid4()
        first = self.play(request_key=key)
        add_rule_version(self.game.id, config=rules_config(False), min_bet=1, max_bet=100, make_current=True)
        set_game_active(self.game.id, False)
        with patch("finance.services.spin", side_effect=AssertionError("RNG called on replay")):
            repeated = self.play(request_key=str(key))
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(first.result, repeated.result)
        self.assertEqual(Round.objects.count(), 1)
        self.assertEqual(first.operations.count(), 2)

    def test_key_reuse_with_changed_parameters_is_rejected(self):
        key = uuid4()
        self.play(request_key=key)
        with self.assertRaises(RequestConflict):
            self.play(request_key=key, bet_amount=11)
        with self.assertRaises(RequestConflict):
            self.play(request_key=key, game_id=uuid4())
        self.assertEqual(Round.objects.count(), 1)

    def test_failures_after_each_financial_write_rollback_everything(self):
        # Fail immediately after the actual write, not before it.
        for helper_name in ("_change_balance", "_append_operation"):
            original = getattr(services, helper_name)
            for failure_number in (1, 2):
                calls = []

                def fail_after_write(*args, **kwargs):
                    value = original(*args, **kwargs)
                    calls.append(1)
                    if len(calls) == failure_number:
                        raise RuntimeError("Injected failure")
                    return value

                with self.subTest(helper=helper_name, stage=failure_number):
                    with patch("finance.services." + helper_name, side_effect=fail_after_write), self.assertRaises(RuntimeError):
                        self.play()
                    self.assert_rolled_back()

        original_create = Round.objects.create

        def fail_after_round(*args, **kwargs):
            original_create(*args, **kwargs)
            raise RuntimeError("Failure after round insert")

        with patch.object(Round.objects, "create", side_effect=fail_after_round), self.assertRaises(RuntimeError):
            self.play()
        self.assert_rolled_back()

    def test_commit_checks_reject_missing_win_operation(self):
        original = services._append_operation

        def omit_win(wallet, kind, amount, **kwargs):
            if kind != WalletOperation.Type.WIN:
                return original(wallet, kind, amount, **kwargs)

        with patch("finance.services._append_operation", side_effect=omit_win), self.assertRaises(IntegrityError):
            self.play()
        self.assert_rolled_back()

    def test_engine_error_and_overflow_rollback(self):
        with patch("finance.services.spin", side_effect=RuntimeError("Calculation failed")), self.assertRaises(RuntimeError):
            self.play()
        self.assert_rolled_back()
        result, _ = spin(rules_config(), 10)
        with patch("finance.services.spin", return_value=(result, 2**63 - 1)), self.assertRaises(ValidationError):
            self.play()
        self.assert_rolled_back()

    def test_invalid_or_forbidden_requests_make_no_changes(self):
        for changes in ({"bet_amount": 0}, {"bet_amount": True}, {"bet_amount": 1.5}, {"bet_amount": 101}, {"request_key": "invalid"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.play(**changes)
            self.assert_rolled_back()
        set_status(self.user.id, "blocked")
        with self.assertRaises(ValidationError):
            self.play()
        self.assert_rolled_back()

    def test_coupon_atomicity_replay_and_saved_amount(self):
        coupon = Coupon.objects.get(redemptions__user=self.user)
        coupon.amount = 200
        coupon.is_active = False
        coupon.save()
        existing = redeem_coupon(user_id=self.user.id, code=coupon.code.lower())
        self.assertEqual(existing.amount, 100)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 100)
        self.assertEqual(CouponRedemption.objects.filter(user=self.user).count(), 1)
        other = Coupon.objects.create(code="SECOND", amount=50, is_active=True, created_by=self.admin, updated_by=self.admin)
        with patch("finance.services._append_operation", side_effect=RuntimeError("Journal unavailable")), self.assertRaises(RuntimeError):
            redeem_coupon(user_id=self.user.id, code=other.code)
        self.assertFalse(CouponRedemption.objects.filter(coupon=other).exists())
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 100)

    def test_database_guards_protect_history_and_balances(self):
        round = self.play()
        for queryset, changes in ((round.operations.all(), {"amount": 1}), (Round.objects.filter(pk=round.pk), {"payout_amount": 0}), (CouponRedemption.objects.filter(user=self.user), {"amount": 1})):
            with self.assertRaises(IntegrityError), transaction.atomic():
                queryset.update(**changes)
            with self.assertRaises(IntegrityError), transaction.atomic():
                queryset.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Wallet.objects.filter(user=self.user).update(balance=111)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 110)

    def test_wallet_provisioning_is_part_of_user_creation(self):
        with patch("django.db.models.query.QuerySet.create", side_effect=RuntimeError("Wallet failed")), self.assertRaises(RuntimeError):
            User.objects.create_user("failed_registration", "River-7624-Player")
        self.assertFalse(User.objects.filter(username="failed_registration").exists())
        self.assertEqual(self.admin.wallet.balance, 0)

    def test_durable_service_rejects_nested_outer_transaction(self):
        with transaction.atomic(), self.assertRaises(RuntimeError):
            self.play()
        self.assert_rolled_back()

    def test_fractional_line_awards_sum_to_total_floor(self):
        config = rules_config()
        config["paylines"].append({"id": "middle", "rows": [1, 1, 1]})
        config["paytable"][0]["multiplier"] = {"numerator": 1, "denominator": 3}
        result, payout = spin(config, 5)
        self.assertEqual(payout, 3)
        self.assertEqual(sum(line["amount"] for line in result["winning_lines"]), payout)

    def parallel(self, values):
        barrier = Barrier(2)

        def worker(kwargs):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return play_round(**kwargs).pk
            except InsufficientBalance:
                return "insufficient"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, values))

    def test_parallel_same_key_spends_and_pays_once(self):
        kwargs = dict(user_id=self.user.id, game_id=self.game.id, request_key=uuid4(), bet_amount=10)
        ids = self.parallel([kwargs, dict(kwargs)])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Round.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 110)

    def test_parallel_different_keys_cannot_overspend(self):
        user = self.funded_user(15)
        add_rule_version(self.game.id, config=rules_config(False), min_bet=1, max_bet=100, make_current=True)
        values = [dict(user_id=user.id, game_id=self.game.id, request_key=uuid4(), bet_amount=10) for _ in range(2)]
        ids = self.parallel(values)
        self.assertEqual(ids.count("insufficient"), 1)
        self.assertEqual(Round.objects.filter(user=user).count(), 1)
        self.assertEqual(Wallet.objects.get(user=user).balance, 5)

    def test_other_connection_cannot_see_intermediate_state(self):
        reached, release = Event(), Event()
        original = services._append_operation

        def pause_after_debit(wallet, kind, amount, **kwargs):
            value = original(wallet, kind, amount, **kwargs)
            if kind == WalletOperation.Type.BET:
                reached.set()
                if not release.wait(timeout=10):
                    raise RuntimeError("Reader timeout")
            return value

        def worker():
            close_old_connections()
            try:
                return self.play().pk
            finally:
                connection.close()

        with patch("finance.services._append_operation", side_effect=pause_after_debit), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(reached.wait(timeout=10))
                self.assertEqual(Wallet.objects.get(user=self.user).balance, 100)
                self.assertEqual(Round.objects.count(), 0)
                self.assertEqual(WalletOperation.objects.filter(wallet__user=self.user).count(), 1)
            finally:
                release.set()
            future.result(timeout=10)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 110)
