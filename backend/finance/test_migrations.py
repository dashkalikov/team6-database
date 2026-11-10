from django.contrib.auth.hashers import make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class FinanceUpgradeTests(TransactionTestCase):
    def test_existing_users_receive_zero_wallets_during_upgrade(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([("finance", None)])
            state = executor.loader.project_state([("accounts", "0001_initial")])
            User = state.apps.get_model("accounts", "User")
            user = User.objects.create(username="legacy_player", password=make_password("Legacy-7624-River"))
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            Wallet = executor.loader.project_state(latest).apps.get_model("finance", "Wallet")
            wallet = Wallet.objects.get(user_id=user.id)
            self.assertEqual(wallet.balance, 0)
            self.assertEqual(User.objects.get(pk=user.id).username, "legacy_player")
        finally:
            # Restore the complete schema even if the upgrade assertion fails.
            MigrationExecutor(connection).migrate(latest)
