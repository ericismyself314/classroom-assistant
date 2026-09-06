"""集成测试：用可控 Provider 跑通真实流水线（转写→分析→保存→索引）。

说明：这里用 FakeLLM 替代真实大模型，仅用于验证「流水线 plumbing」——
schema 校验、去重合并、相对日期归一化、FTS 索引、数据库落库、以及
「绝不把学生提问/举例当作确定通知」的存储层保证。真实大模型智能在
tests/e2e_real.py 中验证（需本机已下载模型）。
"""
import json
import os
import zipfile

from backend import db, schedule, pipeline as pipeline_mod
from backend.ai import provider as provider_mod
from backend import backup as backup_mod


ACCEPTANCE_TRANSCRIPT = [
    "今天我们主要介绍消费者购买决策。",
    "期中考试安排在十月二十日，范围是第一至第四周。",
    "第二次作业不用提交。",
    "下周课程换到H302。",
    "星期五有一场投资讲座，自愿参加。",
]

ANTI1 = ["有同学问第二次作业是不是不用交，老师没有回答。"]
ANTI2 = ["比如考试可能会安排在十月，但这只是举例，不是我们的考试安排。"]


def _segments(texts):
    out = []
    t = 0.0
    for i, txt in enumerate(texts):
        dur = max(2.0, len(txt) * 0.25)
        out.append({"start_seconds": round(t, 1), "end_seconds": round(t + dur, 1),
                    "text": txt, "language": "zh", "is_uncertain": False})
        t += dur
    return out


ACCEPTANCE_JSON = json.dumps({
    "topics": [{"title": "消费者购买决策", "summary": "介绍消费者从需求到购买后的决策过程。"}],
    "items": [
        {"category": "exam", "title": "期中考试", "details": "范围第一至第四周",
         "raw_date_text": "十月二十日", "confidence": "explicit",
         "evidence": [{"quote": "期中考试安排在十月二十日，范围是第一至第四周。"}]},
        {"category": "assignment", "title": "第二次作业取消", "details": "不用提交",
         "confidence": "explicit", "evidence": [{"quote": "第二次作业不用提交。"}]},
        {"category": "schedule_change", "title": "下周教室换到H302", "details": "",
         "confidence": "explicit", "evidence": [{"quote": "下周课程换到H302。"}]},
        {"category": "activity", "title": "星期五投资讲座", "details": "自愿参加",
         "confidence": "explicit", "evidence": [{"quote": "星期五有一场投资讲座，自愿参加。"}]},
    ],
}, ensure_ascii=False)


class FakeLLM:
    name = "fake"

    def available(self):
        return True

    def model_label(self):
        return "FakeLLM"

    def chat(self, system, user, json_mode=True, temperature=0.2):
        if "合并去重" in user:
            # 最终合并：直接回传草稿中的 items 数组
            s, e = user.find("["), user.rfind("]")
            try:
                items = json.loads(user[s:e + 1]) if s != -1 else []
            except Exception:
                items = []
            return json.dumps({"items": items}, ensure_ascii=False)
        if "消费者购买决策" in user:
            return ACCEPTANCE_JSON
        if "老师没有回答" in user or "第二次作业是不是不用交" in user:
            return json.dumps({"topics": [{"title": "课堂问答"}], "items": []}, ensure_ascii=False)
        if "只是举例" in user or "举例，不是我们的考试安排" in user:
            return json.dumps({"topics": [{"title": "举例说明"}], "items": []}, ensure_ascii=False)
        return json.dumps({"topics": [], "items": []}, ensure_ascii=False)


def _setup_session(monkeypatch, texts, session_date="2026-09-09"):
    monkeypatch.setattr(provider_mod, "build_provider", lambda s: FakeLLM())
    # pipeline.py 用 `from .ai.provider import build_provider` 把名字绑定进了自己的命名空间，
    # 只 patch provider 模块不够，必须同时 patch pipeline 里的同名绑定。
    monkeypatch.setattr(pipeline_mod, "build_provider", lambda s: FakeLLM())
    sid = db.create_semester("2026秋", "2026-09-07", "2027-01-15")
    db.set_active_semester(sid)
    cid = db.create_course(sid, "市场营销", code="MKT101", location="H201")
    db.replace_slots(cid, [{"weekday": 2, "start_time": "10:00", "end_time": "11:40",
                            "location": "H201", "recurrence_start": "2026-09-07",
                            "recurrence_end": "2027-01-15"}])
    sess = db.get_or_create_session(cid, session_date, title="第3周")
    db.replace_segments(sess, _segments(texts))
    return sess


def test_acceptance_extraction(monkeypatch):
    sess = _setup_session(monkeypatch, ACCEPTANCE_TRANSCRIPT)
    job_id = db.create_job(sess, "import", status="running")
    res = pipeline_mod.run_pipeline(sess, mode="import", settings={}, job_id=job_id)
    assert res["status"] == "completed"
    assert res["topics"] == 1
    assert res["items"] == 4

    topics = db.list_topics(sess)
    assert any(t["title"] == "消费者购买决策" for t in topics)

    items = db.list_items({"session_id": sess})
    cats = {it["category"] for it in items}
    assert {"exam", "assignment", "schedule_change", "activity"} <= cats
    exam = next(it for it in items if it["category"] == "exam")
    assert exam["normalized_date"] == "2026-10-20"  # 真实相对日期归一化代码生效
    # 证据不应在入库时被丢弃（此前 evidence_data 字段名 bug 会丢证据）
    assert exam.get("evidence_data"), "重要事项证据必须持久化"
    job = db.get_job(job_id)
    assert job["status"] == "completed"


def test_anti_case_question_not_confirmed(monkeypatch):
    """学生提问但老师未确认 -> 不应产生任何重要事项。"""
    sess = _setup_session(monkeypatch, ANTI1)
    job_id = db.create_job(sess, "import", status="running")
    res = pipeline_mod.run_pipeline(sess, mode="import", settings={}, job_id=job_id)
    assert res["status"] == "completed"
    items = db.list_items({"session_id": sess})
    assert items == [], "学生未确认的提问不应被当作通知"
    assert not any("第二次作业不用交" in (it.get("title", "") + it.get("details", "")) for it in items)


def test_anti_case_example_not_exam(monkeypatch):
    """老师明确说“只是举例” -> 不应产生考试事项。"""
    sess = _setup_session(monkeypatch, ANTI2)
    job_id = db.create_job(sess, "import", status="running")
    res = pipeline_mod.run_pipeline(sess, mode="import", settings={}, job_id=job_id)
    assert res["status"] == "completed"
    items = db.list_items({"session_id": sess})
    assert not any(it["category"] == "exam" for it in items), "举例不应被当作真实考试"


def test_fts_search_finds_items(monkeypatch):
    sess = _setup_session(monkeypatch, ACCEPTANCE_TRANSCRIPT)
    job_id = db.create_job(sess, "import", status="running")
    pipeline_mod.run_pipeline(sess, mode="import", settings={}, job_id=job_id)
    hits = db.fts_search("投资")
    assert any(h["row_type"] == "item" for h in hits)
    hits2 = db.fts_search("期中考试")
    assert hits2, "FTS 应能搜到转写/事项中的中文子串"


def test_job_interrupted_recovery():
    """重启恢复：运行中的任务应被标记为失败（中断）。"""
    sid = db.create_semester("2026秋", "2026-09-07", "2027-01-15")
    db.set_active_semester(sid)
    cid = db.create_course(sid, "市场营销")
    sess = db.get_or_create_session(cid, "2026-09-09")
    jid = db.create_job(sess, "import", status="running")
    db.mark_interrupted_jobs()
    assert db.get_job(jid)["status"] == "failed"


HALLUC_TRANSCRIPT = [
    "今天我们讲第三章消费者行为。",
    "期中考试安排在十月二十日，范围第一到第四周。",
]

HALLUC_JSON = json.dumps({
    "topics": [{"title": "消费者行为"}],
    "items": [
        {"category": "exam", "title": "期中考试", "details": "范围第一到第四周",
         "raw_date_text": "十月二十日", "confidence": "explicit",
         "evidence": [{"quote": "期中考试安排在十月二十日，范围第一到第四周。"}]},
        {"category": "activity", "title": "购买泡泡玛特盲盒享八折", "details": "品牌方赞助",
         "confidence": "explicit",
         "evidence": [{"quote": "品牌方赞助大家购买泡泡玛特盲盒享八折优惠"}]},
    ],
}, ensure_ascii=False)


class FakeLLMHalluc:
    name = "fake"
    model_label = lambda self: "FakeLLMHalluc"

    def available(self):
        return True

    def chat(self, system, user, json_mode=True, temperature=0.2):
        if "合并去重" in user:
            s, e = user.find("["), user.rfind("]")
            try:
                items = json.loads(user[s:e + 1]) if s != -1 else []
            except Exception:
                items = []
            return json.dumps({"items": items}, ensure_ascii=False)
        if "消费者行为" in user:
            return HALLUC_JSON
        return json.dumps({"topics": [], "items": []}, ensure_ascii=False)


def test_pipeline_verification_rejects_hallucination(monkeypatch):
    """流水线层面的 5 层核查：模型返回的编造事项（原文找不到证据）必须被拒绝并默认隐藏。"""
    monkeypatch.setattr(provider_mod, "build_provider", lambda s: FakeLLMHalluc())
    monkeypatch.setattr(pipeline_mod, "build_provider", lambda s: FakeLLMHalluc())
    sid = db.create_semester("2026秋", "2026-09-07", "2027-01-15")
    db.set_active_semester(sid)
    cid = db.create_course(sid, "市场营销")
    sess = db.get_or_create_session(cid, "2026-09-09")
    db.replace_segments(sess, _segments(HALLUC_TRANSCRIPT))
    job_id = db.create_job(sess, "import", status="running")
    res = pipeline_mod.run_pipeline(sess, mode="import", settings={}, job_id=job_id)
    assert res["status"] == "completed"

    default = db.list_items({"session_id": sess})
    # 真实考试事项应保留（且为 confirmed）
    assert any(it["title"] == "期中考试" for it in default)
    exam = next(it for it in default if it["title"] == "期中考试")
    assert exam["verification_status"] == "confirmed"

    # 编造的泡泡玛特事项应被拒绝且默认不出现在列表中
    assert "购买泡泡玛特盲盒享八折" not in {it["title"] for it in default}

    # 但 include_rejected=1 时仍可见（保留调试），且状态为 rejected
    incl = db.list_items({"session_id": sess, "include_rejected": "1"})
    rejected = [it for it in incl if it["verification_status"] == "rejected"]
    assert any(it["title"] == "购买泡泡玛特盲盒享八折" for it in rejected)
    assert rejected[0]["rejection_reason"] == "evidence_not_found_in_source"


def test_backup_restore_roundtrip(monkeypatch):
    """备份 ZIP 应包含数据库与设置；恢复后事项数量回到备份时状态。"""
    sess = _setup_session(monkeypatch, ACCEPTANCE_TRANSCRIPT)
    job_id = db.create_job(sess, "import", status="running")
    pipeline_mod.run_pipeline(sess, mode="import", settings={}, job_id=job_id)

    before = db.list_items({"session_id": sess})
    assert len(before) == 4

    path = backup_mod.create_backup(include_audio=True)
    assert os.path.isfile(path)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    assert "settings.json" in names
    assert any(n.endswith("classroom-assistant.db") for n in names)

    # 模拟数据丢失
    for it in before:
        db.delete_item(it["id"])
    db.close_all_connections()
    assert db.list_items({"session_id": sess}) == []

    # 恢复
    res = backup_mod.restore_backup(path)
    assert os.path.exists(res["snapshot"])
    after = db.list_items({"session_id": sess})
    assert len(after) == 4, "恢复后重要事项应回到 4 条"
