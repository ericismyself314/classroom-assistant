"""AI 输出结构化校验与修复。

规则（见需求文档第十章）：
- 模型必须输出严格结构化 JSON；
- 如果格式不符合，先尝试修复（剥离 ```json``` 围栏、截取首个 { / [ ）；
- 必要时重新请求一次（由调用方决定）；
- 仍然失败则保留错误信息，不把无法解析的原始 JSON 直接展示给普通用户；
- 分类 / 可信度必须是受控枚举。
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import dateparse
from .important import ALLOWED_CATEGORIES  # noqa: F401  (re-export for convenience)

ALLOWED_CATEGORIES = {"exam", "assignment", "study_scope", "schedule_change", "activity", "other"}
ALLOWED_CONFIDENCE = {"explicit", "probable", "uncertain"}


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _extract_json(text):
    if isinstance(text, dict):
        return text
    if isinstance(text, list):
        return text
    if not isinstance(text, str):
        return None
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except Exception:
            pass
    start, end = s.find("["), s.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except Exception:
            pass
    return None


def normalize_overview(obj):
    data = _extract_json(obj)
    if data is None:
        return None, "无法解析为 JSON"
    if isinstance(data, list):
        topics, overview_title = data, "本节课内容概览"
    elif isinstance(data, dict):
        topics = data.get("topics")
        overview_title = data.get("overview_title") or "本节课内容概览"
        if topics is None and ("items" in data or "important_items" in data):
            return None, "返回结构不是概览（缺少 topics 字段）"
    else:
        return None, "结构类型不支持"
    if not isinstance(topics, list):
        return None, "topics 不是数组"
    out = []
    for i, t in enumerate(topics):
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "summary": (t.get("summary") or "").strip(),
            "start_seconds": _num(t.get("start_seconds")),
            "end_seconds": _num(t.get("end_seconds")),
            "evidence_segment_ids": t.get("evidence_segment_ids") or [],
            "sequence": i,
        })
    if not out:
        return None, "未解析到任何主题"
    return {"overview_title": overview_title, "topics": out}, None


def normalize_items(obj, session_date: str):
    data = _extract_json(obj)
    if data is None:
        return None, "无法解析为 JSON"
    if isinstance(data, dict):
        items = data.get("items") if "items" in data else data.get("important_items")
        if items is None:
            if "title" in data and "category" in data:
                items = [data]
            else:
                return None, "缺少 items 数组"
    elif isinstance(data, list):
        items = data
    else:
        return None, "结构类型不支持"
    if not isinstance(items, list):
        return None, "items 不是数组"
    if not items:
        return [], None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        cat = it.get("category")
        if cat not in ALLOWED_CATEGORIES:
            cat = "other"
        title = (it.get("title") or "").strip()
        if not title:
            continue
        conf = it.get("confidence")
        if conf not in ALLOWED_CONFIDENCE:
            conf = "explicit"
        evidence = it.get("evidence") or []
        norm_ev = []
        for e in evidence:
            if isinstance(e, dict) and (e.get("segment_id") or e.get("quote")):
                norm_ev.append({
                    "segment_id": e.get("segment_id"),
                    "start_seconds": _num(e.get("start_seconds")),
                    "end_seconds": _num(e.get("end_seconds")),
                    "quote": (e.get("quote") or "").strip(),
                })
        out.append({
            "category": cat,
            "title": title,
            "details": (it.get("details") or "").strip(),
            "raw_date_text": it.get("raw_date_text"),
            "normalized_date": it.get("normalized_date"),
            "action_required": bool(it.get("action_required", False)),
            "confidence": conf,
            "evidence": norm_ev,
        })
    if not out:
        return None, "未解析到任何重要事项"
    return out, None


def apply_date_normalization(items: list[dict], session_date: str) -> list[dict]:
    """用相对日期解析补全 normalized_date（仅当模型未给出或给出不可信时）。"""
    try:
        base = date.fromisoformat(session_date)
    except Exception:
        return items
    for it in items:
        raw = it.get("raw_date_text")
        if raw and not it.get("normalized_date"):
            nd = dateparse.parse_relative_date(raw, base)
            if nd:
                it["normalized_date"] = nd
    return items
