"""重要事项合并与去重。

分段分析时，每一段都会产出候选重要事项。合并阶段需要：
- 把相同（分类 + 标题 + 日期）的条目合并，避免重复；
- 合并证据（保留去重后的原始片段与时间戳）；
- 保留最高可信度；
- 不打丢关键通知。
"""
from __future__ import annotations

import re

ALLOWED_CATEGORIES = {"exam", "assignment", "study_scope", "schedule_change", "activity", "other"}
CONFIDENCE_ORDER = {"explicit": 3, "probable": 2, "uncertain": 1}


def _norm_title(t: str) -> str:
    return re.sub(r"\s+", "", (t or "").lower())


def merge_items(items: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for it in items:
        cat = it.get("category") or "other"
        if cat not in ALLOWED_CATEGORIES:
            cat = "other"
        key = (cat, _norm_title(it.get("title")), it.get("normalized_date"),
               it.get('raw_date_text'), it.get('confidence') == 'uncertain', bool(it.get('action_required')))
        if key not in groups:
            groups[key] = {
                **it,
                "category": cat,
                "evidence": list(it.get("evidence") or []),
            }
            order.append(key)
        else:
            g = groups[key]
            seen = {(str(e.get("segment_id")), e.get("start_seconds"), e.get('quote')) for e in g["evidence"]}
            for e in (it.get("evidence") or []):
                kk = (str(e.get("segment_id")), e.get("start_seconds"), e.get('quote'))
                if kk not in seen:
                    g["evidence"].append(e)
                    seen.add(kk)
            if CONFIDENCE_ORDER.get(it.get("confidence"), 0) > CONFIDENCE_ORDER.get(g.get("confidence"), 0):
                g["confidence"] = it["confidence"]
            if it.get("action_required"):
                g["action_required"] = True
            detail = (it.get('details') or '').strip()
            old = (g.get('details') or '').strip()
            if detail and _norm_title(detail) != _norm_title(old):
                g['details'] = '\n'.join(dict.fromkeys([p for p in old.split('\n') if p] + [detail]))
    return [groups[k] for k in order]
