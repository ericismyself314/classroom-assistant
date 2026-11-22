"""课表与周次计算。

设计要点：
- 不预先生成整个学期的空课堂记录；根据课表槽位 + 日期动态计算某天/某周有哪些课。
- 用户上传录音、导入文字或修改某次课堂时，才在 class_sessions 中创建实际记录。
- 默认以学期开始日所在周为第 1 周。校准周次只调整周一基准，排课日期独立。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from . import db
from .logutil import get_logger

LOG = get_logger("schedule")

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def iso_weekday(d: date) -> int:
    """返回 0=周一 ... 6=周日。"""
    return d.weekday()


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_number(semester_start: str, session_date: str) -> int:
    start = _parse_date(semester_start)
    sess = _parse_date(session_date)
    if not start or not sess:
        return 0
    return (monday_of(sess) - monday_of(start)).days // 7 + 1


def week_anchor(semester: dict) -> str:
    return semester.get("week_one_monday") or semester["start_date"]


def calibrated_monday(reference: str, number: int) -> str:
    day = _parse_date(reference)
    if day is None or type(number) is not int or number < 1:
        raise ValueError("请输入有效日期和大于零的整数周次。")
    try:
        return (monday_of(day) - timedelta(weeks=number - 1)).isoformat()
    except OverflowError as exc:
        raise ValueError("周次超出了可表示的日期范围。") from exc


def session_with_week(session: dict, semester: dict | None) -> dict:
    if not semester or (not semester.get("week_one_monday") and session.get("week_number")):
        return session
    number = week_number(week_anchor(semester), session["session_date"])
    return {**session, "week_number": number if number > 0 else None}


def validate_slots(slots: list[dict]) -> None:
    if not isinstance(slots, list):
        raise ValueError("课表格式无效。")
    for i, slot in enumerate(slots, 1):
        try:
            weekday = int(slot["weekday"])
            start, end = (time.fromisoformat(slot[key]) for key in ("start_time", "end_time"))
            if not 0 <= weekday < 7 or end <= start:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"请检查第 {i} 个时段的星期和起止时间。") from exc


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def occurrences_for_date(slots: Iterable[dict], day: date, semester_start: str, semester_end: str) -> list[dict]:
    """返回某天应上的课程（基于课表槽位）。"""
    start = _parse_date(semester_start)
    end = _parse_date(semester_end)
    if start and day < start:
        return []
    if end and day > end:
        return []
    wd = day.weekday()
    out = []
    for slot in slots:
        if int(slot["weekday"]) != wd:
            continue
        rs = slot.get("recurrence_start")
        re = slot.get("recurrence_end")
        if rs and _parse_date(rs) and day < _parse_date(rs):
            continue
        if re and _parse_date(re) and day > _parse_date(re):
            continue
        out.append(slot)
    return out


def week_days(center: date) -> list[date]:
    """返回以 center 所在周周一为起点的 7 天。"""
    mon = monday_of(center)
    return [mon + timedelta(days=i) for i in range(7)]


def build_week_plan(semester: dict, courses: list[dict], center: date) -> list[dict]:
    """构建「本周」页面数据：7 天，每天列出课程 + 是否已存在课堂记录。"""
    days = week_days(center)
    # 收集该学期所有课表槽位：course_id -> slots
    slot_map: dict[str, list[dict]] = {}
    for c in courses:
        slot_map[c["id"]] = db.list_slots(c["id"])
    course_by_id = {c["id"]: c for c in courses}

    # 预取这些日期范围内已存在的 session
    existing: dict[tuple[str, str], dict] = {}
    for c in courses:
        for s in db.list_sessions(c["id"]):
            existing[(c["id"], s["session_date"])] = s

    plan = []
    for d in days:
        day_slots = []
        for cid, slots in slot_map.items():
            for slot in occurrences_for_date(slots, d, semester["start_date"], semester["end_date"]):
                course = course_by_id[cid]
                key = (cid, d.isoformat())
                sess = existing.get(key)
                day_slots.append({
                    "course_id": cid,
                    "course_name": course["name"],
                    "color": course["color"],
                    "language_mode": course["language_mode"],
                    "weekday": d.weekday(),
                    "start_time": slot["start_time"],
                    "end_time": slot["end_time"],
                    "location": slot.get("location") or course.get("location"),
                    "session": session_with_week(sess, semester) if sess else None,
                    "week_number": week_number(week_anchor(semester), d.isoformat()),
                })
        day_slots.sort(key=lambda x: x["start_time"])
        plan.append({
            "date": d.isoformat(),
            "weekday": d.weekday(),
            "weekday_cn": WEEKDAY_CN[d.weekday()],
            "is_today": d == date.today(),
            "slots": day_slots,
        })
    return plan


def shift_week(center: date, delta_weeks: int) -> date:
    return center + timedelta(weeks=delta_weeks)
