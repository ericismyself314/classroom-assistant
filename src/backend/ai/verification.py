"""5 层证据核查关卡（模型输出之后的独立验证）。

设计原则（见需求文档第八阶段）：
- 不依赖模型是否“听话”。即使模型自信地返回一条看似合理的通知，也要逐条独立核查。
- 代码级证据存在性核查是硬关卡，不能用“第二次模型调用”替代。

五层：
- L1 结构核查：category/confidence 合法、必填字段存在、segment_id 真实、时间戳在音频范围内、
  start<=end、引用非空且长度合理、normalized_date 格式合法。
- L2 原文存在性核查：每条证据的 quote 必须能在对应 segment 的**真实原文**中找到
  （保守归一化 + 有限模糊匹配）；找不到则拒绝该条“正式通知”。
- L3 上下文核查：把证据所在 segment 的真实文本分类为
  confirmed_announcement / student_question / hypothetical_example / speculation /
  correction_or_retraction / unclear。只有 confirmed_announcement 进正式。
- L4 语言规则辅助：问句/举例/不确定/明确通知信号，仅作为 L3 的特征，不单独决定结果。
- L5 展示规则：confirmed=正式重要事项；pending=待确认；rejected=已拒绝（默认不显示，保留调试）。

最终展示门槛（必须同时满足才算 confirmed）：
- 有真实 segment；有可定位时间；有真实证据引用；引用能在原文找到；
- 未被判为学生提问 / 假设案例；无明显不确定表达；未被后续内容推翻。
"""
from __future__ import annotations

import re
from datetime import date
import math

ALLOWED_CATEGORIES = {"exam", "assignment", "study_scope", "schedule_change", "activity", "other"}
ALLOWED_CONFIDENCE = {"explicit", "probable", "uncertain"}

# ---- L4 语言规则信号（仅辅助特征）----
QUESTION_SIGNALS = ["是不是", "要不要", "会不会", "可以吗", "吗？", "能否", "是否", "吗?", "吗"]
EXAMPLE_SIGNALS = ["比如", "例如", "假设", "如果", "假如", "举个例子", "for example",
                   "suppose", "hypothetically", "举例说明", "举个"]
UNCERTAIN_SIGNALS = ["可能", "大概", "也许", "暂定", "还没确定", "尚未确定", "应该",
                     "maybe", "probably", "not confirmed", "tentative", "或许", "估计", "可能放在"]
CONFIRMED_SIGNALS = ["定在", "安排在", "截止", "必须", "不需要提交", "不用提交", "不用交",
                    "取消", "延期", "改到", "大家要", "请在", "will be held", "due",
                    "must submit", "cancelled", "postponed", "确定改到", "已经确定",
                    "正式通知", "通知大家", "还是在",
                    "作业", "思考题", "练习", "请大家", "请完成", "请阅读", "上交", "提交"]
RETRACTION_SIGNALS = ["等一下", "我说错了", "更正", "纠正", "刚收到通知", "已经确定改到",
                      "改在", "更正为", "收回", "不是的", "其实"]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 归一化时去除的中英文标点（注意：\t \n 是合法转义，其余用字面字符，避免无效转义警告）
_PUNCT = "，。、？！,.?!；;：:“”‘’\"'（）()[]【】…—-_~·　 \t\n"


def _norm(s) -> str:
    """保守归一化：去空白与常见标点，转小写。仅用于存在性/信号匹配，不影响展示。"""
    if not s:
        return ""
    s = str(s).lower()
    for ch in _PUNCT:
        s = s.replace(ch, "")
    return s


def _quote_in_segment(quote: str, seg_text: str) -> bool:
    """L2：仅忽略标点/空白，引用的全部文字必须连续存在。"""
    q = _norm(quote)
    t = _norm(seg_text)
    if not q or not t:
        return False
    return q in t


def classify_context(text: str) -> str:
    """L3：把一段文本归为上下文类型。信号优先级：retraction > question > example > speculation > confirmed > unclear。

    注意：ASR 转写常丢失问号/标点，因此“学生提问”仅凭疑问短语信号判定，不强制要求字面问号。
    误判为提问的代价低（落入 pending 等人工确认），误判为通知的代价高（直接进正式），故宁可偏保守。
    """
    t = (text or "").lower()
    if any(sig in t for sig in RETRACTION_SIGNALS):
        return "correction_or_retraction"
    if any(sig in t for sig in QUESTION_SIGNALS):
        return "student_question"
    if any(sig in t for sig in EXAMPLE_SIGNALS):
        return "hypothetical_example"
    if any(sig in t for sig in UNCERTAIN_SIGNALS):
        return "speculation"
    if any(sig in t for sig in CONFIRMED_SIGNALS):
        return "confirmed_announcement"
    return "unclear"


def _index_segments(segments: list[dict]):
    """建立 segment_id(索引/字符串) -> segment 的映射。

    模型返回的 evidence.segment_id 通常是 _fmt_segments 中的 0 基序号；
    也兼容真实 DB id（字符串 uuid）。
    """
    by_idx: dict[int, dict] = {}
    by_id: dict[str, dict] = {}
    for i, s in enumerate(segments):
        by_idx[i] = s
        sid = s.get("id")
        if sid:
            by_id[str(sid)] = s
    return by_idx, by_id


def _resolve_segment(ev: dict, by_idx, by_id):
    seg_id = ev.get("segment_id")
    if seg_id is None:
        return None
    # 先按真实 id
    if str(seg_id) in by_id:
        return by_id[str(seg_id)]
    # 再按整数索引（模型可能返回越界/非法段号，必须容错而非抛异常）
    try:
        return by_idx[int(seg_id)]
    except (TypeError, ValueError, KeyError):
        return by_id.get(str(seg_id))


def _l1_structure(item: dict) -> str | None:
    """返回拒绝原因（字符串）或 None（通过）。"""
    cat = item.get("category")
    if cat not in ALLOWED_CATEGORIES:
        if cat in (None, ""):
            return "missing_category"
        # 非法类别直接拒绝（不擅自改写，避免误导）
        return f"invalid_category:{cat}"
    conf = item.get("confidence")
    if conf is not None and conf not in ALLOWED_CONFIDENCE:
        # 非法置信度：归并为 uncertain（可修复，不拒绝）
        item["confidence"] = "uncertain"
    title = (item.get("title") or "").strip()
    if not title:
        return "missing_title"
    nd = item.get("normalized_date")
    if nd:
        if not _DATE_RE.match(str(nd)):
            return f"bad_date:{nd}"
        try:
            date.fromisoformat(str(nd))
        except ValueError:
            return f"bad_date:{nd}"
    return None


def _l2_evidence(item: dict, segments, by_idx, by_id, audio_duration: float | None) -> tuple[str | None, bool]:
    """返回 (拒绝原因或None, 证据是否全部匹配)。

    硬要求：每条证据的 quote 必须能在真实转写原文中找到。
    - 优先按 evidence.segment_id 定位对应 segment；
    - 若 segment_id 缺失/错位，或定位到的 segment 不含该 quote，则在全部 segment 中扫描；
      （容忍模型偶尔的段号偏移，但仍强制“引用必须存在真实原文”这一底线）
    - 任何一段的 quote 在全文都找不到 -> evidence_not_found_in_source（拒绝）。
    """
    evidence = item.get("evidence") or []
    if not evidence:
        return "no_evidence", False
    all_matched = True
    for ev in evidence:
        quote = (ev.get("quote") or "").strip()
        if not quote or len(quote) > 300:
            return "evidence_quote_invalid", False
        # 定位 quote 所在的真实 segment：先按 segment_id，再退化全扫描
        target = _resolve_segment(ev, by_idx, by_id)
        if target is None or not _quote_in_segment(quote, target.get("text", "")):
            target = None
            for s in segments:
                if _quote_in_segment(quote, s.get("text", "")):
                    target = s
                    break
        if target is None:
            return "evidence_not_found_in_source", False
        # 时间戳范围（若有音频时长）——仅在 segment_id 可信时使用其时间戳
        if audio_duration is not None:
            st = ev.get("start_seconds")
            en = ev.get("end_seconds")
            if st is not None and (st < -0.5 or st > audio_duration + 0.5):
                return "evidence_timestamp_out_of_range", False
            if st is not None and en is not None and st > en + 0.5:
                return "evidence_start_after_end", False
        if not _quote_in_segment(quote, target.get("text", "")):
            all_matched = False
    if not all_matched:
        return "evidence_not_found_in_source", False
    return None, True


def verify_item(item: dict, segments, by_idx, by_id, audio_duration: float | None) -> dict:
    """对单条事项做 L1+L2+L3+L5 核查，返回带 verification_* 字段的新 dict（不修改原对象）。"""
    reason = _l1_structure(item)
    evidence_matched = True
    if reason is None:
        reason, evidence_matched = _l2_evidence(item, segments, by_idx, by_id, audio_duration)

    # 上下文：用证据所在 segment 的真实文本（而非模型 quote）判断
    ctx_text = []
    source_uncertain = False
    for ev in (item.get("evidence") or []):
        seg = _resolve_segment(ev, by_idx, by_id)
        quote = ev.get("quote", "")
        if seg is None or (quote and not _quote_in_segment(quote, seg.get("text", ""))):
            # 退化扫描：找真正包含 quote 的 segment
            for s in segments:
                if _quote_in_segment(quote, s.get("text", "")):
                    seg = s
                    break
        if seg:
            ctx_text.append(seg.get("text", ""))
            source_uncertain = source_uncertain or bool(seg.get("is_uncertain"))
    context = classify_context(" ".join(ctx_text))

    if reason is not None:
        status = "rejected"
    elif context == "confirmed_announcement" and not source_uncertain and item.get("confidence") != "uncertain":
        status = "confirmed"
    else:
        status = "pending"

    return {
        **item,
        "confidence": "uncertain" if source_uncertain else item.get("confidence", "uncertain"),
        "verification_status": status,
        "context_type": context,
        "rejection_reason": reason,
        "evidence_matched": int(evidence_matched),
    }


def _source_position(item, segments, by_idx, by_id):
    """Use located source text, never model-supplied timestamps, to order corrections."""
    ordered = list(enumerate(segments))
    times = [s.get('start_seconds') for s in segments]
    if all(isinstance(t, (int, float)) and math.isfinite(t) for t in times):
        ordered.sort(key=lambda pair: (pair[1]['start_seconds'], pair[0]))
    ranks = {index: rank for rank, (index, _) in enumerate(ordered)}
    positions = []
    for evidence in item.get('evidence') or []:
        quote = _norm(evidence.get('quote'))
        cited = _resolve_segment(evidence, by_idx, by_id)
        targets = [(i, s) for i, s in enumerate(segments)
                   if s is cited and _quote_in_segment(quote, s.get('text', ''))]
        if not targets:
            targets = [(i, s) for i, s in enumerate(segments)
                       if _quote_in_segment(quote, s.get('text', ''))]
        matches = []
        for index, segment in targets:
            text = _norm(segment.get('text', ''))
            position = text.find(quote)
            while quote and position >= 0:
                matches.append((ranks[index], position))
                position = text.find(quote, position + 1)
        if len(matches) != 1:
            return None
        positions.append(matches[0])
    return max(positions) if positions else None


def apply_verification(items: list[dict], segments: list[dict], session_meta: dict | None = None) -> dict:
    """对整批事项核查，并按状态分组。

    返回 {confirmed:[], pending:[], rejected:[]}，每条含 verification_* 字段。
    同时处理“前后修正”：correction 条目若带明确信号，则把同分类、同标题但日期/地点不同的
    “已确认”条目降级为 rejected（reason=superseded_by_correction）。
    """
    session_meta = session_meta or {}
    audio_duration = session_meta.get("audio_duration_seconds")
    by_idx, by_id = _index_segments(segments)

    verified = [verify_item(it, segments, by_idx, by_id, audio_duration) for it in items]

    groups = {}
    for value in verified:
        if value['verification_status'] != 'rejected':
            groups.setdefault((value.get('category'), _norm(value.get('title'))), []).append(value)
    for group in groups.values():
        if not any(v['context_type'] == 'correction_or_retraction' for v in group):
            continue
        positions = [_source_position(v, segments, by_idx, by_id) for v in group]
        if None in positions:
            for value in group:
                value['verification_status'] = 'pending'
            continue
        latest = max(positions)
        winners = [v for v, position in zip(group, positions) if position == latest]
        facts = {(v.get('normalized_date') or '', _norm(v.get('details'))) for v in winners}
        winner = winners[0]
        wording = ' '.join(e.get('quote', '') for e in winner.get('evidence') or []).lower()
        clear = winner['verification_status'] == 'confirmed' or (
            winner['context_type'] == 'correction_or_retraction'
            and winner.get('confidence') != 'uncertain'
            and any(signal in wording for signal in CONFIRMED_SIGNALS))
        if len(facts) != 1 or not clear:
            for value in group:
                value['verification_status'] = 'pending'
            continue
        for value in group:
            value_facts = (value.get('normalized_date') or '', _norm(value.get('details')))
            if value_facts != next(iter(facts)):
                value.update(verification_status='rejected', rejection_reason='superseded_by_correction',
                             context_type='correction_or_retraction')
            elif value in winners and value['context_type'] == 'correction_or_retraction':
                value['verification_status'] = 'confirmed'

    return {status: [v for v in verified if v['verification_status'] == status]
            for status in ('confirmed', 'pending', 'rejected')}
