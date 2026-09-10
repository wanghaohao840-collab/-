from datetime import date, timedelta

PHASES = ("reading", "cards", "exercises", "review")

def _valid_days(days: int) -> None:
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("days must be an integer in 1..365")

def allocate_phases(days: int) -> tuple[str, ...]:
    _valid_days(days)
    if days == 1:
        return ("review",)
    if days == 2:
        return ("reading", "review")
    if days == 3:
        return ("reading", "cards", "review")
    quotas = [days * weight / 100 for weight in (25, 30, 25, 20)]
    floors = [int(quota) for quota in quotas]
    counts = [max(1, floor) for floor in floors]
    fractions = [quota - floor for quota, floor in zip(quotas, floors)]
    while sum(counts) < days:
        for index in sorted(range(4), key=lambda i: (-fractions[i], i)):
            if sum(counts) == days:
                break
            counts[index] += 1
    while sum(counts) > days:
        for index in sorted(range(4), key=lambda i: (fractions[i], i)):
            if sum(counts) == days:
                break
            if counts[index] > 1:
                counts[index] -= 1
    return tuple(phase for phase, count in zip(PHASES, counts) for _ in range(count))

def plan_dates(start: date, days: int) -> tuple[date, ...]:
    _valid_days(days)
    if type(start) is not date:
        raise ValueError("start must be a date")
    return tuple(start + timedelta(days=offset) for offset in range(days))
