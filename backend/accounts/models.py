import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import Q
from django.db.models.functions import Lower


def normalize_username(value):
    return value.strip().lower() if isinstance(value, str) else value


def normalize_email(value):
    if isinstance(value, str):
        return value.strip().lower() or None
    return value


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, username, password, email=None, **extra_fields):
        if not isinstance(password, str) or not password.strip():
            raise ValidationError({"password": "Пароль обязателен."})
        user = self.model(username=username, email=email, **extra_fields)
        validate_password(password, user)
        user.set_password(password)
        from finance.models import Wallet

        database = self._db or "default"
        with transaction.atomic(using=database):
            user.save(using=database)
            Wallet.objects.using(database).create(user=user)
        return user

    def create_superuser(self, username, password, email=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("status", "active")
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"] or extra_fields["status"] != "active":
            raise ValidationError("Суперпользователь должен быть активен и иметь служебные права.")
        return self.create_user(username, password, email, **extra_fields)

    def get_by_natural_key(self, username):
        return self.get(username=normalize_username(username))


class User(AbstractBaseUser, PermissionsMixin):
    class Status(models.TextChoices):
        ACTIVE = "active", "Активен"
        BLOCKED = "blocked", "Заблокирован"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(
        max_length=32,
        unique=True,
        validators=[RegexValidator(r"\A[a-z0-9_]{3,32}\Z", "Логин: 3–32 символа a–z, 0–9 или _.")],
    )
    email = models.EmailField(max_length=254, null=True, blank=True)
    password = models.CharField(max_length=128, db_column="password_hash")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()
    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"
        constraints = [
            models.CheckConstraint(condition=Q(username__regex=r"^[a-z0-9_]{3,32}$"), name="users_username_format"),
            models.UniqueConstraint(Lower("email"), condition=Q(email__isnull=False), name="users_email_unique"),
            models.CheckConstraint(condition=Q(email__isnull=True) | ~Q(email=""), name="users_email_not_empty"),
            models.CheckConstraint(condition=~Q(password=""), name="users_password_not_empty"),
            models.CheckConstraint(
                condition=Q(status="active", is_active=True) | Q(status="blocked", is_active=False),
                name="users_status_active_consistent",
            ),
        ]

    def clean(self):
        super().clean()
        self.username = normalize_username(self.username)
        self.email = normalize_email(self.email)
        self.is_active = self.status == self.Status.ACTIVE
        if not self.password or not self.has_usable_password():
            raise ValidationError({"password": "Требуется хеш пароля."})
        from django.contrib.auth.hashers import identify_hasher

        try:
            identify_hasher(self.password)
        except ValueError as error:
            raise ValidationError({"password": "Пароль должен быть сохранён через set_password."}) from error

    def save(self, *args, **kwargs):
        self.username = normalize_username(self.username)
        self.email = normalize_email(self.email)
        self.is_active = self.status == self.Status.ACTIVE
        self.full_clean()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"is_active", "status", "updated_at"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.username
