from datetime import date

import pytest

from backend.dateparse import parse_relative_date


@pytest.mark.parametrize('text,expected', [
    ('下周五交第一章', '2026-09-11'),
    ('下下星期五交第一章', '2026-09-18'),
    ('下个星期五交作业', '2026-09-11'),
    ('每周复习第一章', None),
    ('2027-01-05交作业', '2027-01-05'),
    ('2027年1月5日考试', '2027-01-05'),
    ('2027/01/05考试', '2027-01-05'),
    ('2027-02-30考试', None),
    ('明年3月5日考试', None),
    ('10/20交作业', '2026-10-20'),
])
def test_dates_do_not_invent_weekdays_or_years(text, expected):
    assert parse_relative_date(text, date(2026, 9, 2)) == expected
