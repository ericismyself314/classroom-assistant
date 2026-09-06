"""第五阶段：5 层证据核查关卡的单元测试 + 防误报测试集（30+ 用例）。

目标：在“模型看似合理但其实是编造”的情况下，逐条独立核查并拒绝进入正式重要事项。

覆盖：
- L1 结构：category/confidence 非法、缺标题、日期格式错
- L2 原文存在性：无证据、引用为空/超长、引用在真实原文找不到（含“泡泡玛特”编造测试）
- L3 上下文：confirmed_announcement / student_question / hypothetical_example / speculation / correction_or_retraction
- L5 展示：confirmed / pending / rejected，以及 correction 覆盖同标题旧条目
- DB 持久化：rejected 入库但默认隐藏
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from backend.ai import verification as V  # noqa: E402


# ---------------------------------------------------------------------------
# 真实课堂转写（用于证据存在性核查的“地面真相”）
# ---------------------------------------------------------------------------
SEGMENTS = [
    {"id": "seg0", "start_seconds": 0.0, "end_seconds": 6.0,
     "text": "同学们好，今天我们讲第三章。下周三的期中考试定在 10 月 15 日，地点在 H201。"},
    {"id": "seg1", "start_seconds": 6.0, "end_seconds": 11.0,
     "text": "老师，这个考试会不会很难啊？还是说可以带笔记进去？"},
    {"id": "seg2", "start_seconds": 11.0, "end_seconds": 17.0,
     "text": "比如如果考到第五章的矩阵运算，那大家要重点复习特征值和特征向量。"},
    {"id": "seg3", "start_seconds": 17.0, "end_seconds": 23.0,
     "text": "另外期末大作业可能要分组做，暂定每组三到四人，具体后面再通知。"},
    {"id": "seg4", "start_seconds": 23.0, "end_seconds": 30.0,
     "text": "等一下，我刚收到通知，期中考试已经确定改到 10 月 22 日了，请大家注意群里公告。"},
    {"id": "seg5", "start_seconds": 30.0, "end_seconds": 36.0,
     "text": "今天的课后思考题是阅读课本第 88 页的案例，下周上课讨论。"},
]


def item(**kw):
    base = {
        "category": "exam",
        "title": "测试事项",
        "details": "",
        "confidence": "explicit",
        "evidence": [],
    }
    base.update(kw)
    return base


def run(items):
    return V.apply_verification(items, SEGMENTS, {"audio_duration_seconds": 36.0})


# ---------------------------------------------------------------------------
# L1 结构核查
# ---------------------------------------------------------------------------
def test_l1_missing_category():
    res = run([item(category=None)])
    assert res["rejected"][0]["rejection_reason"] == "missing_category"
    assert res["rejected"][0]["verification_status"] == "rejected"


def test_l1_invalid_category():
    res = run([item(category="weather")])
    assert res["rejected"][0]["rejection_reason"].startswith("invalid_category")
    assert res["rejected"][0]["verification_status"] == "rejected"


def test_l1_missing_title():
    res = run([item(title="   ")])
    assert res["rejected"][0]["rejection_reason"] == "missing_title"


def test_l1_bad_date_format():
    res = run([item(title="测验", normalized_date="10/15", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}])])
    assert res["rejected"][0]["rejection_reason"].startswith("bad_date")


def test_l1_invalid_confidence_normalized_to_uncertain():
    # 非法置信度会被归并为 uncertain（不拒绝），最终看上下文
    res = run([item(confidence="super_sure", title="考试安排", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}])])
    assert res["pending"] and not res["confirmed"]  # Invalid confidence remains uncertain.


# ---------------------------------------------------------------------------
# L2 原文存在性核查（核心防误报关卡）
# ---------------------------------------------------------------------------
def test_l2_no_evidence():
    res = run([item(title="无证据事项", evidence=[])])
    assert res["rejected"][0]["rejection_reason"] == "no_evidence"


def test_l2_quote_empty():
    res = run([item(title="空引用", evidence=[{"segment_id": 0, "quote": "   ", "start_seconds": 1.0}])])
    assert res["rejected"][0]["rejection_reason"] == "evidence_quote_invalid"


def test_l2_quote_too_long():
    res = run([item(title="超长引用", evidence=[{"segment_id": 0, "quote": "x" * 301, "start_seconds": 1.0}])])
    assert res["rejected"][0]["rejection_reason"] == "evidence_quote_invalid"


def test_l2_quote_not_in_source_rejected():
    # 模型编造的引用，原文根本不存在 -> 拒绝
    res = run([item(title="编造的通知", evidence=[
        {"segment_id": 0, "quote": "学校决定全体放假一周去旅游", "start_seconds": 1.0, "end_seconds": 3.0}])])
    assert res["rejected"][0]["rejection_reason"] == "evidence_not_found_in_source"
    assert res["rejected"][0]["verification_status"] == "rejected"


def test_l2_quote_not_in_source_rejected_2():
    # 另一编造引用（真实但有歧义）
    res = run([item(title="考试改线上", evidence=[
        {"segment_id": 0, "quote": "考试改为线上进行", "start_seconds": 1.0, "end_seconds": 3.0}])])
    assert res["rejected"][0]["rejection_reason"] == "evidence_not_found_in_source"


def test_popmart_hallucination_rejected():
    # “泡泡玛特”编造测试：模型自信地返回一条和课堂无关、原文完全没有的促销通知
    res = run([item(
        category="activity",
        title="下周购买泡泡玛特盲盒享八折优惠",
        details="品牌方赞助，凭学生证到店打折",
        normalized_date="2026-10-18",
        confidence="explicit",
        evidence=[{"segment_id": 2, "quote": "购买泡泡玛特盲盒享八折优惠", "start_seconds": 12.0, "end_seconds": 15.0}])])
    assert res["rejected"], "泡泡玛特编造通知必须被拒绝"
    assert res["rejected"][0]["rejection_reason"] == "evidence_not_found_in_source"
    assert res["rejected"][0]["evidence_matched"] == 0


def test_l2_quote_present_in_real_source_confirmed():
    res = run([item(title="期中考试", normalized_date="2026-10-15", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}])])
    assert res["confirmed"]
    assert res["confirmed"][0]["context_type"] == "confirmed_announcement"
    assert res["confirmed"][0]["evidence_matched"] == 1


def test_l2_fuzzy_head_tail_match():
    # 较长引用做首尾保守模糊匹配
    long_quote = "下周三的期中考试定在 10 月 15 日，地点在 H201"
    res = run([item(title="考试安排", evidence=[
        {"segment_id": 0, "quote": long_quote, "start_seconds": 0.5, "end_seconds": 5.5}])])
    assert res["confirmed"], "长引用首尾命中应判为存在"


def test_l2_timestamp_out_of_range():
    res = run([item(title="考试安排", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 999.0, "end_seconds": 1000.0}])])
    assert res["rejected"][0]["rejection_reason"] == "evidence_timestamp_out_of_range"


def test_l2_start_after_end():
    res = run([item(title="考试安排", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 5.0, "end_seconds": 1.0}])])
    assert res["rejected"][0]["rejection_reason"] == "evidence_start_after_end"


def test_l2_segment_id_missing_falls_back_scan():
    # 模型没给 segment_id，但 quote 真实存在 -> 全扫描兜底，仍判存在
    res = run([item(title="考试安排", normalized_date="2026-10-15", evidence=[
        {"segment_id": None, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}])])
    assert res["confirmed"], "缺 segment_id 时应退化扫描并找到原文"


def test_l2_wrong_segment_id_falls_back_scan():
    # 模型段号错位（指向 seg2），但 quote 真实存在 -> 兜底接受
    res = run([item(title="考试安排", normalized_date="2026-10-15", evidence=[
        {"segment_id": 2, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}])])
    assert res["confirmed"], "段号错位的引用只要原文存在就应接受"


# ---------------------------------------------------------------------------
# L3 上下文核查
# ---------------------------------------------------------------------------
def test_l3_student_question_pending():
    res = run([item(title="考试难不难", evidence=[
        {"segment_id": 1, "quote": "考试会不会很难", "start_seconds": 6.5, "end_seconds": 10.0}])])
    assert res["pending"]
    assert res["pending"][0]["context_type"] == "student_question"
    assert res["pending"][0]["verification_status"] == "pending"


def test_l3_hypothetical_example_pending():
    res = run([item(title="第五章重点", evidence=[
        {"segment_id": 2, "quote": "比如如果考到第五章", "start_seconds": 11.5, "end_seconds": 16.0}])])
    assert res["pending"][0]["context_type"] == "hypothetical_example"


def test_l3_speculation_pending():
    res = run([item(title="期末大作业", evidence=[
        {"segment_id": 3, "quote": "期末大作业可能要分组做", "start_seconds": 17.5, "end_seconds": 22.0}])])
    assert res["pending"][0]["context_type"] == "speculation"


def test_l3_confirmed_announcement():
    res = run([item(title="期中考试", normalized_date="2026-10-15", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}])])
    assert res["confirmed"][0]["context_type"] == "confirmed_announcement"


def test_l3_correction_confirmed():
    res = run([item(title="考试改期", normalized_date="2026-10-22", evidence=[
        {"segment_id": 4, "quote": "期中考试已经确定改到 10 月 22 日", "start_seconds": 23.5, "end_seconds": 29.0}])])
    # 带明确通知信号 -> 以最后表述为准，进 confirmed
    assert res["confirmed"]
    assert res["confirmed"][0]["context_type"] == "correction_or_retraction"


def test_l3_correction_supersedes_same_title():
    # 同标题旧条目（10/15 已确认）被后面的修正（10/22）覆盖为 rejected
    items = [
        item(title="期中考试", normalized_date="2026-10-15", evidence=[
            {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}]),
        item(title="期中考试", normalized_date="2026-10-22", evidence=[
            {"segment_id": 4, "quote": "期中考试已经确定改到 10 月 22 日", "start_seconds": 23.5, "end_seconds": 29.0}]),
    ]
    res = run(items)
    titles = [i["title"] for i in res["rejected"]]
    assert "期中考试" in titles, "被修正覆盖的同标题旧条目应进 rejected"
    assert any(i["rejection_reason"] == "superseded_by_correction" for i in res["rejected"])
    # 最终确认的是 10/22 那条
    assert any(i["normalized_date"] == "2026-10-22" for i in res["confirmed"])


def test_l3_correction_without_confirm_signal_stays_pending():
    # 修正信号但无明确通知措辞：归为 correction_or_retraction，但不强行确认
    res = run([item(title="考试可能改期", evidence=[
        {"segment_id": 4, "quote": "等一下，我刚收到通知", "start_seconds": 23.5, "end_seconds": 25.0}])])
    # 无 CONFIRMED_SIGNALS -> 不进 confirmed（仍 pending，等待人工确认）
    assert res["confirmed"] == []
    assert res["pending"]


# ---------------------------------------------------------------------------
# L4 语言信号（仅辅助，单独不决定）
# ---------------------------------------------------------------------------
def test_l4_uncertain_signal_blocks_confirm():
    # 标题含“可能”，即使 segment 里有被否定的内容，也不应直接确认
    res = run([item(title="可能调课", details="可能改时间", evidence=[
        {"segment_id": 3, "quote": "期末大作业可能要分组做", "start_seconds": 17.5, "end_seconds": 22.0}])])
    assert res["confirmed"] == [], "含不确定信号不应进 confirmed"


# ---------------------------------------------------------------------------
# L5 展示与分组
# ---------------------------------------------------------------------------
def test_l5_grouping_counts():
    items = [
        item(title="A确认", normalized_date="2026-10-15", evidence=[
            {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}]),
        item(title="B提问", evidence=[{"segment_id": 1, "quote": "考试会不会很难", "start_seconds": 6.5, "end_seconds": 10.0}]),
        item(title="C编造", evidence=[{"segment_id": 0, "quote": "学校决定放假去旅游", "start_seconds": 1.0, "end_seconds": 3.0}]),
    ]
    res = run(items)
    assert len(res["confirmed"]) == 1
    assert len(res["pending"]) == 1
    assert len(res["rejected"]) == 1


def test_l5_returns_status_field_on_each():
    items = [item(title="A确认", normalized_date="2026-10-15", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}])]
    res = run(items)
    it = res["confirmed"][0]
    assert set(["verification_status", "context_type", "rejection_reason", "evidence_matched"]).issubset(it.keys())


# ---------------------------------------------------------------------------
# 多证据：任一条引用编造即整条拒绝
# ---------------------------------------------------------------------------
def test_multi_evidence_one_fake_rejects_all():
    res = run([item(title="混合证据", normalized_date="2026-10-15", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0},
        {"segment_id": 2, "quote": "购买泡泡玛特盲盒享八折", "start_seconds": 12.0, "end_seconds": 15.0}])])
    assert res["rejected"], "任一证据编造则整条拒绝"


# ---------------------------------------------------------------------------
# 边界：全文不含问号但语义是提问
# ---------------------------------------------------------------------------
def test_question_signal_without_question_mark():
    # 含“是不是”且是疑问语气，但无问号 -> 仍判 student_question
    res = run([item(title="能不能补交", evidence=[
        {"segment_id": 1, "quote": "还是说可以带笔记进去", "start_seconds": 8.0, "end_seconds": 10.0}])])
    # 这条不是问号问句，靠 EXAMPLE/QUESTION 信号其一；这里主要验证不误判为 confirmed
    assert res["confirmed"] == []


def test_real_announcement_with_location_and_time_confirmed():
    res = run([item(title="期中考试", normalized_date="2026-10-15", details="H201", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日，地点在 H201", "start_seconds": 1.0, "end_seconds": 5.0}])])
    assert res["confirmed"]


def test_homework_assignment_confirmed():
    res = run([item(category="assignment", title="课后思考题", evidence=[
        {"segment_id": 5, "quote": "课后思考题是阅读课本第 88 页的案例", "start_seconds": 30.5, "end_seconds": 35.0}])])
    assert res["confirmed"], "明确的作业通知应为 confirmed"


def test_activity_category_with_real_evidence():
    # 用真实存在的“群里公告”措辞
    res = run([item(category="activity", title="群公告提醒", evidence=[
        {"segment_id": 4, "quote": "请大家注意群里公告", "start_seconds": 27.0, "end_seconds": 29.0}])])
    # 注意：evidence 命中但上下文是 correction 段，可能 pending/confirmed 取决于信号
    assert res["rejected"] == [], "引用真实存在不应被拒"


def test_empty_transcript_no_match():
    res = V.apply_verification([item(title="X", evidence=[
        {"segment_id": 0, "quote": "任何文字", "start_seconds": 1.0}])], [], {"audio_duration_seconds": 10.0})
    assert res["rejected"][0]["rejection_reason"] in ("evidence_not_found_in_source",)


def test_uncertain_confidence_with_real_evidence_stays_pending():
    res = run([item(confidence="uncertain", title="期中考试", normalized_date="2026-10-15", evidence=[
        {"segment_id": 0, "quote": "期中考试定在 10 月 15 日", "start_seconds": 1.0, "end_seconds": 4.0}])])
    # An exact quote alone must not upgrade an uncertain interpretation.
    assert res["pending"] and not res["confirmed"]


def test_classify_context_returns_all_types():
    assert V.classify_context("考试定在 10 月 15 日") == "confirmed_announcement"
    assert V.classify_context("考试会不会很难") == "student_question"
    assert V.classify_context("比如如果考到第五章") == "hypothetical_example"
    assert V.classify_context("可能要分组做") == "speculation"
    assert V.classify_context("等一下，我刚收到通知，改到 22 日") == "correction_or_retraction"
    assert V.classify_context("同学们翻开课本") == "unclear"


def test_quote_in_segment_normalization():
    assert V._quote_in_segment("考试定在10月15日", "同学们，期中考试定在 10 月 15 日，地点在 H201。")
    assert not V._quote_in_segment("放假去旅游", "同学们，期中考试定在 10 月 15 日")


def test_normalize_strips_punctuation():
    assert V._norm("  “期 中 考 试” ！") == "期中考试"


# ---------------------------------------------------------------------------
# DB 持久化：rejected 入库但默认隐藏
# ---------------------------------------------------------------------------
def _make_session():
    from backend import db
    sem = db.create_semester("2026 Fall", "2026-09-01", "2027-01-31")
    cid = db.create_course(sem, "市场营销")
    sid = db.get_or_create_session(cid, "2026-10-15")
    return sid, cid


def test_db_persist_hides_rejected_by_default(fresh_app):
    from backend import db
    sid, cid = _make_session()
    # 直接写一条（绕过 pipeline，仅验证 list_items 过滤语义）
    db.replace_items(sid, [
        {"course_id": cid, "category": "exam", "title": "正式考试", "confidence": "explicit",
         "verification_status": "confirmed", "context_type": "confirmed_announcement",
         "rejection_reason": None, "evidence_matched": 1},
        {"course_id": cid, "category": "activity", "title": "编造泡泡玛特", "confidence": "explicit",
         "verification_status": "rejected", "context_type": "unclear",
         "rejection_reason": "evidence_not_found_in_source", "evidence_matched": 0},
    ])
    default = db.list_items({"course_id": cid})
    assert all(i["verification_status"] != "rejected" for i in default)
    assert len(default) == 1
    incl = db.list_items({"course_id": cid, "include_rejected": "1"})
    assert len(incl) == 2


def test_db_filter_by_verification_status(fresh_app):
    from backend import db
    sid, cid = _make_session()
    db.replace_items(sid, [
        {"course_id": cid, "category": "exam", "title": "A", "confidence": "explicit",
         "verification_status": "confirmed", "context_type": "confirmed_announcement",
         "rejection_reason": None, "evidence_matched": 1},
        {"course_id": cid, "category": "exam", "title": "B", "confidence": "explicit",
         "verification_status": "pending", "context_type": "student_question",
         "rejection_reason": None, "evidence_matched": 1},
    ])
    confirmed_only = db.list_items({"course_id": cid, "verification_status": "confirmed"})
    assert len(confirmed_only) == 1 and confirmed_only[0]["title"] == "A"
    pending_only = db.list_items({"course_id": cid, "verification_status": "pending"})
    assert len(pending_only) == 1 and pending_only[0]["title"] == "B"
