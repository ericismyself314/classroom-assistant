"""单元测试：相对日期、周次/课表、schema 校验、去重合并、路径安全、哈希。"""
import os
from datetime import date

from backend import dateparse, schedule, schemas, important, paths
from backend import hashutil


def test_parse_absolute_chinese():
    base = date(2026, 9, 2)
    assert dateparse.parse_relative_date("十月二十日", base) == "2026-10-20"
    assert dateparse.parse_relative_date("期中考试在10月20日", base) == "2026-10-20"


def test_parse_relative_week():
    # 2026-09-02 是周三；下周五应为 2026-09-04 (本周五) 之后一周 = 2026-09-11
    base = date(2026, 9, 2)
    assert dateparse.parse_relative_date("下周五", base) == "2026-09-11"
    assert dateparse.parse_relative_date("明天", base) == "2026-09-03"
    assert dateparse.parse_relative_date("后天", base) == "2026-09-04"


def test_parse_unreliable_returns_none():
    base = date(2026, 9, 2)
    # 仅“周X”且已过去、非明确“下周” -> 保守返回 None
    assert dateparse.parse_relative_date("周一", date(2026, 9, 2)) is None
    assert dateparse.parse_relative_date("随便说说的日期", base) is None


def test_week_number_monday_based():
    # 学期从 2026-09-07（周一）开始，第1周
    assert schedule.week_number("2026-09-07", "2026-09-07") == 1
    assert schedule.week_number("2026-09-07", "2026-09-14") == 2
    # 跨周计算正确
    plan = schedule.build_week_plan(
        {"start_date": "2026-09-07", "end_date": "2027-01-15"},
        [],
        date(2026, 9, 9),
    )
    assert len(plan) == 7
    assert plan[0]["weekday"] == 0  # 周一


def test_occurrences_respects_recurrence():
    slots = [{"weekday": 2, "start_time": "10:00", "end_time": "11:40",
              "recurrence_start": "2026-09-01", "recurrence_end": "2026-09-30"}]
    # 2026-09-02 是周三(weekday 2)，在范围内
    assert len(schedule.occurrences_for_date(slots, date(2026, 9, 2), "2026-09-01", "2027-01-15")) == 1
    # 超出 recurrence_end
    assert len(schedule.occurrences_for_date(slots, date(2026, 10, 7), "2026-09-01", "2027-01-15")) == 0


def test_normalize_items_enum_coercion():
    # 受控枚举为英文；模型被要求输出英文 category/confidence
    raw = '{"items":[{"category":"exam","title":"期中","confidence":"explicit","raw_date_text":"十月二十日"}]}'
    items, err = schemas.normalize_items(raw, "2026-09-09")
    assert err is None
    assert items[0]["category"] == "exam"
    assert items[0]["confidence"] == "explicit"


def test_normalize_items_invalid_category_to_other():
    raw = '{"items":[{"category":"神秘分类","title":"某事项"}]}'
    items, _ = schemas.normalize_items(raw, "2026-09-09")
    assert items[0]["category"] == "other"


def test_normalize_items_rejects_non_json():
    out, err = schemas.normalize_items("这不是json", "2026-09-09")
    assert out is None and err is not None


def test_apply_date_normalization_real_code():
    items = [{"category": "exam", "title": "期中", "raw_date_text": "十月二十日", "normalized_date": None}]
    res = schemas.apply_date_normalization(items, "2026-09-09")
    assert res[0]["normalized_date"] == "2026-10-20"


def test_merge_dedup_keeps_highest_confidence():
    items = [
        {"category": "assignment", "title": "作业1", "confidence": "probable",
         "evidence": [{"segment_id": "1", "quote": "a"}]},
        {"category": "assignment", "title": "作业1", "confidence": "explicit",
         "evidence": [{"segment_id": "2", "quote": "b"}]},
    ]
    merged = important.merge_items(items)
    assert len(merged) == 1
    assert merged[0]["confidence"] == "explicit"
    assert len(merged[0]["evidence"]) == 2  # 证据合并去重


def test_safe_join_blocks_traversal():
    root = "/data/models"
    want = os.path.abspath(os.path.join(root, "a/b.gguf"))
    assert paths.safe_join(root, "a/b.gguf") == want
    try:
        paths.safe_join(root, "../etc/passwd")
        assert False, "应检测到路径穿越"
    except ValueError:
        pass


def test_sha256_and_audio_support(tmp_path):
    f = tmp_path / "x.m4a"
    f.write_bytes(b"hello")
    h = hashutil.sha256_file(str(f))
    assert isinstance(h, str) and len(h) == 64
    assert hashutil.is_supported_audio("a.mp3")
    assert not hashutil.is_supported_audio("a.exe")
