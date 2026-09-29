from enum import IntEnum


class AbaiNGDUIDsEnum(IntEnum):
    DMG = 9
    ZHlMG = 10
    ZHMG = 11
    KMG = 12


NGDU_ORG_TYPE = 10

# Кайнармунайгаз в матрице инцидентов и сводке НГДУ — только скважины этого
# месторождения (решение владельца).
KMG_ONLY_OIL_FIELD_PREFIX = "VMB"
