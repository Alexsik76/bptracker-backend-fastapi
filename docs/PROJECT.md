
FastAPI-BPTracker



How can I help you today?


BP Tracker: Alembic і перша міграція
Last message just now
Налаштування FastAPI проекту з PostgreSQL
Last message 7 hours ago
Memory
Only you
Purpose & context Alex is rebuilding an existing C# backend (bptracker-backend, GitHub: Alexsik76) as a Python/FastAPI project (bptracker-backend-fastapi) — primarily as a structured learning exercise in modern Python backend development. The goal is to replicate the same domain logic while adopting best practices and modern libraries, not to explore low-level fundamentals. Alex explicitly frames Claude's role as teacher/guide in this work. Sessions are conducted in Ukrainian. Current state Full infrastructure scaffolding is complete: Containerization: Docker Compose with Postgres 18 (Alex correctly challenged an initial Claude suggestion of Postgres 16) Package management: uv with pyproject.toml as source of truth and uv.lock for reproducibility; UVCACHEDIR relocated from C: to D: via setx on Windows Stack: fastapi[standard], ruff (dev), SQLModel + SQLAlchemy 2.0 async + psycopg3 + Alembic Primary keys: uuidv7() server-side via Postgres 18's native function Config: .env uses five discrete fields (POSTGRESUSER, POSTGRESPASSWORD, POSTGRESDB, POSTGRESHOST, POSTGRESPORT); URL construction deferred to a config module VS Code: .vscode/ placement corrected mid-session measurements module: SQLModel multi-class inheritance pattern (MeasurementBase / Measurement(table=True) / MeasurementCreate / MeasurementRead), validated against C# source via GitHub API; source field intentionally omitted (not stored in C# measurements table) On the horizon Alembic configuration — this is the immediate next step, left pending at session end Key learnings & principles Alex actively and successfully challenges Claude's reasoning; Claude should acknowledge when prior reasoning was flawed rather than defending it (established over the Postgres version, DATABASEURL duplication, and uuidv7() framing episodes) Decisions from the C# codebase are treated as the canonical reference — the GitHub API was used mid-session to validate model design against actual source Approach & patterns Alex prefers Claude to make decisions with clear rationale rather than presenting open-ended choices; questions should only be asked when genuinely uncertain Responses should be concise and non-repetitive — re-explaining already-covered content has been corrected multiple times Code and config comments must always be in English, never Ukrainian — Alex had to redo work due to Ukrainian comments; this is a hard requirement. Conversational prose can remain Ukrainian. Tools & resources Languages/runtime: Python (uv), Docker Compose Framework: FastAPI, SQLModel, SQLAlchemy 2.0 async, psycopg3, Alembic Database: Postgres 18 Dev tooling: Ruff, VS Code Reference codebase: bptracker-backend (C#, GitHub: Alexsik76)

Last updated 17 hours ago

Instructions
Add instructions to tailor Claude’s responses

Files
6% of project capacity used
Search mode

Alexsik76/bptracker-backend-fastapi
master

GITHUB



PROJECT.md
79 lines

md



medication-data-model.md
136 lines

md



PROJECT.md


<!-- MAINTENANCE: вступний документ нового беку BP Tracker (FastAPI).
     Фіксує прийняті рішення та межі. Не план робіт, не код.
     Правки робити точково; рішення, що змінились, переписувати в місці, а не дописувати знизу. -->
 
# BP Tracker — новий бекенд (FastAPI)
 
Чистий старт. Новий репозиторій, новий бек на Python/FastAPI. Поточний C#-бек **заморожено** в робочому стані (прод живе як є). З попереднього проєкту переноситься лише цей документ і `medication-data-model.md`.
 
---
 
## Навіщо переписуємо
 
Не тому, що C# гірший. Тому, що автор вільно читає Python і ледве — C#. Код, який автор не може ревізувати на швидкості розробки, — це борг, незалежно від того, що він «працює». Мета нового проєкту: автор контролює кожен рядок і розуміє кожне рішення.
 
Головна провалена ціль попереднього етапу — темп проти розуміння. Це пастка мови-незалежна: вона повториться й на Python, якщо знову гнати на результат. Тому головне правило нижче.
 
---
 
## Правила розробки (це і є зміна підходу)
 
1. **Жодного рядка швидше, ніж автор його розуміє.** Темп задає розуміння, не швидкість видачі. Краще один зрозумілий модуль за тиждень, ніж десять незрозумілих за день.
2. **Бюджет залежностей вголос.** Кожна зовнішня бібліотека — з явним обґрунтуванням проти стандартної бібліотеки. «Заради одного парсингу в одному місці» — за замовчуванням ні. Автор має право вето, навіть на інтуїції.
3. **Межі модулів фізичні.** Один модуль не імпортує нутрощі іншого. Самодостатні застосунки — кожен зі своїми моделями, схемами, роутером.
---
 
## Архітектура
 
Модульна: окремі самодостатні застосунки, що підключаються одним рядком (підхід у дусі Django apps, реалізований руками на FastAPI — свобода структури, яку автор хоче проєктувати сам).
 
Модулі першої черги:
- **measurements** — заміри тиску. Чистий CRUD. Стартовий модуль.
- **prescriptions** — призначення (рецепти) + позиції ліків.
- **reminders** — розклад/нагадування (похідне від призначення).
Кожен модуль: власні моделі, Pydantic-схеми, роутер, доступ до БД. Спільне (підключення до БД, конфіг) — окремо, не всередині модулів.
 
Домен — за специфікацією `medication-data-model.md`. **Це специфікація домену, не готова структура таблиць для копіювання.** Перекладаємо її в чисті модульні моделі, не переносячи дослівно.
 
Ключовий доменний принцип (зі специфікації): **проєкція — вперед** (майбутні прийоми обчислюються з призначення), **снапшот — назад** (підтверджений факт зберігає власну копію). Розклад похідний від призначення: немає призначення — немає розкладу.
 
---
 
## Що НЕ робимо зараз
 
Накопичуємо в дев-гілках / документації, не в проді:
- автентифікація складніше за мінімум (passkey/WebAuthn, magic-link);
- авторизація, ролі, адмінка;
- push-нагадування, OCR-проксі;
- частота (`frequency`) і курс (`course`) лікування — даних під них поки нема.
Старт — чистий CRUD на трьох модулях.
 
### Закласти заздалегідь (передбачити, не реалізувати)
 
- **Ізоляція даних per-user.** Моделі від початку несуть `user_id`. Поки користувач один (захардкоджений, як старий dev-сід), але поле є з першого дня.
- **Ролі як окрема вісь.** Поява `user` / `admin` і адмінки не повинна вимагати чіпати доменні моделі. Лишаємо для ролей місце, самі ролі не пишемо. Деталі — коли дійдемо до auth-модуля.
---
 
## Середовище розробки
 
**База в контейнері, код у venv локально.** Свідомий вибір під мету (вчитися, читати, контролювати):
- код у `venv` → миттєвий перезапуск, дебагер напряму, жодного шару Docker між автором і помилкою;
- Postgres у `docker compose` (один сервіс) → чиста БД однією командою, легко знести й підняти заново;
- це найкоротша петля «змінив → побачив», потрібна, коли питаєш за кожен рядок.
Повна контейнеризація (код теж у Docker) — **відкладено** до хмарного деплою. Тоді Dockerfile напишемо свідомо. Зараз вона лише додала б шар, що ховає те, що автор хоче бачити.
 
Старт: новий порожній Postgres у контейнері, FastAPI у venv, перший модуль `measurements`.
 
---
 
## Зв'язок зі старим проєктом
 
- C#-бек: заморожено, працює, не розвиваємо.
- Мобільний клієнт: уповільнюємо розробку, щоб менше переробляти під нову БД та API.
- Переноситься в новий репозиторій: цей документ + `medication-data-model.md`. Решта старого коду — як довідка/специфікація (контракти, edge-cases auth), не для копіювання.
 
