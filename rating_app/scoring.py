from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CriterionSeed:
    code: str
    section: str
    name: str
    rate: float
    cap: float | None
    unit: str


POSITIVE = "Положительные"
NEGATIVE = "Отрицательные"


CRITERIA: tuple[CriterionSeed, ...] = (
    CriterionSeed("edu_technical", POSITIVE, "Техническое, профессиональное и послесреднее образование", .5, .5, "наличие"),
    CriterionSeed("edu_higher", POSITIVE, "Высшее образование", 1, 1, "наличие"),
    CriterionSeed("edu_master", POSITIVE, "Магистратура", .5, .5, "наличие"),
    CriterionSeed("edu_science", POSITIVE, "Учёная степень", .5, 1, "степень"),
    CriterionSeed("edu_oper_tactical", POSITIVE, "Послевузовское образование оперативно-тактического уровня", 1, 1, "наличие"),
    CriterionSeed("edu_strategic", POSITIVE, "Послевузовское образование оперативно-стратегического/стратегического уровня", 1.5, 1.5, "наличие"),
    CriterionSeed("edu_honors", POSITIVE, "Окончание полного курса обучения с отличием", .25, .75, "программа"),
    CriterionSeed("training_certificate", POSITIVE, "Профильные сертификаты/удостоверения за последние 5 лет", .1, .2, "сертификат"),
    CriterionSeed("academic_courses", POSITIVE, "Высшие академические курсы при военных учебных заведениях", .25, .25, "наличие"),
    CriterionSeed("state_language", POSITIVE, "Государственный язык: B2=1, C1=2, C2=3", .25, .75, "уровень"),
    CriterionSeed("foreign_language", POSITIVE, "Иностранные языки (свободное владение)", .25, 8, "язык"),
    CriterionSeed("service_political", POSITIVE, "Стаж на политических должностях", .5, None, "год"),
    CriterionSeed("service_strategic", POSITIVE, "Стаж в стратегическом органе", .4, None, "год"),
    CriterionSeed("service_oper_strategic", POSITIVE, "Стаж в оперативно-стратегическом органе", .3, None, "год"),
    CriterionSeed("service_oper_tactical", POSITIVE, "Стаж в оперативно-территориальном/оперативно-тактическом органе", .2, None, "год"),
    CriterionSeed("service_tactical", POSITIVE, "Стаж в тактическом органе", .1, None, "год"),
    CriterionSeed("leader_strategic", POSITIVE, "Руководящий стаж в стратегическом органе", .35, None, "год"),
    CriterionSeed("leader_oper_strategic", POSITIVE, "Руководящий стаж в оперативно-стратегическом органе", .3, None, "год"),
    CriterionSeed("leader_oper_tactical", POSITIVE, "Руководящий стаж в оперативно-тактическом органе", .25, None, "год"),
    CriterionSeed("leader_tactical", POSITIVE, "Руководящий стаж в тактическом органе", .2, None, "год"),
    CriterionSeed("civilian_service", POSITIVE, "Стаж вне государственной службы", .1, None, "год"),
    CriterionSeed("civilian_leader", POSITIVE, "Руководящий стаж вне государственной службы", .2, None, "год"),
    CriterionSeed("career_direction", POSITIVE, "Непрерывный стаж на следующей нижестоящей должности", .25, None, "год"),
    CriterionSeed("attestation_promotion", POSITIVE, "Аттестация: соответствует и рекомендуется к выдвижению", 1, 1, "наличие"),
    CriterionSeed("attestation_match", POSITIVE, "Аттестация: соответствует занимаемой должности", .5, .5, "наличие"),
    CriterionSeed("state_award", POSITIVE, "Государственные награды", 1, 8, "награда"),
    CriterionSeed("department_award", POSITIVE, "Ведомственные профильные награды", .5, 1, "награда"),
    CriterionSeed("early_rank", POSITIVE, "Досрочно присвоенные воинские звания", .5, 1, "звание"),
    CriterionSeed("achievement", POSITIVE, "Реализованные профессиональные проекты", .5, 1, "достижение"),
    CriterionSeed("emergency", POSITIVE, "Участие в ликвидации ЧС/обеспечении режима ЧП", .25, .25, "наличие"),
    CriterionSeed("peacekeeping", POSITIVE, "Участие в миротворческих операциях", .5, .5, "наличие"),
    CriterionSeed("combat", POSITIVE, "Участие в боевых/антитеррористических операциях", 1, 1, "наличие"),
    CriterionSeed("social_achievement", POSITIVE, "Призовые места не ниже республиканского уровня", .25, .5, "место"),
    CriterionSeed("leadership_recognition", POSITIVE, "Грамоты, благодарности и международные знаки отличия", .5, 1, "награда"),
    CriterionSeed("discipline", NEGATIVE, "Дисциплинарные взыскания за последние 12 месяцев", -.25, -1, "взыскание"),
    CriterionSeed("rank_reduction", NEGATIVE, "Снижение в воинском звании за весь период службы", -1, -1, "наличие"),
    CriterionSeed("attestation_negative", NEGATIVE, "Аттестация: не соответствует, рекомендуется к понижению", -.5, -.5, "наличие"),
    CriterionSeed("dismissal_negative", NEGATIVE, "Увольнение по служебному несоответствию/отрицательным мотивам", -1, -1, "наличие"),
    CriterionSeed("administrative_offense", NEGATIVE, "Административные взыскания за последние 12 месяцев", -.5, -1.5, "взыскание"),
    CriterionSeed("criminal_offense", NEGATIVE, "Уголовное правонарушение", -2, -2, "наличие"),
)


def calculate(rate: float, quantity: float, cap: float | None) -> float:
    points = rate * max(0.0, quantity)
    if cap is not None:
        points = min(points, cap) if rate >= 0 else max(points, cap)
    return round(points, 2)
