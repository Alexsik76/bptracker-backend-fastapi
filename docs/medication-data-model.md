<!-- MAINTENANCE: agreed entity/table model for BP Tracker medication regimens.
     DESIGN doc (що зберігаємо і чому), не промпт агенту, не план робіт.
     Підсумок Етапу 0. Доповнює medication-dosage-model_2.md (там — 4 осі та FHIR-обґрунтування).
     Правки робити точково, статус відкритих питань тримати актуальним. -->
 
# BP Tracker — модель даних прийому ліків (погоджена)
 
Підсумок Етапу 0: погоджена структура сутностей/таблиць. Деталізація чотирьох осей дозування й обґрунтування через FHIR — у `medication-dosage-model_2.md`. Тут — **які таблиці, які колонки, як зв'язані**.
 
Реалізація: C#-класи + EF Core (`OwnsOne` для owned types), PostgreSQL 16. Зміни структури — через EF-міграції (`dotnet ef migrations add ...`), накочуються на старті.
 
---
 
## Ключовий принцип
 
**Проєкція — вперед, снапшот — назад.**
- Майбутні прийоми («сьогодні/найближче») **обчислюються** з призначення + годин нагадувань. Це не таблиця.
- Підтверджений факт прийому зберігає **власну копію** прийнятого і не залежить від поточного стану призначення.
Наслідок: **розклад похідний від призначення. Немає призначення — немає розкладу.**
 
ReminderTemplate (стара сутність, що дублювала ліки зі схеми) — **схлопнуто**. Від нього лишились тільки години слотів + параметри нагадувань, які тепер живуть у `reminder_config` як 1:1 дитина призначення.
 
---
 
## Таблиці
 
### users
| Колонка | Тип | Ключ | Примітка |
|---|---|---|---|
| id | uuid | PK | |
| email | string | | |
| timezone | string | | IANA; про людину/пристрій, не про рецепт |
 
### measurements
| Колонка | Тип | Ключ | Примітка |
|---|---|---|---|
| id | uuid | PK | |
| user_id | uuid | FK → users | |
| sys, dia, pulse | int | | діапазони: 40–300 / 20–200 / 30–250 |
| recorded_at | timestamptz | | |
| source | string | | local_ocr / user_confirmed / gemini тощо |
 
### prescriptions
Призначення (рецепт). Контейнер позицій. Максимум одне активне на користувача.
| Колонка | Тип | Ключ | Примітка |
|---|---|---|---|
| id | uuid | PK | |
| user_id | uuid | FK → users | |
| doctor | string | | |
| prescribed_on | date | | |
| is_active | bool | | інваріант «рівно одне активне» — per-user, атомарно |
| created_at | timestamptz | | |
 
### reminder_config
Години слотів + параметри нагадувань. **1:1 дитина призначення** — розклад живе тут.
Видалили призначення → каскадом пішов конфіг і весь розклад.
| Колонка | Тип | Ключ | Примітка |
|---|---|---|---|
| prescription_id | uuid | PK + FK → prescriptions | один рядок на призначення |
| morning_time | time | | напр. 08:00 |
| day_time | time | | напр. 14:00 |
| evening_time | time | | напр. 20:00 |
| max_reminders | int | | |
| duration_minutes | int | | вікно підтвердження |
 
### medication_items
Позиції ліків. **Окрема таблиця**, one-to-many до призначення через FK.
Один рядок = один препарат у рецепті.
| Колонка | Тип | Ключ | Примітка |
|---|---|---|---|
| id | uuid | PK | сутність із власним життям |
| prescription_id | uuid | FK → prescriptions | композиція (каскад) |
| medicine | string | | |
| condition | string | | напр. «після їжі» |
| when_slots | jsonb | | масив слотів, напр. `["Morning","Evening"]` |
| dose_amount | string | | **owned (dose)** — `"0.5"`, `"1"` |
| dose_unit | string? | | **owned (dose)** — опц.; якщо нема, одиниця в назві |
| freq_count | int | | **owned (frequency)** — скільки разів |
| freq_period | int | | **owned (frequency)** |
| freq_period_unit | string | | **owned (frequency)** — h / d / wk |
| course_type | string | | **owned (course)** — ongoing / course |
| course_start | timestamptz? | | **owned (course)** — для курсу |
| course_intakes | int? | | **owned (course)** — кількість прийомів (не календарні дні) |
 
> Колонки `dose_*`, `freq_*`, `course_*` — це EF owned types (`OwnsOne`): у C# окремі класи `Dose`/`Frequency`/`Course` для зручності, фізично — колонки рядка `medication_items`. Власного id/FK не мають, не існують без позиції. У `medication-dosage-model_2.md` це «чотири незалежні осі».
 
### intake_reports
Журнал прийомів. **Незмінний снапшот** — несе власну копію прийнятого.
| Колонка | Тип | Ключ | Примітка |
|---|---|---|---|
| id | uuid | PK | |
| user_id | uuid | FK → users | |
| prescription_id | uuid? | FK → prescriptions (nullable) | **лише трасування**, не для читання |
| period | slot | | Morning / Day / Evening |
| date | date | | з урахуванням таймзони клієнта |
| status | enum | | Confirmed / Missed |
| time | timestamptz | | коли підтверджено |
| snapshot | jsonb | | копія на момент confirm: `[{medicine, amount, condition}]` |
 
> Унікальність `(user_id, prescription_id?, period, date)` — захист від дублів; `ConfirmAsync` ідемпотентний.
> «Що прийнято» читається зі `snapshot`, **не** join'ом до поточного шаблону. Тому зміна/видалення призначення не псує історію.
 
---
 
## Зв'язки (по FK)
 
- `users` 1 — * `measurements`, `prescriptions`, `intake_reports` — через `user_id`
- `prescriptions` 1 — 1 `reminder_config` — через `prescription_id`; розклад похідний
- `prescriptions` 1 — * `medication_items` — через `prescription_id`; композиція (каскад)
- `prescriptions` 0..1 — * `intake_reports` — через `prescription_id` (nullable); лише трасування
---
 
## Що НЕ таблиця
 
**Розклад «сьогодні/найближче»** — обчислювана проєкція, не сутність:
 
```
проєкція = medication_items активного призначення
           × години з reminder_config
           (розгорнуті по слотах і, за потреби, по частоті/курсу)
```
 
Точка обчислення одна — на беку. Для офлайн-алярмів натива бек матеріалізує плаский список на горизонт (натив кешує в Room, ставить AlarmManager сам).
 
---
 
## Відкрите питання
 
- **Глибина `course` у проді.** Зберігати `course_start`/`course_intakes` одразу, чи лишити позицію поки тільки з `course_type=ongoing`, а решту додати окремою міграцією під перший реальний курсовий кейс? Постійний прийом (Бісопролол) двигуна розмотування курсу не потребує. Сховище дороге в зміні — двигун дешевий; схиляємось зберегти поля, реалізувати двигун пізніше.
## Наступна стадія (поза цим документом)
 
Порівняння з наявними таблицями: що мігрувати, як не зламати дані, які ендпоінти `/schemas`, `/reminders/template`, `/reminders/today`, `/reminders/confirm` міняти під нову модель.
