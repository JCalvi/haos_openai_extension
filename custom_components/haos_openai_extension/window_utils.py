"""Resolve user-friendly history windows using the Home Assistant local timezone.

All returned datetimes are aware. Date boundaries are constructed in local
calendar time to handle daylight-saving transitions correctly.
"""

from datetime import date, datetime, time, timedelta, timezone


def _day_at(day: date, hour: int, tzinfo: object) -> datetime:
    """Construct a local wall-clock time for the specified day."""
    return datetime.combine(day, time(hour=hour), tzinfo=tzinfo)


def _parse_time(value: str, local_tz: object) -> datetime:
    """Accept ISO 8601 with an offset, or local HA wall time without one."""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "start_time/end_time must be ISO 8601 datetimes"
        ) from exc
    return result if result.tzinfo else result.replace(tzinfo=local_tz)


def resolve_history_window(
    now: datetime,
    *,
    hours: int = 240,
    period: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_hours: int = 240,
) -> tuple[datetime, datetime, str]:
    """Calculate an exact window, with an enforced maximum history lookback.

    Available period names: today, yesterday, this_week, last_week,
    last_night (18:00-06:00 local), last_hour, last_24_hours,
    last_7_days and last_10_days.

    A last_week request near the end of a week might exceed retained history;
    in that case an error is returned rather than silently cutting it short.
    """
    if now.tzinfo is None:
        raise ValueError("The Home Assistant clock must be timezone-aware")
    if isinstance(hours, bool) or not 1 <= hours <= max_hours:
        raise ValueError(f"hours must be between 1 and {max_hours}")
    if (start_time or end_time) and period:
        raise ValueError("Use period OR start_time/end_time, not both")

    local_now = now
    tz = now.tzinfo
    today = local_now.date()
    midnight = _day_at(today, 0, tz)

    def subtract_elapsed_hours(anchor: datetime, number: int) -> datetime:
        # For rolling windows, subtract in UTC rather than wall-clock time:
        # a daylight-saving change must not make 240 h become 239/241 h.
        return (
            anchor.astimezone(timezone.utc) - timedelta(hours=number)
        ).astimezone(tz)

    if start_time is not None or end_time is not None:
        end = _parse_time(end_time, tz) if end_time is not None else now
        start = (
            _parse_time(start_time, tz)
            if start_time is not None
            else subtract_elapsed_hours(end, hours)
        )
        label = "custom"
    elif period is None:
        end = now
        start = subtract_elapsed_hours(now, hours)
        label = f"last_{hours}_hours"
    else:
        label = period.strip().lower().replace(" ", "_")
        if label == "today":
            start, end = midnight, now
        elif label == "yesterday":
            start = _day_at(today - timedelta(days=1), 0, tz)
            end = midnight
        elif label == "this_week":
            start = _day_at(today - timedelta(days=today.weekday()), 0, tz)
            end = now
        elif label == "last_week":
            this_monday = today - timedelta(days=today.weekday())
            start = _day_at(this_monday - timedelta(days=7), 0, tz)
            end = _day_at(this_monday, 0, tz)
        elif label == "last_night":
            start = _day_at(today - timedelta(days=1), 18, tz)
            end = min(now, _day_at(today, 6, tz))
        elif label in {
            "last_hour", "last_24_hours", "last_7_days", "last_10_days"
        }:
            period_hours = {
                "last_hour": 1,
                "last_24_hours": 24,
                "last_7_days": 168,
                "last_10_days": 240,
            }[label]
            start = subtract_elapsed_hours(now, period_hours)
            end = now
        else:
            raise ValueError(
                "Unsupported period. Use today, yesterday, this_week, "
                "last_week, last_night, last_hour, last_24_hours, "
                "last_7_days, last_10_days, or start_time/end_time."
            )

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    if end_utc > now_utc:
        if end_utc - now_utc > timedelta(seconds=1):
            raise ValueError("History cannot include future dates")
        end = now
        end_utc = now_utc
    if end_utc <= start_utc:
        raise ValueError("History range must have a positive duration")
    if start_utc < now_utc - timedelta(hours=max_hours, seconds=1):
        raise ValueError(
            f"The requested window starts more than {max_hours} hours ago. "
            "Recorder history is limited; choose a more recent range."
        )
    if end_utc - start_utc > timedelta(hours=max_hours, seconds=1):
        raise ValueError(f"History range must not exceed {max_hours} hours")
    return start, end, label
