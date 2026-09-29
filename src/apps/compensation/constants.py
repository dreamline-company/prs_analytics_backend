"""Коды контура компенсации: статусы пар, причины закрытия, риск доноров."""

# Статус пары «потеря → донор». Согласование пока без эндпоинта: все пары
# «на согласовании».
RECOMMENDATION_PENDING = "pending"
RECOMMENDATION_ACCEPTED = "accepted"
RECOMMENDATION_REJECTED = "rejected"
RECOMMENDATION_APPLIED = "applied"
# Статус донора без открытой пары.
DONOR_RESERVE = "reserve"

# Почему пара закрыта.
CLOSE_LOSS_RESOLVED = "loss_resolved"  # скважина с потерей запустилась
CLOSE_DONOR_UNAVAILABLE = "donor_unavailable"  # донор встал или в аварии
CLOSE_REJECTED = "rejected"  # технолог отклонил

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# Мнемокод статуса ABAI «В простое» (wells_well_status_type.code).
ABAI_STATUS_IDLE = "DWN"
# План Qн: техрежим месячный и приезжает с лагом — как в сводке НГДУ.
PLAN_GRACE_DAYS = 31

# Почему скважина стоит.
STOP_IDLE = "idle"  # простой в ABAI
STOP_REPAIR = "repair"  # незавершённый ремонт

# Единицы скорости привода по типу насоса (как на макете).
SPEED_UNITS = {"ШГН": "кач/мин", "ЭВН": "об/мин"}

# Состояние скважины в контуре.
CONTOUR_STOPPED = "stopped"  # стоит, есть план — потеря считается
CONTOUR_NO_PLAN = "no_plan"  # стоит, плана по нефти нет — потерю не оценить
CONTOUR_WORKING = "working"  # не стоит или вне контура
