# Сервер casi no

Модель игрового автомата, версии правил, миграции и пример описаны в [games/README.md](games/README.md) (VC-67).

Кошелёк, журнал и атомарный спин описаны в [finance/README.md](finance/README.md) (VC-74). Актуальная команда полной проверки: `python backend/manage.py test accounts games finance` из корня проекта с выбранным виртуальным окружением.

## VC-66: пользователи

Реализованы модель accounts.User, таблица users, начальная миграция, менеджер создания и служебные операции чтения и изменения. Модель использует AbstractBaseUser и PermissionsMixin. Штатные группы и права создаются зависимостями миграции. В VC-74 менеджер создания дополнен атомарным созданием нулевого кошелька.

Политика данных:

- Логин обязателен: 3–32 символа a–z, 0–9, подчёркивание. Пробелы по краям удаляются, регистр приводится к нижнему.
- Email необязателен; пустая строка преобразуется в NULL. Указанный адрес валидируется, нормализуется в нижний регистр и уникален без учёта регистра.
- Пароль передаётся в create_user и хешируется через set_password. В БД используется колонка password_hash. Открытые пароли не сохраняются.
- Статусы active/blocked. is_active изменяется вместе со status; противоречия запрещены CHECK БД.
- created_at и updated_at задаются ORM; updated_at обновляется при save, включая update_fields. QuerySet.update и bulk_create не выполняют валидацию модели и обновление времени; прикладные записи выполняются менеджером и сервисами, а не этими обходными путями.
- UNIQUE и CHECK БД остаются последним уровнем защиты при конкурентных записях. ValidationError сообщает об ошибках валидации, IntegrityError возможна при гонке уникальности; вызывающий слой должен обрабатывать обе ошибки.
- Изменение профиля доступно как внутренняя операция VC-66; пользовательский интерфейс редактирования профиля в MVP отсутствует.
- HTTP API, вход, сессии, админский UI и начисления отсутствуют.

## Установка

Из корня проекта в PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

Параметры PostgreSQL передаются через переменные окружения PGHOST, PGPORT, PGDATABASE, PGUSER и PGPASSWORD. Файлы .env автоматически не загружаются. Учётная запись должна иметь права создания таблиц в целевой базе; для тестов — создания отдельной тестовой БД. Пароли в Git не сохраняются.

```powershell
$env:PGHOST = '127.0.0.1'
$env:PGPORT = '5432'
$env:PGDATABASE = 'casi_no'
$env:PGUSER = 'casi_no'
# PGPASSWORD задаётся локально, без публикации значения.
.\.venv\Scripts\python.exe backend/manage.py check
.\.venv\Scripts\python.exe backend/manage.py migrate --plan
.\.venv\Scripts\python.exe backend/manage.py migrate
.\.venv\Scripts\python.exe backend/manage.py test accounts
```

Перед применением миграций база casi_no и её владелец создаются администратором PostgreSQL. Подключение DBeaver к postgres само по себе их не создаёт. Настройки по умолчанию предназначены для локальной разработки. DJANGO_SECRET_KEY должен быть задан отдельно при развёртывании.

## Служебные операции

```python
from accounts.models import User
from accounts.services import get_user, update_profile, set_status

# password поступает из вызывающего кода, не записывается в логи.
user = User.objects.create_user(username="player_1", password=password)
same_user = get_user(user_id=user.id)
same_user = get_user(username="PLAYER_1")
update_profile(user.id, username="player_1", email="player@example.com")
set_status(user.id, "blocked")
```

Блокировка изменяет состояние существующей записи без удаления. Полномочия вызывающего кода проверяются будущим API-слоем; сервисы сами по себе не являются публичными обработчиками.
