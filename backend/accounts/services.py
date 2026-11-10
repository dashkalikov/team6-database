from django.db import transaction

from .models import User, normalize_email, normalize_username


def get_user(*, user_id=None, username=None, email=None):
    if sum(value is not None for value in (user_id, username, email)) != 1:
        raise ValueError("Укажите ровно один идентификатор поиска.")
    if user_id is not None:
        return User.objects.get(pk=user_id)
    if username is not None:
        return User.objects.get(username=normalize_username(username))
    normalized = normalize_email(email)
    if not normalized:
        raise ValueError("Email для поиска не может быть пустым.")
    return User.objects.get(email__iexact=normalized)


@transaction.atomic
def update_profile(user_id, *, username, email):
    user = User.objects.select_for_update().get(pk=user_id)
    user.username = username
    user.email = email
    user.save(update_fields=["username", "email"])
    return user


@transaction.atomic
def set_status(user_id, status):
    user = User.objects.select_for_update().get(pk=user_id)
    user.status = status
    user.save(update_fields=["status"])
    return user
