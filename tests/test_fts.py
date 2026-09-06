"""阶段七：中文 FTS5（trigram）搜索验证。

覆盖：
- 中文子串/关键词可被 FTS 检索到（含“泡泡玛特”场景）；
- trigram 对 <3 字符的查询自动回退 LIKE，仍可用；
- 被拒绝（rejected）的 AI 候选项不进入搜索结果（防止编造内容被搜到）；
- 真实转写片段中的中文可被搜索定位并带上下文。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from backend import db


def _setup(fresh_app):
    sid = db.create_semester("2026秋", "2026-09-07", "2027-01-15")
    db.set_active_semester(sid)
    cid = db.create_course(sid, "商业分析")
    sess = db.get_or_create_session(cid, "2026-10-20")
    segs = [
        {"start_seconds": 0.0, "end_seconds": 4.0,
         "text": "今天我们聊聊泡泡玛特这类潮玩品牌的营销策略。"},
        {"start_seconds": 4.0, "end_seconds": 8.0,
         "text": "期中考试的考点集中在第三到第五章。"},
    ]
    db.replace_segments(sess, segs)
    return sid, cid, sess


def test_fts_finds_chinese_substring(fresh_app):
    sid, cid, sess = _setup(fresh_app)
    db.index_session(sess)
    hits = db.fts_search("泡泡玛特")
    assert any(h["row_type"] == "segment" for h in hits), "中文子串（泡泡玛特）必须可被 FTS 检索"
    assert any("泡泡玛特" in h["snippet_text"] for h in hits)


def test_fts_trigram_short_query_falls_back_to_like(fresh_app):
    """trigram 分词器要求查询 >=3 字符；不足时回退 LIKE，仍可命中。"""
    sid, cid, sess = _setup(fresh_app)
    db.index_session(sess)
    # “潮玩”仅 2 字 -> 走 LIKE 回退
    hits = db.fts_search("潮玩")
    assert any("潮玩" in h["snippet_text"] for h in hits), "2 字查询应回退 LIKE 命中"


def test_fts_long_query_uses_trigram(fresh_app):
    sid, cid, sess = _setup(fresh_app)
    db.index_session(sess)
    hits = db.fts_search("期中考试")
    assert any(h["row_type"] == "segment" for h in hits)
    assert any("期中考试" in h["snippet_text"] for h in hits)


def test_rejected_items_excluded_from_search(fresh_app):
    """被拒绝的 AI 候选项（如编造的泡泡玛特促销）不应出现在搜索结果中。"""
    sid, cid, sess = _setup(fresh_app)
    db.replace_items(sess, [
        {"course_id": cid, "category": "activity", "title": "真实讲座", "confidence": "explicit",
         "verification_status": "confirmed", "context_type": "confirmed_announcement",
         "rejection_reason": None, "evidence_matched": 1},
        {"course_id": cid, "category": "activity",
         "title": "【编造】购买泡泡玛特盲盒享八折优惠", "details": "品牌赞助",
         "confidence": "explicit", "verification_status": "rejected", "context_type": "unclear",
         "rejection_reason": "evidence_not_found_in_source", "evidence_matched": 0},
    ])
    db.index_session(sess)
    hits = db.fts_search("泡泡玛特")
    # 真实转写里有“泡泡玛特” -> 命中 segment；但被拒绝的事项不应作为 item 命中
    item_hits = [h for h in hits if h["row_type"] == "item"]
    assert not any("八折优惠" in h["snippet_text"] for h in item_hits), \
        "被拒绝的编造事项不得进入搜索结果"


def test_search_filter_by_course(fresh_app):
    sid, cid, sess = _setup(fresh_app)
    db.index_session(sess)
    other = db.create_course(db.get_active_semester()["id"], "其它课")
    hits = db.fts_search("泡泡玛特", {"course_id": other})
    assert hits == [], "按课程筛选应排除其它课程的命中"
    hits2 = db.fts_search("泡泡玛特", {"course_id": cid})
    assert hits2, "按课程筛选应命中本课内容"
