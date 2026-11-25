"""Handle explicit personal energy-target questions without model arithmetic."""

import re

from app.data_access.users import UserProfile

_ENERGY = re.compile(r"kalori|kcal|calori|enerji|energy", re.IGNORECASE)
_PERSONAL_TARGET = re.compile(
    r"hedef|ihtiya|gereksinim|almalı|almam|yemeli|tüketmeli|günlük|günde|"
    r"daily|target|need|should i|maintenance|maintain|goal",
    re.IGNORECASE,
)


def is_energy_target_question(question: str) -> bool:
    return bool(_ENERGY.search(question) and _PERSONAL_TARGET.search(question))


def energy_answer(user: UserProfile, question: str) -> str:
    turkish = bool(re.search(r"kalori|enerji|gün|hedef|ihtiya|almalı|yemeli", question, re.IGNORECASE))
    if user.age is not None and user.age < 18:
        return (
            "18 yaş altındakiler için kalori hedefi hesaplamıyorum; bir sağlık uzmanına danış."
            if turkish else
            "I can't set a calorie target for someone under 18; consult a health professional."
        )
    if re.search(
        r"hamile|gebey|gebelik|emzir|yeme bozuk|anoreksi|bulimi|diyabet|böbrek|"
        r"pregnan|breastfeed|eating disorder|anorexi|bulimi|diabet|kidney",
        question, re.IGNORECASE,
    ):
        return (
            "Bu durumda kişisel enerji hedefi için doktoruna veya diyetisyenine danış."
            if turkish else
            "For a personal energy target in this situation, consult your doctor or dietitian."
        )
    missing = [label for value, label in ((user.age, "yaş"), (user.height_cm, "boy"), (user.weight_kg, "kilo")) if value is None]
    if turkish:
        profile = f"Önce profilinde şu bilgileri tamamla: {', '.join(missing)}. " if missing else ""
        return profile + (
            "Bakım kalorisi için Plan sayfasında denklem ve aktivite düzeyini seçip güvenlik koşullarını onayla; "
            "tahmini orada görebilirsin. Gebelik, emzirme, yeme bozukluğu veya özel sağlık durumlarında uzmana danış."
        )
    missing_en = [label for value, label in ((user.age, "age"), (user.height_cm, "height"), (user.weight_kg, "weight")) if value is None]
    profile = f"First complete your profile: {', '.join(missing_en)}. " if missing_en else ""
    return profile + (
        "For a maintenance estimate, choose the equation and activity level and confirm the safety conditions "
        "on the Plan page. Consult a professional for pregnancy, breastfeeding, eating disorders or medical conditions."
    )
