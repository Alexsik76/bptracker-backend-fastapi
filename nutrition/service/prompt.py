import json

from nutrition.models import MEAL_TYPE_LABELS, MealType, MenuStatus, NutritionSource
from nutrition.service.context import GenerationContext

PROMPT_VERSION = "2026-10-28.2"


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
        if context.existing_day_menus:
            meals_info = [
                f"{MEAL_TYPE_LABELS.get(m.meal_type, m.meal_type.value)} ({m.time})"
                if m.time
                else MEAL_TYPE_LABELS.get(m.meal_type, m.meal_type.value)
                for m in context.existing_day_menus
            ]
        else:
            meals_info = [
                f"{MEAL_TYPE_LABELS.get(m.type, m.type.value)} ({m.time})"
                if m.time
                else MEAL_TYPE_LABELS.get(m.type, m.type.value)
                for m in profile.meals
            ] or [
                "Сніданок",
                "Обід",
                "Вечеря",
            ]
        restrictions_note = (
            f" Враховуй медичні обмеження: '{profile.restrictions}'."
            if profile.restrictions
            else ""
        )

    products_map = {p.id: p.name for p in context.products}

    def _comp_str(c) -> str:
        name = products_map.get(c.product_id)
        return f"{name} ({c.weight_g} г)" if name else f"{c.weight_g} г"

    dishes_data = [
        {
            "id": str(d.id),
            "name": d.name,
            "cooked_on": str(d.cooked_on) if d.cooked_on else None,
            "remaining_g": d.remaining_g,
            "cooked_weight_g": d.cooked_weight_g,
            "components": [_comp_str(c) for c in d.components],
        }
        for d in context.dishes
    ]

    products_data = [
        {
            "id": str(p.id),
            "name": p.name,
            "preference": p.preference.value,
            "stock_g": p.stock_g,
        }
        for p in context.products
    ]

    # Present existing meals for today (pinned meals passed as facts)
    existing_meals_data = []
    for menu in context.existing_day_menus:
        existing_meals_data.append(
            {
                "meal_type": menu.meal_type.value,
                "status": menu.status.value,
                "pinned": menu.pinned,
                "items": [
                    {
                        "title": it.title,
                        "dish_id": str(it.dish_id) if it.dish_id else None,
                        "portion_g": it.portion_g,
                        "portion_text": it.portion_text,
                        "kcal": (
                            "not calculated"
                            if it.kcal is None
                            else (
                                f"{it.kcal} (approximate estimate)"
                                if it.nutrition_source == NutritionSource.MODEL
                                else it.kcal
                            )
                        ),
                        "protein_g": (
                            (
                                f"{it.protein_g} (approximate estimate)"
                                if it.nutrition_source == NutritionSource.MODEL
                                else it.protein_g
                            )
                            if it.protein_g is not None
                            else None
                        ),
                        "fat_g": (
                            (
                                f"{it.fat_g} (approximate estimate)"
                                if it.nutrition_source == NutritionSource.MODEL
                                else it.fat_g
                            )
                            if it.fat_g is not None
                            else None
                        ),
                        "carbs_g": (
                            (
                                f"{it.carbs_g} (approximate estimate)"
                                if it.nutrition_source == NutritionSource.MODEL
                                else it.carbs_g
                            )
                            if it.carbs_g is not None
                            else None
                        ),
                        "nutrition_source": (
                            "approximate estimate"
                            if it.nutrition_source == NutritionSource.MODEL
                            else (it.nutrition_source.value if it.nutrition_source else None)
                        ),
                    }
                    for it in menu.items
                ],
            }
        )

    # History summary (only eaten meals as facts)
    history_summary = []
    for h in context.history_menus:
        if (h.status == MenuStatus.EATEN or h.eaten_at is not None) and h.items:
            history_summary.append(
                {
                    "date": str(h.date),
                    "meal_type": h.meal_type.value,
                    "items": [
                        f"{i.title} ({i.portion_g}g)" if i.portion_g else i.title
                        for i in h.items
                    ],
                }
            )

    scope_instructions = f"Обсяг генерації: {context.scope}."
    if context.scope == "slot" and context.target_meal_type:
        label = MEAL_TYPE_LABELS.get(context.target_meal_type, context.target_meal_type.value)
        scope_instructions += (
            f" Згенерувати або оновити пропозиції лише для прийому: "
            f"'{context.target_meal_type.value}' ({label}). "
            "Замінити всі позиції цього прийому "
            "(якщо він незакріплений, не з'їдений і не пропущений). "
            "Усі інші прийоми їжі цього дня є вже сформованими — "
            "врахуй їх як фіксований контекст і не генеруй для них нових позицій. "
            "Прийоми зі статусом 'eaten', а також закріплені прийоми (pinned) "
            "є незмінними фактами з їхньою поживністю — не змінюй їх. "
            "Пропущені прийоми (missed) вважаються не з'їденими; не генеруй для них позицій. "
            "Якщо решта дня не вміщує добову ціль, у rationale можна порадити додати прийом їжі."
        )
    elif context.scope in ("day", "unmarked"):
        scope_instructions += (
            " Скласти меню на день для всіх доступних прийомів їжі на канвасі. "
            "Позиції незакріплених, не з'їдених та не пропущених прийомів замінюються повністю. "
            "Прийоми зі статусом 'eaten', а також закріплені прийоми їжі (pinned) "
            "є вже сформованими незмінними фактами з їхньою поживністю — врахуй їх і не змінюй. "
            "Пропущені прийоми (missed) вважаються не з'їденими; не генеруй для них позицій. "
            "Якщо решта дня не вміщує добову ціль, у rationale можна порадити додати прийом їжі."
        )

    has_evening_snack = any(
        m.type == MealType.EVENING_SNACK
        for m in (profile.meals if profile else [])
    )
    if has_evening_snack:
        rule_4 = (
            "4. Обід і вечеря мають бути ситними та білковими. Запланувати вечірній перекус\n"
            "   (вікно, коли тягне на солодке), оскільки такий прийом є в розкладі профілю.\n"
        )
    else:
        rule_4 = (
            "4. Обід і вечеря мають бути ситними та білковими. Запланувати вечірній перекус\n"
            "   лише якщо такий прийом їжі є в розкладі профілю (наразі відсутній).\n"
        )

    rules = (
        "1. Страви використовуються ПЕРШИМИ, у порядку свіжості (cooked_on),\n"
        "   з розподілом на кілька днів з урахуванням строку зберігання "
        "(риба 1–2 дні, м'ясо до 3 днів).\n"
        "   Сумарна порція страви НЕ повинна перевищувати її наявний залишок remaining_g.\n"
        "2. Модель розподіляє тільки наявні страви і ніколи не пропонує нових страв чи продуктів.\n"
        f"3. Помірна кількість жиру за один прийом.{restrictions_note}\n"
        f"{rule_4}"
        "5. Добова ціль — з дефіцитом відносно підтримання для темпу 0,5–1 кг на тиждень.\n"
        "6. Кожна згенерована позиція містить посилання на наявну страву (dish_id).\n"
        "   Назва (title) має відповідати назві обраної страви.\n"
        "7. Якщо виникає дисбаланс раціону, додай це у shopping_list (рекомендації докупити).\n"
        "8. Пропущені прийоми (missed) не з'їдені; для них страви не плануються.\n"
        "    Якщо решта дня не вміщує добову ціль, у rationale можна порадити додати прийом їжі."
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
                "at": o.at.isoformat() if hasattr(o.at, "isoformat") else str(o.at),
                "text": o.text,
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

НАЯВНІ СТРАВИ (використати в першу чергу; remaining_g — поточний залишок
з урахуванням уже запланованого, сума порцій не має перевищувати remaining_g):
{json.dumps(dishes_data, ensure_ascii=False, indent=2)}

НАЯВНІ ПРОДУКТИ (для shopping_list):
{json.dumps(products_data, ensure_ascii=False, indent=2)}

МЕНЮ ПОТОЧНОГО ДНЯ:
{json.dumps(existing_meals_data, ensure_ascii=False, indent=2)}
{off_plan_block}
ФАКТИЧНЕ СПОЖИВАННЯ ПОПЕРЕДНІХ ДНІВ:
{json.dumps(history_summary, ensure_ascii=False, indent=2)}

ЗАВДАННЯ:
{scope_instructions}

Сформуй збалансоване меню та поверни JSON, що містить:
- rationale: коротке зрозуміле пояснення вибору та калорійності
  (може радити додати прийом їжі, якщо решта дня не вміщує ціль);
- shopping_list: список продуктів, які варто докупити (якщо все є, пустий список);
- items: масив позицій меню (meal_type — тип прийому їжі:
  breakfast, lunch, dinner, morning_snack, afternoon_snack, evening_snack;
  dish_id, title, portion_g, portion_text, position).
  Модель розподіляє лише наявні страви;
  не пропонує нових страв чи продуктів.
  Generating items for meals already marked 'eaten', pinned, or missed is not required.
"""
    return prompt
