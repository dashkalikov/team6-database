# Полная схема для Django — редакция 31.10.2026

Проектная схема содержит 18 таблиц: 8 предметных, 9 служебных Django и 1 прикладного аудита. Базовая среда: Django 5.2 LTS и Python 3.13. Точные исправляющие версии фиксируются в зависимостях серверного проекта. Стандартная auth_user отсутствует: с первой миграции используется кастомная users. Документ описывает полную проектную структуру. Модели и миграции users реализованы в VC-66, games и game_rule_versions — в VC-67; кошельки, раунды, операции и базовые купоны реализованы в VC-74. Прикладной аудит остаётся проектным.

Каждый спин хранится отдельно. Таблица игровых сеансов отсутствует. Поле слота: 3 барабана × 3 видимых символа, несколько линий. Купон бессрочный, с одной активацией на пользователя. Авторизационные сессии и игровые раунды — разные сущности.

## Проектная конфигурация

- django.contrib.auth, contenttypes, sessions, admin и messages; стандартное cookie-based messages-хранилище не создаёт таблиц. sites, token-auth, очередь задач и сторонние плагины не подключаем без отдельного требования.
- AUTH_USER_MODEL указывает на кастомную модель User с db_table='users', на базе AbstractBaseUser + PermissionsMixin, без лишних first_name/last_name. Поле Python password хранится в колонке password_hash через db_column, поэтому set_password/check_password работают стандартно. Не создавать вторую таблицу пользователей.
- DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'. У встроенных моделей с явно заданным AutoField сохраняются integer PK; автоматически создаваемые M2M-таблицы кастомной модели пользователя — bigint. Точные SQL-типы проверяются через sqlmigrate на закреплённом выпуске, включая PK автоматических промежуточных таблиц.
- USE_TZ=True. Предметные UUID генерируются uuid.uuid4 в ORM; при прямых служебных SQL-вставках — явно передаются либо используется согласованный DB-default. Не путать Python default с DEFAULT в PostgreSQL.
- В словаре предметных таблиц now()/gen_random_uuid() обозначают проектные DB-default. При реализации они выражаются через db_default/SQL либо явно отображаются на ORM-default с корректировкой словаря. Обычный default= не создаёт постоянный SQL DEFAULT.

## Изменения users

Остальные поля из data-dictionary.md сохраняются. Прежнее поле role заменяется штатным механизмом Django. Две бизнес-роли реализуются через группу administrators с конкретными permissions и обычных пользователей без этой группы. is_staff разрешает доступ к админке, но само по себе не даёт прав изменения объектов; is_superuser — отдельная техническая учётная запись с полными правами, не обычная роль администратора.

| Поле | Тип PostgreSQL | NOT NULL | Значение при создании | Назначение |
|---|---|---|---|---|
| password_hash | varchar(128) | да | обязательно через set_password | Хеш с параметрами алгоритма, Python-имя password |
| status | varchar(16) | да | active | Требование карточки users: active/blocked |
| is_active | boolean | да | true | Совместимость со стандартной авторизацией Django |
| is_staff | boolean | да | false | Допуск в административный интерфейс |
| is_superuser | boolean | да | false | Штатный флаг PermissionsMixin |
| last_login | timestamptz | нет | NULL | Последний вход |

CHECK: status IN ('active','blocked') AND is_active=(status='active'). Поля состояния обеспечивают совместимость контракта VC-66 со штатной авторизацией и изменяются совместно. Независимое изменение этих полей в админке запрещено. created_at используется вместо стандартного date_joined. Менеджер пользователя и UserAdmin адаптируются к кастомной модели. Пароль/хеш, токены и session_data не попадают в аудит. Окончательная нормализация username/email относится к открытым параметрам реализации.

## Служебные таблицы

Обозначения: «—» — нет содержательного default, значение передаёт фреймворк; generated — числовой ключ создаёт БД. Функции времени и пустые строки ниже обычно задаёт ORM, не DB-default. Структуру штатных таблиц не изменяем вручную. Для FK ниже указано поведение удаления Django ORM, которое нельзя автоматически приравнивать к SQL ON DELETE CASCADE.

### django_migrations — применённые миграции

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | bigint PK | да | generated, при указанном DEFAULT_AUTO_FIELD |
| app | varchar(255) | да | —, имя приложения |
| name | varchar(255) | да | —, имя миграции |
| applied | timestamptz | да | timezone.now в ORM |

FK нет. В штатной модели нет UNIQUE(app,name): не добавлять его на диаграмму как существующее ограничение. Это журнал факта применения, а не хранилище файлов миграций.

### django_session — серверные сессии входа

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| session_key | varchar(40) PK | да | случайный ключ механизма сессий |
| session_data | text | да | сериализованные и подписанные данные |
| expire_date | timestamptz | да | рассчитывает механизм сессий |

Индекс expire_date. Прямого FK users нет: идентификатор авторизованного пользователя хранится внутри session_data. session_key хранится в штатном формате Django, без хеширования. session_data подписывается, но не шифруется. Истёкшие сессии очищаются командой clearsessions по расписанию без отдельной таблицы очереди. Срок сессии и механизм явного отзыва всех сессий относятся к открытым параметрам реализации. Блокировка через is_active запрещает последующую аутентификацию и не означает физического удаления всех строк сессий. Явный отзыв может потребовать поиска и декодирования сессий либо отдельного индекса сессий.

### django_content_type — каталог типов моделей

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | integer PK | да | generated |
| app_label | varchar(100) | да | — |
| model | varchar(100) | да | — |

UNIQUE(app_label,model). Служит для прав и административного журнала; строка описывает модель, а не экземпляр пользователя/купона.

### auth_permission — права

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | integer PK | да | generated |
| name | varchar(255) | да | —, человекочитаемое имя |
| content_type_id | integer FK | да | —, django_content_type.id |
| codename | varchar(100) | да | —, код права |

UNIQUE(content_type_id,codename); ORM on_delete=CASCADE для content type. Поле name приведено для итогового состояния Django 5.2, а не только первой исторической миграции.

### auth_group — группы прав

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | integer PK | да | generated |
| name | varchar(150) UNIQUE | да | — |

### auth_group_permissions — права групп

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | integer PK | да | generated, AutoField приложения auth; сверить на закреплённой версии |
| group_id | integer FK | да | —, auth_group.id |
| permission_id | integer FK | да | —, auth_permission.id |

UNIQUE(group_id,permission_id), ORM CASCADE для обоих FK. Описывает N:M групп и прав.

### users_groups — группы пользователей

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | bigint PK | да | generated |
| user_id | uuid FK | да | —, users.id |
| group_id | integer FK | да | —, auth_group.id |

UNIQUE(user_id,group_id), ORM CASCADE для обоих FK. Имя таблицы получается из db_table='users' и имени поля groups.

### users_user_permissions — персональные права

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | bigint PK | да | generated |
| user_id | uuid FK | да | —, users.id |
| permission_id | integer FK | да | —, auth_permission.id |

UNIQUE(user_id,permission_id), ORM CASCADE для обоих FK. В штатном PermissionsMixin таблица существует, даже если в MVP права назначаем только через группу.

### django_admin_log — штатный журнал админки

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | integer PK | да | generated |
| action_time | timestamptz | да | время Django |
| user_id | uuid FK | да | —, users.id |
| content_type_id | integer FK | нет | NULL разрешён, django_content_type.id |
| object_id | text | нет | NULL разрешён, строковый идентификатор объекта |
| object_repr | varchar(200) | да | —, текстовое представление |
| action_flag | smallint | да | —, 1 создание / 2 изменение / 3 удаление |
| change_message | text | да | допускается пустая строка |

Штатный DB CHECK action_flag>=0, значения 1/2/3 — смысл Django, не более строгий штатный CHECK. ORM: user CASCADE, content_type SET_NULL. object_id не FK на купон/пользователя. Прямое физическое удаление пользователей в продукте запрещается; прикладной аудит ниже дополнительно защищает исторические ссылки. Журнал админки не заменяет запись старых/новых значений и действия вне админки.

## admin_audit_log — прикладной аудит

| Поле | Тип | NOT NULL | Default/источник |
|---|---|---|---|
| id | uuid PK | да | uuid.uuid4 |
| actor_id | uuid FK | да | —, users.id, RESTRICT/PROTECT |
| action | varchar(64) | да | —, coupon_create/coupon_update/coupon_disable/user_block/user_unblock/permission_change |
| target_type | varchar(32) | да | —, coupon/user |
| target_id | uuid | да | —, идентификатор объекта |
| before_data | jsonb | нет | NULL при создании |
| after_data | jsonb | да | —, снимок разрешённых полей |
| reason | text | нет | NULL, обязательность для блокировки проверяет сервис |
| created_at | timestamptz | да | время создания |

target_id — идентификатор цели без универсального FK: существование цели проверяет сервис, удаление объекта запрещено политикой хранения истории. CHECK допустимых action/target_type и соответствия пары; jsonb_typeof снимков='object', когда они не NULL. Индексы (target_type,target_id,created_at), (actor_id,created_at). INSERT-only для приложения. Аудит записывается в той же транзакции, что изменение купона/блокировка. Пароли, токены и данные сессии исключены по явному списку разрешённых полей. Полный перечень аудируемых действий уточняется до реализации администрирования.

## Уточнение слота 3×3

Одна запись rounds соответствует одному спину. Таблица game_sessions отсутствует. result сохраняет всю матрицу 3×3, включая проигрышные символы, и список выигравших линий. Базовый формат результата:

```json
{
  "format_version": 1,
  "grid": [["A", "B", "C"], ["B", "A", "C"], ["C", "B", "A"]],
  "winning_lines": []
}
```

grid[row][reel], индексы 0..2: строки сверху вниз, барабаны слева направо. Выигрышная запись содержит line_id, rule_id и amount. При аддитивном расчёте сумма amount всех winning_lines равна rounds.payout_amount; обработка пересекающихся выигрышей определяется версией правил. Нулевая выплата сохраняет полную grid и пустой winning_lines. Политика расчёта выплат относится к открытым параметрам игры.

config версии содержит format_version, reel_count=3, row_count=3, каталог символов, описание барабанов/весов, paylines с тремя координатами строк (одна на каждый барабан), paytable, трактовку общей ставки/ставки на линию и округление. Число линий, символы, вероятности и коэффициенты относятся к открытым параметрам игры. Полная конфигурация хранится неизменяемо; отдельных таблиц для комбинаций нет. Формат и ссылки символов проверяет доверенный сервис; общий CHECK jsonb_typeof не проверяет структуру матрицы.

## Изменения купонов

Купоны бессрочные. Поля starts_at/expires_at/max_redemptions отсутствуют; общий лимит активаций в MVP не входит. Повторная активация ограничивается UNIQUE(coupon_id,user_id). Проверка активности и атомарность начисления обязательны. Расширение условий купонов оформляется отдельным изменением схемы и миграцией.

## Источники и проверка

Словарь штатных таблиц составлен по документации и исходникам ветки Django stable/5.2.x:

- https://docs.djangoproject.com/en/5.2/topics/auth/customizing/
- https://docs.djangoproject.com/en/5.2/topics/http/sessions/
- https://github.com/django/django/blob/stable/5.2.x/django/contrib/auth/models.py
- https://github.com/django/django/blob/stable/5.2.x/django/contrib/admin/models.py
- https://github.com/django/django/blob/stable/5.2.x/django/db/migrations/recorder.py

Проверка выполнена на уровне проектной документации. Модели и миграции users, games, game_rule_versions и финансового ядра VC-74 проверены в отдельной PostgreSQL. Сессии, админский журнал и прикладной аудит ещё не подключены. Названия автоматических M2M-таблиц и типы ключей проверяются по сгенерированным миграциям до применения к БД. Открытые параметры перечислены в README.md.
