from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from .decor import DECORATION_DOT, SAKURA_3


class SpecialDayType(StrEnum):
    HOLIDAY = "holiday"
    SHORTENED = "shortened"
    WORKING_WEEKEND = "working_weekend"


@dataclass(frozen=True, slots=True)
class SpecialDay:
    work_date: date
    day_type: SpecialDayType


@dataclass(frozen=True, slots=True)
class SpecialDayChange:
    work_date: date
    day_type: SpecialDayType | None


_TYPE_ALIASES = {
    "праздник": SpecialDayType.HOLIDAY,
    "выходной": SpecialDayType.HOLIDAY,
    "нерабочий": SpecialDayType.HOLIDAY,
    "нерабочий день": SpecialDayType.HOLIDAY,
    "сокращенный": SpecialDayType.SHORTENED,
    "сокращенный день": SpecialDayType.SHORTENED,
    "сокр": SpecialDayType.SHORTENED,
    "рабочий": SpecialDayType.WORKING_WEEKEND,
    "рабочий выходной": SpecialDayType.WORKING_WEEKEND,
    "рабочий день": SpecialDayType.WORKING_WEEKEND,
    "рабочий день в выходной": SpecialDayType.WORKING_WEEKEND,
}
_DELETE_ALIASES = {"удалить", "убрать", "очистить"}
_WEEKDAYS = ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")


def parse_special_day_changes(value: str) -> tuple[SpecialDayChange, ...]:
    changes_by_date: dict[date, SpecialDayChange] = {}
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(r"(\d{1,2}\.\d{1,2}\.\d{4})\s+(.+)", line)
        if match is None:
            raise ValueError(
                f"Строка {line_number}: используйте формат ДД.ММ.ГГГГ тип"
            )
        try:
            work_date = datetime.strptime(match.group(1), "%d.%m.%Y").date()
        except ValueError as exc:
            raise ValueError(f"Строка {line_number}: такой даты не существует") from exc

        raw_type = re.sub(r"[\s_-]+", " ", match.group(2).strip().lower()).replace(
            "ё", "е"
        )
        if raw_type in _DELETE_ALIASES:
            day_type = None
        else:
            day_type = _TYPE_ALIASES.get(raw_type)
            if day_type is None:
                raise ValueError(
                    f"Строка {line_number}: неизвестный тип «{match.group(2).strip()}»"
                )
        changes_by_date[work_date] = SpecialDayChange(work_date, day_type)

    if not changes_by_date:
        raise ValueError("Не найдено ни одной даты")
    return tuple(changes_by_date.values())


def format_special_days(days: list[SpecialDay] | tuple[SpecialDay, ...]) -> str:
    title = f"{SAKURA_3} <b>Особенные рабочие дни</b>"
    if not days:
        return title + "\n\nПока ни одной даты не отмечено."

    grouped = {
        SpecialDayType.HOLIDAY: [],
        SpecialDayType.SHORTENED: [],
        SpecialDayType.WORKING_WEEKEND: [],
    }
    for special_day in sorted(days, key=lambda item: item.work_date):
        grouped[special_day.day_type].append(_format_day(special_day.work_date))

    sections = [title]
    labels = (
        (SpecialDayType.HOLIDAY, "Праздники · нерабочие дни"),
        (SpecialDayType.SHORTENED, "Сокращённые рабочие дни"),
        (SpecialDayType.WORKING_WEEKEND, "Рабочие дни в выходные"),
    )
    for day_type, label in labels:
        sections.append(f"\n<b>{label}</b>")
        values = grouped[day_type]
        sections.extend(values or ["—"])
    return "\n".join(sections)


def _format_day(value: date) -> str:
    return (
        f"{DECORATION_DOT} {_WEEKDAYS[value.weekday()]} • "
        f"<code>{value.strftime('%d.%m.%Y')}</code>"
    )
