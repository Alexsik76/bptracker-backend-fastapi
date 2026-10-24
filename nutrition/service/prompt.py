import json

from nutrition.models import MenuStatus
from nutrition.service.context import GenerationContext

PROMPT_VERSION = "2026-10-24.1"


def build_generation_prompt(context: GenerationContext) -> str:
    """Construct prompt for Gemini menu generation according to domain rules."""
    profile = context.profile
    if profile is None:
        profile_data = {}
        meals_info = ["Сніданок", "Обід", "Вечеря"]
        restrictions_note = ""
    else:
        profile_data = {
            "sex": profile.sex.value if profile.sex else "unknown",
            "birth_year": profile.birth_year,
            "height_cm": profile.height_cm,
            "weight_kg": float(profile.weight_kg) if profile.weight_kg else None,
            "target_weight_kg": (
                float(profile.target_weight_kg) if profile.target_weight_kg else None
            ),
            "pace_kg_per_week": (
                float(profile.pace_kg_per_week) if profile.pace_kg_per_week else 0.5
            ),
            "restrictions": profile.restrictions,
            "dislikes": profile.dislikes,
        }
        meals_info = [f"{m.name} ({m.time})" if m.time else m.name for m in profile.meals] or [
            "Сніданок",
            "Обід",
            "Вечеря",
        ]
        restrictions_note = (
            f" Враховуй медичні обмеження: '{profile.restrictions}'."
            if profile.restrictions
            else ""
        )

    ready_dishes_data = [
        {
            "id": str(d.id),
            "name": d.name,
            "cooked_on": str(d.cooked_on) if d.cooked_on else None,
            "remaining_g": d.remaining_g,
            "cooked_weight_g": d.cooked_weight_g,
            "components": [c.name for c in d.components],
        }
        for d in context.ready_dishes
    ]

    constant_dishes_data = [
        {
            "id": str(d.id),
            "name": d.name,
            "fixed_slot": d.fixed_slot,
            "notes": d.notes,
            "components": [c.name for c in d.components],
        }
        for d in context.constant_dishes
    ]

    products_data = [
        {
            "id": str(p.id),
            "name": p.name,
            "preference": p.preference.value,
            "daily_limit_g": p.daily_limit_g,
            "eat_raw": p.eat_raw,
        }
        for p in context.products
    ]

    # Present existing meals for today
    existing_meals_data = []
    for menu in context.existing_day_menus:
        existing_meals_data.append(
            {
                "slot": menu.slot,
                "status": menu.status.value,
                "items": [
                    {
                        "title": it.title,
                        "dish_id": str(it.dish_id) if it.dish_id else None,
                        "product_id": str(it.product_id) if it.product_id else None,
                        "portion_g": it.portion_g,
                        "portion_text": it.portion_text,
                        "kcal_est": it.kcal_est,
                    }
                    for it in menu.items
                ],
            }
        )

    # History summary (only eaten meals as facts)
    history_summary = []
    for h in context.history_menus:
        if h.status == MenuStatus.EATEN:
            history_summary.append(
                {
                    "date": str(h.date),
                    "slot": h.slot,
                    "items": [
                        f"{i.title} ({i.portion_g}g)" if i.portion_g else i.title
                        for i in h.items
                    ],
                }
            )

    scope_instructions = f"Обсяг генерації: {context.scope}."
    if context.scope == "slot" and context.target_slot:
        scope_instructions += (
            f" Згенерувати або оновити пропозиції лише для прийому: '{context.target_slot}'."
        )
    elif context.scope == "day":
        scope_instructions += (
            " Скласти меню на весь день для всіх запланованих прийомів їжі. "
            "Прийоми їжі зі статусом 'eaten' чи 'skipped' є незмінними фактами — "
            "врахуй їх і не змінюй."
        )
    elif context.scope == "unmarked":
        scope_instructions += (
            " Перегенерувати лише непозначені прийоми їжі "
            "(ті, які ще не мають статусу 'eaten' чи 'skipped'). "
            "Прийоми їжі зі статусом 'eaten' чи 'skipped' є незмінними фактами — "
            "врахуй їх і не змінюй."
        )

    rules = (
        "1. Постійні страви із закріпленим прийомом (fixed_slot) не змінюються:\n"
        "   вони обов'язково включаються у свій прийом, калорійність враховується.\n"
        "2. Готові страви використовуються ПЕРШИМИ, у порядку свіжості (cooked_on),\n"
        "   з розподілом на кілька днів з урахуванням строку зберігання "
        "(риба 1–2 дні, м'ясо до 3 днів).\n"
        "   Сумарна порція готової страви НЕ повинна перевищувати її наявний залишок remaining_g.\n"
        "3. Додатково використовуються наявні продукти: спочатку бажані (preferred),\n"
        "   потім нейтральні (neutral); небажані (avoid) — лише якщо немає інших.\n"
        "4. Денні ліміти продуктів (daily_limit_g) НЕ перевищуються.\n"
        f"5. Помірна кількість жиру за один прийом.{restrictions_note}\n"
        "6. Обід і вечеря мають бути ситними та білковими. Запланувати вечірній перекус\n"
        "   (вікно, коли тягне на солодке).\n"
        "7. Добова ціль — з дефіцитом відносно підтримання для темпу 0,5–1 кг на тиждень.\n"
        "8. Кожна згенерована позиція містить рівно одне посилання: dish_id АБО product_id.\n"
        "   Назва (title) має відповідати назві обраної страви чи продукту.\n"
        "9. Якщо виникає дисбаланс раціону, додай це у shopping_list (рекомендації докупити)."
    )

    corrections_block = ""
    if context.corrections:
        corrections_list = "\n".join(f"- {c}" for c in context.corrections)
        corrections_block = (
            f"\nКОРИГУВАЛЬНІ УТОЧНЕННЯ ДНЯ (СУВОРО ВРАХУВАТИ):\n{corrections_list}\n"
        )

    off_plan_block = ""
    if context.off_plan_entries:
        off_plan_data = [
            {
                "text": o.text,
                "portion_g": o.portion_g,
                "kcal_est": o.kcal_est,
            }
            for o in context.off_plan_entries
        ]
        off_plan_block = (
            "\nПОЗАПЛАНОВЕ СПОЖИВАННЯ СЬОГОДНІ (врахувати у добовому балансі калорій):\n"
            f"{json.dumps(off_plan_data, ensure_ascii=False, indent=2)}\n"
        )

    prompt = f"""Ти — професійний дієтолог і помічник зі складання щоденного меню.

ПРАВИЛА ГЕНЕРАТОРА (СУВОРО ДОТРИМУВАТИСЯ):
{rules}
{corrections_block}
ДАТА МЕНЮ: {context.date}

ПРОФІЛЬ КОРИСТУВАЧА:
{json.dumps(profile_data, ensure_ascii=False, indent=2)}

ПРИЙОМИ ЇЖІ ЗА РОЗКЛАДОМ:
{", ".join(meals_info)}

НАЯВНІ ГОТОВІ СТРАВИ (використати в першу чергу; remaining_g — поточний залишок
з урахуванням уже з'їденого, сума порцій у НЕз'їдених прийомах не має перевищувати remaining_g):
{json.dumps(ready_dishes_data, ensure_ascii=False, indent=2)}

ПОСТІЙНІ СТРАВИ:
{json.dumps(constant_dishes_data, ensure_ascii=False, indent=2)}

НАЯВНІ ПРОДУКТИ:
{json.dumps(products_data, ensure_ascii=False, indent=2)}

МЕНЮ ПОТОЧНОГО ДНЯ:
{json.dumps(existing_meals_data, ensure_ascii=False, indent=2)}
{off_plan_block}
ФАКТИЧНЕ СПОЖИВАННЯ ПОПЕРЕДНІХ ДНІВ:
{json.dumps(history_summary, ensure_ascii=False, indent=2)}

ЗАВДАННЯ:
{scope_instructions}

Сформуй збалансоване меню та поверни JSON, що містить:
- rationale: коротке зрозуміле пояснення вибору та калорійності;
- shopping_list: список продуктів, які варто докупити (якщо все є, пустий список);
- items: масив позицій меню (slot — назва прийому їжі без часу, dish_id, product_id,
  title, portion_g, portion_text, kcal_est, position). Generating items for meals already
  marked 'eaten' or 'skipped' is not required.
"""
    return prompt
