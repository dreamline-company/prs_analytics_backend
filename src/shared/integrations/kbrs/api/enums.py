from __future__ import annotations

from enum import Enum


class TreeGroupType(str, Enum):
    DEVICES = "mtgtDevices"


class MeasureDateCondition(str, Enum):
    NONE = "mlcdNone"
    NOW = "mlcdNow"
    YESTERDAY = "mlcdYesterday"
    WEEK = "mlcdWeek"
    MONTH = "mlcdMonth"
    THREE_MONTHS = "mlcdThreeMonths"
    HALF_YEAR = "mlcdHalfYear"
    YEAR = "mlcdYear"
    INTERVAL = "mlcdInterval"


class MeasureConditionType(str, Enum):
    ACTUAL = "mlctActual"


class MeasureOperation(str, Enum):
    NONE = "moNone"


class MeasureDirection(str, Enum):
    NONE = "mlvtNone"


class MeasurementChannel(int, Enum):
    HOOK_WEIGHT = 0x0200
    H2S = 0x137E
    CH4 = 0x1397
