"""Portable attendance inputs; no persistence or provider dependencies."""
from datetime import time
from enum import StrEnum
from pydantic import BaseModel, Field, field_validator, model_validator


class AttendanceStatus(StrEnum):
    PRESENT = "P"
    LATE = "LT"
    EARLY_EXIT = "EE"
    PAID_PERMISSION = "PP"
    OFFICIAL_DUTY = "OD"
    WORK_FROM_HOME = "WFH"
    PAID_LEAVE = "PL"
    UNPAID_LEAVE = "UL"
    ABSENT = "A"
    MISSING_PUNCH = "MP"
    WEEKLY_OFF = "WO"
    HOLIDAY = "H"


class Shift(BaseModel):
    start: time = time(10, 0)
    end: time = time(19, 0)
    break_minutes: int = Field(default=60, ge=0, le=480)

    @field_validator("end")
    @classmethod
    def distinct(cls, value, info):
        if info.data.get("start") == value:
            raise ValueError("The shift start and end times cannot be the same.")
        return value

    @model_validator(mode="after")
    def valid_break(self):
        span = ((self.end.hour * 60 + self.end.minute) - (self.start.hour * 60 + self.start.minute)) % 1440
        if self.break_minutes >= span:
            raise ValueError("The break must be shorter than the shift.")
        return self
