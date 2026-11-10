from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from .models import User
from .services import get_user, set_status, update_profile


class UserTests(TestCase):
    password = "Starship-river-7624"

    def create(self, username="player_one", email=None):
        return User.objects.create_user(username=username, password=self.password, email=email)

    def test_creation_normalization_lookup_and_hash(self):
        user = self.create(" Player_One ", " Player@Example.COM ")
        user.refresh_from_db()
        self.assertEqual(user.username, "player_one")
        self.assertEqual(user.email, "player@example.com")
        self.assertTrue(user.check_password(self.password))
        self.assertNotEqual(user.password, self.password)
        self.assertEqual(get_user(user_id=user.id), user)
        self.assertEqual(get_user(username=" PLAYER_ONE "), user)
        self.assertEqual(get_user(email="PLAYER@EXAMPLE.COM"), user)
        self.assertIsNotNone(user.created_at.tzinfo)

    def test_optional_email_and_empty_normalization(self):
        self.assertIsNone(self.create(email=" ").email)
        self.assertIsNone(self.create("second_player").email)
        with self.assertRaises(ValueError):
            get_user(email=" ")

    def test_duplicates(self):
        self.create(email="one@example.com")
        with self.assertRaises(ValidationError):
            self.create(" PLAYER_ONE ")
        with self.assertRaises(ValidationError):
            self.create("second_player", "ONE@EXAMPLE.COM")

    def test_invalid_input(self):
        for username in ("", "ab", "x" * 33, "bad name", "кириллица"):
            with self.subTest(username=username), self.assertRaises(ValidationError):
                self.create(username)
        with self.assertRaises(ValidationError):
            self.create(email="not-an-email")
        with self.assertRaises(ValidationError):
            User.objects.create_user("player", "")
        with self.assertRaises(ValidationError):
            User.objects.create(username="plaintext", password="plain-secret")

    def test_profile_and_status_keep_identity(self):
        user = self.create()
        old_updated = user.updated_at
        updated = update_profile(user.id, username=" New_Name ", email="new@example.com")
        self.assertEqual(updated.username, "new_name")
        self.assertGreater(updated.updated_at, old_updated)
        blocked = set_status(user.id, "blocked")
        self.assertFalse(blocked.is_active)
        self.assertEqual(blocked.id, user.id)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(set_status(user.id, "active").is_active)
        with self.assertRaises(ValidationError):
            set_status(user.id, "deleted")

    def test_database_constraints_cannot_be_bypassed_by_update(self):
        user = self.create(email="one@example.com")
        second = self.create("second_player", "two@example.com")
        invalid_changes = [
            {"username": user.username},
            {"username": "INVALID NAME"},
            {"email": "ONE@EXAMPLE.COM"},
            {"email": ""},
            {"password": ""},
            {"status": "missing"},
            {"status": "blocked", "is_active": True},
        ]
        for changes in invalid_changes:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                User.objects.filter(pk=second.pk).update(**changes)

    def test_physical_columns_and_scope(self):
        with connection.cursor() as cursor:
            columns = {column.name for column in connection.introspection.get_table_description(cursor, "users")}
        self.assertIn("password_hash", columns)
        self.assertNotIn("password", columns)
        self.assertEqual(User.objects.get(pk=self.create("has_wallet").pk).wallet.balance, 0)
