# BP Tracker backend — нотатки для розробника

Цей документ фіксує причини рішень, поточний технічний борг і, найголовніше,
конкретні контракти, які має дотримуватись клієнт (frontend/native). Публічний
[README.md](README.md) лишається коротким фасадом — усі деталі тут.

## Архітектура модулів

Кожен домен — окремий самодостатній пакет у корені репозиторію (проєкт свідомо
без пакета `app/`: `measurements/`, `prescriptions/`, `reminders/` лежать поруч
з `main.py`, `config.py`, `db.py`). Усередині пакета — той самий шар:

- `models/` — ланцюжок SQLModel-класів: `Base` (спільні поля й валідація) →
  клас із `table=True` (додає `id`, серверні дефолти, FK) → `Create` (тіло
  запиту на створення) → `Read` (тіло відповіді) → `Update` (тіло часткового
  оновлення, лише де редагування взагалі має сенс).
- `crud/` — функції доступу до БД, завжди приймають `session` і `user_id`,
  завжди скоупляться за `user_id` (або за батьківською сутністю, якщо в таблиці
  немає власного `user_id`, як-от `medication_items`).
- `router/` — FastAPI-роутер плюс залежність `CurrentUserId`, яка резолвиться
  до реального `auth.deps.get_current_user_id` (декодує Bearer JWT).

Виняток — `measurements`: це найстаріший модуль, і він досі має пласку
структуру (`models.py`/`crud.py`/`router.py` — по одному файлу, без
під-пакетів), тоді як `prescriptions` і `reminders` уже розбиті на пакети,
бо в них по кілька сутностей. Пласка структура `measurements` — це не
недогляд, а те, що модуль ще не переростав файл на файл; вирівнювати його під
пакет має сенс лише тоді, коли з'явиться друга сутність усередині.

Модуль `auth` має під-пакет `webauthn/` (моделі `WebAuthnCredential`,
`WebAuthnChallenge`, CRUD, сервіс, роутер). Решта файлів `auth` — пласкі
(`models.py`, `security.py`, `crud.py`, `deps.py`, `router.py`, `service.py`).
`security.py` — чисті функції без FastAPI й без БД (кодування/декодування JWT,
генерація та хешування токенів magic-link і refresh).

## Тимчасові рішення та технічний борг

- Діапазони `measurements` (`sys`/`dia`/`pulse`) перевіряються лише на рівні
  Pydantic (`Field(ge=..., le=...)`). CHECK-обмеження на рівні БД відкладені —
  поки що ніщо, крім самого API, не заважає вставити рядок напряму в базу з
  некоректними значеннями.
- Поля `source` в `measurements` немає навмисно: у старому C#-проєкті воно
  належало зовнішньому OCR-пайплайну, якого в цьому проєкті поки що немає.
- Проєкції на читання ще не побудовані: немає ендпоінта «сьогодні/найближче»
  для нагадувань і немає окремого ендпоінта історії підтверджень. Схема вже
  готова під них — жодне поле не видаляється і не обмежується заднім числом,
  просто самих ендпоінтів ще нема.
- «Двигун курсу» (розмотування `course` на відміну від `ongoing`) свідомо не
  реалізований — поля `course_start`/`course_intakes` зберігаються, але нічого
  їх поки не інтерпретує.
- **`get_current_user_id` — stateless.** Він лише декодує JWT і перевіряє
  підпис/термін дії; у базу даних не заглядає. Якщо користувача видалили чи
  токен скомпрометований до завершення терміну дії — запит із валідним
  токеном усе одно пройде. Перевірка існування користувача в БД на кожен
  запит — можливе майбутнє доповнення, свідомо не додане зараз (ціна:
  зайвий SELECT на кожен захищений запит).
- **Rate limiting (D-003):** обмеження частоти запитів відкладене до публічної
  експозиції (LAN + VPN → жодного анонімного нападника).

## Наскрізні правила

Джерело істини для наскрізних правил — [docs/conventions.md](docs/conventions.md).
Коротко про час і пояс: клієнт — єдиний, хто знає часовий пояс користувача.
Бек зберігає моменти часу як `timestamptz` і ніколи не інтерпретує зсув
пояса; класифікація моменту в конкретний слот (`Morning`/`Day`/`Evening`) і
календарну дату — це робота клієнта, а не бекенду.

Виняток — серверний рендеринг CSV-експорту: клієнт передає IANA-ідентифікатор
часового поясу як поле `tz` у запиті `POST /export/csv`, і бек використовує
його для форматування дат у файлі. Часовий пояс **не зберігається** в базі
(D-008).

## Контракти для клієнта (frontend / native)

**Усі ендпоінти нижче, крім magic-link request/confirm і WebAuthn
authenticate, вимагають заголовок `Authorization: Bearer <access_token>`.**
Без токена, з протермінованим чи невалідним підписом — 401.

Access-токен — stateless JWT, живе 15 хвилин. Refresh-токен — серверний
(таблиця `sessions`), ротується при кожному використанні, живе 30 днів.
Пред'явлення вже відкликаного refresh-токена трактується як компрометація
і гасить **усі** сесії користувача (D-007).

### auth — magic-link (`/auth`)

| Метод | Шлях | Тіло запиту | Відповідь | Помилки |
|---|---|---|---|---|
| POST | `/auth/magic-link/request` | `{email:EmailStr}` | 202, `{detail}` | — (завжди 202, захист від перелічування) |
| POST | `/auth/magic-link/confirm` | `{token:str}` | 200, `TokenResponse` | 401 |
| POST | `/auth/refresh` | `{refresh_token:str}` | 200, `TokenResponse` | 401 |
| POST | `/auth/logout` | `{refresh_token:str}` | 204 | — |
| POST | `/auth/logout-all` | — | 204 | — |
| GET | `/auth/sessions` | — | 200, `list[SessionRead]` | — |

`TokenResponse`: `{access_token, refresh_token, token_type:"bearer", expires_in}`.
`SessionRead`: `{id, created_at, last_used_at, expires_at, user_agent}`.

Реєстрації як окремого ендпоінта немає: `POST /auth/magic-link/request` створює
користувача автоматично, якщо email є у `ALLOWED_EMAILS`.

### auth — WebAuthn/passkey (`/auth/webauthn`)

Usernameless flow (D-004): клієнт нічого не надсилає для автентифікації,
платформа пропонує наявні passkey, сервер упізнає користувача за `credential_id`.

| Метод | Шлях | Тіло запиту | Відповідь | Помилки | Auth |
|---|---|---|---|---|---|
| POST | `/auth/webauthn/register/options` | — | 200, JSON | 404 | ✓ |
| POST | `/auth/webauthn/register/verify` | dict (WebAuthn response) | 201, `WebAuthnCredentialRead` | 400 | ✓ |
| POST | `/auth/webauthn/authenticate/options` | — | 200, JSON | — | ✗ |
| POST | `/auth/webauthn/authenticate/verify` | dict (WebAuthn response) | 200, `TokenResponse` | 401 | ✗ |
| GET | `/auth/webauthn/credentials` | — | 200, `list[WebAuthnCredentialRead]` | — | ✓ |
| DELETE | `/auth/webauthn/credentials/{credential_id}` | — | 204 | 404 | ✓ |

`WebAuthnCredentialRead`: `{id, label, transports, created_at, last_used_at}`.

### users (`/users`)

| Метод | Шлях | Тіло запиту | Відповідь | Помилки |
|---|---|---|---|---|
| GET | `/users/me` | — | 200, `UserRead` | 404 |
| PATCH | `/users/me` | `{display_name?:str}` (max 120, stripped, empty -> null) | 200, `UserRead` | 404 |
| DELETE | `/users/me` | — | 204 | 404 |

`UserRead`: `{id, email, display_name, created_at}`.

`DELETE /users/me` каскадно видаляє всі пов'язані рядки (measurements,
prescriptions, sessions, credentials тощо) через `ON DELETE CASCADE`.

### measurements (`/measurements`)

| Метод | Шлях | Тіло запиту | Відповідь | Помилки |
|---|---|---|---|---|
| POST | `/measurements` | `{sys:int, dia:int, pulse:int, recorded_at?:timestamptz}` | 201, `{id, sys, dia, pulse, recorded_at}` | 422 — вихід за діапазон |
| GET | `/measurements?days=90` | — | 200, список, найновіші перші | — |
| GET | `/measurements/{id}` | — | 200 | 404 |
| PATCH | `/measurements/{id}` | будь-яка підмножина `{sys,dia,pulse,recorded_at}` | 200 | 404 |
| DELETE | `/measurements/{id}` | — | 204 | 404 |
| POST | `/measurements/analyze` | `multipart/form-data`, поле `image` (файл) | 200, `{sys, dia, pulse}` | 400, 413, 422, 502 |

Діапазони: `sys` 40–300, `dia` 20–200, `pulse` 30–250; вихід за межі — 422 ще
до звернення до бази. `recorded_at` — опційне поле; якщо клієнт його не
надішле, бек підставить `now()`. Відповідь ніколи не містить `user_id`.
Параметр запиту `days` у списку — ціле число від 1 до 365, за замовчуванням 90.

`POST /measurements/analyze` — безстанційне розпізнавання показників тиску з
фото через Gemini API. Приймає файл зображення (`image/*`, ≤ 10 МБ), повертає
розпізнані `sys`/`dia`/`pulse`. Помилки: 400 — файл відсутній або не
зображення, 413 — перевищення розміру, 422 — не вдалося розпізнати значення,
502 — Gemini API недоступний.

### prescriptions (`/prescriptions`)

| Метод | Шлях | Тіло запиту | Відповідь | Помилки |
|---|---|---|---|---|
| POST | `/prescriptions` | `{doctor:str, prescribed_on:date, is_active?:bool=true}` | 201, `{id, doctor, prescribed_on, is_active, created_at}` | 422 |
| GET | `/prescriptions` | — | 200, усі призначення користувача | — |
| GET | `/prescriptions/{id}` | — | 200 | 404 |
| PATCH | `/prescriptions/{id}` | підмножина `{doctor,prescribed_on,is_active}` | 200 | 404 |
| DELETE | `/prescriptions/{id}` | — | 204 (каскадом видаляє позиції ліків) | 404 |

Важливо: у користувача може бути кілька одночасно активних призначень —
жодного інваріанта «лише одне активне» в цьому проєкті немає (типовий кейс —
кілька лікарів). Відповідь не містить `user_id`.

### medication items (`/prescriptions/{prescription_id}/items`)

| Метод | Шлях | Тіло запиту | Відповідь | Помилки |
|---|---|---|---|---|
| POST | `/prescriptions/{prescription_id}/items` | див. нижче | 201 | 404 — немає такого призначення |
| GET | `/prescriptions/{prescription_id}/items` | — | 200, список | 404 |
| GET | `/prescriptions/{prescription_id}/items/{item_id}` | — | 200 | 404 |
| PATCH | `/prescriptions/{prescription_id}/items/{item_id}` | підмножина полів нижче | 200 | 404 |
| DELETE | `/prescriptions/{prescription_id}/items/{item_id}` | — | 204 | 404 |

Тіло створення позиції ліків:

```
{
  medicine: str,
  condition?: str,
  when_slots: [WhenSlot],
  dose_amount: str,
  dose_unit?: DoseUnit,
  freq_count: int,
  freq_period: int,
  freq_period_unit: FreqPeriodUnit,
  course_type: CourseType = "ongoing",
  course_start?: timestamptz,
  course_intakes?: int
}
```

Точні значення enum-ів (рядки, як є, без перекладу):
`WhenSlot` = `Morning` | `Day` | `Evening`;
`DoseUnit` = `tablet` | `mg` | `ml` | `drop` | `mcg` | `IU`;
`FreqPeriodUnit` = `h` | `d` | `wk`;
`CourseType` = `ongoing` | `course`.
Невалідне значення enum-а — 422.

### reminder config (`/reminders/config`, один рядок на користувача)

| Метод | Шлях | Тіло запиту | Відповідь | Помилки |
|---|---|---|---|---|
| GET | `/reminders/config` | — | 200, `{morning_time, day_time, evening_time, max_reminders, duration_minutes}` | 404 — конфіг ще не налаштовано |
| PUT | `/reminders/config` | усі п'ять полів вище | 200, upsert | 422 |

Часи серіалізуються як рядок `"HH:MM:SS"` (напр. `"08:00:00"`). `PUT` — це
повне перезаписування, часткового оновлення тут немає: клієнт завжди
надсилає всі п'ять полів. Відповідь не містить `user_id`.

### intake reports (`/reminders/intake-reports`)

Це найважливіша частина контракту — саме тут найбільше шансів розійтися з
фронтендом, тож поведінку варто зафіксувати явно, до таблиці.

Ключ ідемпотентності — трійка `(user_id, period, date)`: на кожного
користувача, на кожен слот, на кожну дату існує щонайбільше один рядок
(унікальність не глобальна за `period`+`date` — вона завжди прив'язана до
конкретного користувача). `POST` — це upsert, і він завжди повертає 201,
навіть коли насправді перезаписує вже існуючий рядок (свідоме спрощення на
цьому етапі проєкту, а не недогляд).

Поле `taken_at` у тілі запиту — опційне, і його наявність чи відсутність
означає різну поведінку:
- **якщо `taken_at` відсутнє** — бек підставляє `now()`. Це сценарій
  підтвердження «просто зараз», того самого дня, незалежно від того, чи
  клієнт встиг у вікно нагадування, чи ні.
- **якщо `taken_at` присутнє** — бек зберігає його точно так, як прийшло,
  без жодної інтерпретації. Це два сценарії: перше підтвердження заднім
  числом за інший день (коли автоматичний `now()` був би неправдою), і
  редагування вже існуючого запису (повторний виклик того самого
  `(user_id, period, date)` — це НЕ помилка, а свідомий перезапис; `snapshot`
  при цьому перебудовується заново з поточних активних призначень).

**Важливий наслідок для клієнта.** Оскільки `taken_at` опційне, повторний
`POST` того самого `(user_id, period, date)` БЕЗ `taken_at` перезапише вже наявний
`taken_at` на поточний `now()`, а не залишить його без змін. Якщо клієнт
хоче відредагувати рядок з іншої причини (наприклад, лише щоб бек
перебудував `snapshot` під поточні активні призначення) і йому не важливо
міняти сам момент прийому — він мусить явно повторно надіслати старе
значення `taken_at`, інакше момент прийому мовчки зміниться на «зараз». Це
свідомо прийнята поведінка на цьому етапі проєкту (не аргумент робити
`taken_at` обов'язковим при повторному записі), але клієнт має її
враховувати: «редагування без наміру змінити час» і «відсутність
`taken_at`» — це не те саме, і бек їх не розрізняє.

Клієнт завжди надсилає `date` і `period` окремими полями, а не виводить їх
із `taken_at` — це пряме продовження правила «класифікує клієнт» із
[docs/conventions.md](docs/conventions.md): той самий момент часу може
потрапити в різний слот і навіть різну календарну дату залежно від
часового поясу, а це знає лише клієнт.

| Метод | Шлях | Тіло запиту | Відповідь | Помилки |
|---|---|---|---|---|
| POST | `/reminders/intake-reports` | `{period:WhenSlot, date:date, taken_at?:timestamptz}` | 201, upsert | 422 |
| GET | `/reminders/intake-reports` | — | 200, список | — |
| GET | `/reminders/intake-reports/{id}` | — | 200 | 404 |

Відповідь: `{id, period, date, taken_at, recorded_at, snapshot}`.
`recorded_at` — завжди серверний `now()` останнього запису (створення або
редагування), клієнт його не надсилає. `snapshot` — масив
`[{medicine, amount, condition}, ...]`, самодостатня копія того, що
приймалось, зібрана з усіх активних призначень користувача на момент
запису. Відповідь не містить `user_id`, `is_late` і `prescription_id` —
усі три поля свідомо прибрані:
- **`is_late`** прибрано, бо «рано/вчасно/пізно» — це проєкція на читання,
  яку завжди можна порахувати з уже збережених чесних моментів часу; зберігати
  її саму по собі — означає заморожувати висновок, який може змінитися
  разом із визначенням «вікна».
- **`prescription_id`** прибрано, бо `snapshot` збирається з УСІХ активних
  призначень користувача для цього слоту, а не з одного конкретного —
  єдиний `prescription_id` як посилання «про яке це призначення» був би
  нечесним.

DELETE і окремого PATCH для `intake_reports` немає — єдиний шлях запису це
`POST` (upsert).

### export (`/export`)

| Метод | Шлях | Тіло запиту | Відповідь | Помилки |
|---|---|---|---|---|
| POST | `/export/csv` | `{tz:str, date_from?:date, date_to?:date}` | 202, `{message, email}` | 404 — user not found; 422 — невалідний timezone або date_from > date_to; 429 — cooldown active |

`tz` — обов'язковий IANA-ідентифікатор часового поясу (напр. `"Europe/Kyiv"`).
`date_from` та `date_to` — опційні календарні дати (`YYYY-MM-DD`) для фільтрації періоду експорту.
Бек форматує дати відповідно до цього поясу та надсилає два вкладення на email користувача
через email outbox (асинхронно, з ретраями): CSV (сирі дані) та друкований PDF-звіт для лікаря
(A4 portrait, дати в форматі `DD.MM.YYYY`). Cooldown між експортами — `export_cooldown_minutes`
(за замовчуванням 10 хв).

## Стан модулів

| Модуль | Стан |
|---|---|
| auth | ✓ magic-link + WebAuthn/passkey, JWT access/refresh, сесії, покрито тестами |
| users | ✓ профіль (display_name) + видалення акаунта, покрито тестами |
| measurements | ✓ CRUD + фото-розпізнавання (Gemini), покрито тестами |
| prescriptions | ✓ реалізовано, покрито тестами |
| reminders | ✓ реалізовано, покрито тестами |
| export | ✓ CSV + PDF експорт (з фільтрацією за датами) на email, покрито тестами |
| email_infra | ✓ SMTP + email outbox worker, покрито тестами |
| cleanup | ✓ фоновий очищувач (magic links, challenges, sessions) |

Усі модулі підключені один до одного: `measurements`, `prescriptions`,
`reminders`, `export` і `users` вимагають реальний Bearer-токен від `auth`,
і всі їхні `user_id` мають FK на `users.id` з `ON DELETE CASCADE`.

## Скрипт міграції з legacy-бази

`scripts/import_from_legacy.py` — автономний утилітарний інструмент для перенесення даних
з legacy C#-бекенду (Postgres) в нову FastAPI-базу. Скрипт переносить лише дві таблиці:

| Legacy таблиця | Нова таблиця |
|---|---|
| `Measurements` | `measurements` |
| `UserCredentials` | `webauthn_credentials` |

Усе інше в старій схемі (призначення ліків, шаблони нагадувань, звіти про прийоми тощо)
**навмисно ігнорується**: призначення не мають потрібних осей частоти й тривалості
для мапінгу, нагадування містять тестові дані, а історія прийомів не є критичною.

### Архітектура роботи
- **Читання (legacy)**: реалізовано напряму через `psycopg` (з фабрикою рядків `dict_row`)
  без використання SQLAlchemy на стороні джерела.
- **Запис (target)**: використовує `AsyncSession` з SQLModel для збереження моделей.
  Усі preflight-запити на цільовій базі робляться через `session.connection()`,
  щоб уникнути попереджень про застарілі методи (`DeprecationWarning`).
- **Безпека виводу**: парсить підключення через `urllib.parse.urlparse`,
  виводячи в таблицю префлайту лише хост, порт і базу без витоку паролів.
- **Контроль оператора**: перед записом обов'язково запитує підтвердження
  через `typer.confirm` (за винятком dry-run).

### Запуск

Скрипт повністю автономний — не залежить від `.env` чи `config.py`. CLI-інтерфейс побудовано
на `typer`. Запуск здійснюється за допомогою `uv`:

```bash
uv run python scripts/import_from_legacy.py \
    --legacy-url postgresql://user:pass@host:port/legacy_db \
    --target-url postgresql://user:pass@host:port/new_db \
    --user-id <uuid-користувача-в-новій-базі> \
    [--dry-run]
```

- `--legacy-url` — рядок підключення до legacy БД (джерело).
- `--target-url` — рядок підключення до нової БД (ціль).
- `--user-id` — UUID користувача в новій базі (валідується парсером як `UUID` автоматично).
- `--dry-run` — прочитати дані, показати таблицю результатів,
  **але нічого не записувати** в цільову базу.

### Ідемпотентність

Скрипт перевіряє наявність записів в `measurements` та `webauthn_credentials`
для `user_id` у цільовій базі. Якщо записи вже є, він зупиняє роботу
й виводить SQL-команди `DELETE` для ручного очищення. Скрипт ніколи
не видаляє дані й не перезаписує їх автоматично для уникнення дублів.
