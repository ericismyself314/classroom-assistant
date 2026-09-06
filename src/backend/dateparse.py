"""相对日期解析（中文课堂场景）。

目标：把老师口中的「十月二十日」「下周五」「下个月五号」「明天」等表达，以本次课堂日期为基准，
解析成可靠的 ISO 日期；无法可靠解析时返回 None，并保留原始文本（绝不强行编造具体日期）。

可信原则：
- 绝对日期（含月份+日）最可靠 -> 直接换算。
- 「下周X」= 严格下一周的周X；「本周X/周X」= 本周的周X。
- 跨年（如 12 月说「明年3月」）此处不强行推断，保留原话（normalzed=None）。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_number(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        left, _, right = s.partition("十")
        left_val = _CN_NUM.get(left, 1) if left else 1
        right_val = _CN_NUM.get(right, 0) if right else 0
        return left_val * 10 + right_val
    # 纯汉字数字（如「五」）
    total = 0
    for ch in s:
        if ch in _CN_NUM:
            total = total * 10 + _CN_NUM[ch]
        else:
            return None
    return total if total else None


def _valid(month: int, day: int, base: date) -> date | None:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        d = date(base.year, month, day)
    except ValueError:
        return None
    return d


def parse_relative_date(text: str, base: date) -> str | None:
    """返回 ISO 日期字符串或 None。"""
    if not text:
        return None
    t = text.strip()

    # 1) 阿拉伯数字：M月D日 / M/D
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", t)
    if not m:
        m = re.search(r"(\d{1,2})\s*[/\-]\s*(\d{1,2})", t)
    if m:
        d = _valid(int(m.group(1)), int(m.group(2)), base)
        return d.isoformat() if d else None

    # 2) 中文：X月Y日
    m = re.search(r"([零〇一二两三四五六七八九十]+)\s*月\s*([零〇一二两三四五六七八九十]+)\s*[日号]?", t)
    if m:
        month = _cn_number(m.group(1))
        day = _cn_number(m.group(2))
        if month and day:
            d = _valid(month, day, base)
            return d.isoformat() if d else None

    # 3) 相对周几：「(下下)?(下|本|这)?周?/星期?(一二三四五六日天)」
    wd = None
    # 显式映射（含「周X」「星期X」「礼拜X」「X」）
    wmap = {
        "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    }
    wd = None
    for key, val in wmap.items():
        if re.search(r"周" + key + r"\b", t) or re.search(r"星期" + key, t) or re.search(r"礼拜" + key, t):
            wd = val
            break
    if wd is None:
        for key, val in wmap.items():
            if key in t and ("周" in t or "星期" in t or "礼拜" in t):
                wd = val
                break

    if wd is not None:
        mon = base - timedelta(days=base.weekday())
        this_week = mon + timedelta(days=wd)
        # 偏移
        if "下下周" in t or "下下个周" in t:
            return (this_week + timedelta(weeks=2)).isoformat()
        if "下周" in t or "下个周" in t or "下星期" in t or "下礼拜" in t:
            target = this_week + timedelta(weeks=1)
        elif "本周" in t or "这周" in t or "这星期" in t or "本周" in t:
            target = this_week
        else:
            # 仅「周X/星期五」：默认本周；若已过去则取本周（保持原话语义）
            target = this_week
        if target < base and ("下周" not in t and "下个周" not in t and "下星期" not in t and "下礼拜" not in t):
            # 已过且非明确「下周」：保守返回 None，避免误判到下周
            return None
        return target.isoformat()

    # 4) 明天 / 后天 / 大后天 / 今天
    if "大后天" in t:
        return (base + timedelta(days=3)).isoformat()
    if "后天" in t:
        return (base + timedelta(days=2)).isoformat()
    if "明天" in t:
        return (base + timedelta(days=1)).isoformat()
    if "今天" in t or "当日" in t:
        return base.isoformat()

    # 5) 下个月 D 号
    m = re.search(r"下个月\s*([零〇一二两三四五六七八九十\d]+)\s*[日号]?", t)
    if m:
        day = _cn_number(m.group(1))
        if day:
            try:
                nxt = (base.replace(day=1) + timedelta(days=32)).replace(day=day)
                return nxt.isoformat()
            except ValueError:
                return None

    return None


def normalize_date_text(raw: str | None, base: date) -> str | None:
    if not raw:
        return None
    return parse_relative_date(raw, base)
