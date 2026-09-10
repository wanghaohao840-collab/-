from datetime import date, datetime
import pytest
from app.learning_schedule import allocate_phases, plan_dates

@pytest.mark.parametrize("days,expected", [
    (1,("review",)), (2,("reading","review")),
    (3,("reading","cards","review")),
    (4,("reading","cards","exercises","review")),
])
def test_short_plan_phases(days, expected):
    assert allocate_phases(days) == expected

@pytest.mark.parametrize("days", [0,366,True,1.5,"3",None])
def test_invalid_days(days):
    with pytest.raises(ValueError):
        allocate_phases(days)
    with pytest.raises(ValueError):
        plan_dates(date(2026,9,6), days)

def test_calendar_days_cross_leap_day():
    assert plan_dates(date(2028,2,28),3) == (date(2028,2,28),date(2028,2,29),date(2028,3,1))

@pytest.mark.parametrize("days", range(1,366))
def test_total_and_order(days):
    phases=allocate_phases(days)
    assert len(phases)==days
    order={phase:index for index,phase in enumerate(("reading","cards","exercises","review"))}
    assert list(phases)==sorted(phases,key=order.__getitem__)
    if days>=4:
        assert set(phases)==set(order)


def test_datetime_start_rejected():
    with pytest.raises(ValueError):
        plan_dates(datetime(2026, 9, 6), 3)


def test_maximum_date_range():
    dates = plan_dates(date(2026, 9, 6), 365)
    assert len(dates) == 365
    assert dates[0] == date(2026, 9, 6)
    assert dates[-1] == date(2027, 9, 5)
