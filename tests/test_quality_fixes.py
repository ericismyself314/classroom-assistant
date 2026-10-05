import json
from types import SimpleNamespace

import pytest

from backend import db, important, note_editing
from backend.ai import coverage, segment_analysis, note_quality, whisper_worker


def seg(text, i=0):
    return {'text': text, 'original_text': text, 'start_seconds': i*5, 'end_seconds': i*5+5,
            'is_uncertain': False, 'language': 'en'}


class Reply:
    def __init__(self, response): self.response = response
    def chat(self, *a, **k): return json.dumps(self.response, ensure_ascii=False)


import re as _re

class ChunkDroppingReply:
    """模拟本地小模型：多段整块翻译时漏掉最后一条，但逐段补翻时能翻出。"""
    def chat(self, system, user, json_mode=False, temperature=0):
        idxs = [int(m) for m in _re.findall(r'\[(\d+)\]', user)]
        keep = idxs[:1] if len(idxs) > 1 else idxs
        translations = [{'index': i, 'text': f'中文{i}'} for i in keep]
        return json.dumps({'translations': translations}, ensure_ascii=False)


class EmptyReply:
    """模拟模型完全翻不出任何内容。"""
    def chat(self, *a, **k): return json.dumps({'translations': []}, ensure_ascii=False)


def test_english_transcript_gets_chinese_display_without_overwriting_source():
    source = [seg('The final exam is next Friday.'), seg('Please submit the report online.', 1)]
    reply = Reply({'translations': [
        {'index': 0, 'text': '期末考试在下周五举行。'},
        {'index': 1, 'text': '请在线提交报告。'},
    ]})
    display = segment_analysis.translate_for_display(reply, source, {'transcript_language': 'en'})
    assert source[0]['text'] == 'The final exam is next Friday.'
    assert [s['text'] for s in display] == ['期末考试在下周五举行。', '请在线提交报告。']
    assert [s['display_text'] for s in display] == ['期末考试在下周五举行。', '请在线提交报告。']


def test_incomplete_chunk_translates_all_via_per_segment_retry():
    # 本地模型整块翻译漏掉一条，但逐段补翻能翻出 -> 整节课不再因少一条而失败。
    source = [seg('The final exam is next Friday.'), seg('Please submit the report online.', 1)]
    display = segment_analysis.translate_for_display(ChunkDroppingReply(), source, {'transcript_language': 'en'})
    assert source[0]['text'] == 'The final exam is next Friday.'          # 原始 ASR 不被覆盖
    assert display[0]['display_text'] is not None
    assert display[1]['display_text'] is not None                          # 逐段补翻补齐
    assert display[1]['text'] == display[1]['display_text']               # 展示中文


def test_untranslatable_segment_falls_back_to_english():
    # 真正翻不出时，仅该段回退英文原文（display_text=None），其余段不受影响、整节课不报错。
    source = [seg('The final exam is next Friday.'), seg('Please submit the report online.', 1)]
    display = segment_analysis.translate_for_display(EmptyReply(), source, {'transcript_language': 'en'})
    assert display[0]['text'] == source[0]['text']
    assert display[0]['display_text'] is None
    assert display[1]['display_text'] is None


def test_qwen_coarse_result_is_split_for_translation_and_evidence():
    text = ("This is a complete classroom sentence. " * 30).strip()
    parts = whisper_worker.split_qwen_segment(text, 0, 120)
    assert len(parts) > 1
    assert all(0 < len(part['text']) <= 240 for part in parts)
    assert parts[0]['start_seconds'] == 0
    assert parts[-1]['end_seconds'] == 120
    assert all(a['end_seconds'] <= b['start_seconds'] for a, b in zip(parts, parts[1:]))


def test_display_text_is_persisted_separately_from_asr_source():
    sid, row = setup_class()
    db.update_segment_display_texts(sid, [{**row, 'display_text': '债券价格'}])
    saved = db.list_segments(sid)[0]
    assert saved['text'] == 'bond price'
    assert saved['display_text'] == '债券价格'


def test_missing_textbook_requirement_is_recovered_with_exact_source():
    s = [seg('The slides follow the textbook. You do not need to buy the textbook.')]
    reply = Reply({'decisions': [{'segment_id': 0, 'status': 'new', 'item': {
        'title': '无需购买教材', 'details': '课件跟随教材，无需另买教材。', 'confidence': 'explicit',
        'category': 'other', 'evidence_segment_ids': [0]}}]})
    items, pending, counts = coverage.audit_requirements(reply, s, [], {'session_date': '2026-09-01'}, segment_analysis._fmt_segments)
    assert not pending and len(items) == 1 and '无需' in items[0]['details']
    assert items[0]['evidence'][0]['quote'] == s[0]['text']
    assert counts['recovered_items'] == 1


def test_audit_failure_keeps_unresolved_requirement_visible():
    s = [seg('Submit the report by Friday.')]
    items, pending, counts = coverage.audit_requirements(Reply({}), s, [], {'session_date': '2026-09-01'}, segment_analysis._fmt_segments)
    assert not items and pending[0]['summary'] == s[0]['text']
    assert counts['pending_passages'] == 1


def test_no_action_exemption_is_not_discarded_as_no_requirement():
    s = [seg('You do not need to buy the textbook.')]
    response = {'decisions': [{'segment_id': 0, 'status': 'not_requirement', 'reason': '无需行动',
        'item': {'title': '无需买教材', 'details': '不需要买教材。', 'confidence': 'explicit', 'evidence_segment_ids': [0]}}]}
    items, pending, _ = coverage.audit_requirements(Reply(response), s, [], {'session_date': '2026-09-01'}, segment_analysis._fmt_segments)
    assert items[0]['confidence'] == 'uncertain' and items[0]['evidence'][0]['quote'] == s[0]['text']


def test_exam_signal_cannot_disappear_on_negative_model_decision():
    s = [seg('The final examination covers options.')]
    response = {'decisions': [{'segment_id': 0, 'status': 'not_requirement', 'reason': '没有任务'}]}
    items, pending, _ = coverage.audit_requirements(Reply(response), s, [], {'session_date': '2026-09-01'}, segment_analysis._fmt_segments)
    assert not items and len(pending) == 1


def test_audit_rejects_invented_evidence_and_false_covered_index():
    s = [seg('Submit the report by Friday.')]
    for decision in [{'segment_id': 0, 'status': 'new', 'item': {'title': '作业', 'evidence_segment_ids': [99]}},
                     {'segment_id': 0, 'status': 'covered', 'existing_index': 99}]:
        items, pending, _ = coverage.audit_requirements(Reply({'decisions': [decision]}), s, [],
                                                       {'session_date': '2026-09-01'}, segment_analysis._fmt_segments)
        assert not items and len(pending) == 1


def test_context_includes_later_exception_with_global_ids():
    s = [seg('Previous topic.'), seg('Submit the report by Friday.', 1), seg('Except group B, whose deadline is Monday.', 2)]
    lo, hi = coverage.context_window(s, 1, 2)
    formatted = segment_analysis._fmt_segments(s[lo:hi], lo)
    assert '[2]' in formatted and 'Except group B' in formatted


def test_merging_preserves_conditions_and_separate_dates():
    base = {'category': 'assignment', 'title': '报告', 'confidence': 'explicit', 'evidence': []}
    out = important.merge_items([{**base, 'details': '周五交，A组必须交。'}, {**base, 'details': 'B组不用交。'}])
    assert len(out) == 1 and 'B组不用交' in out[0]['details'] and 'A组必须交' in out[0]['details']
    assert len(important.merge_items([{**base, 'raw_date_text': '周五'}, {**base, 'raw_date_text': '下周一'}])) == 2


def test_uncertainty_and_nonrepeating_notes():
    assert whisper_worker.segment_is_uncertain(SimpleNamespace(avg_logprob=-0.95))
    assert whisper_worker.segment_is_uncertain(SimpleNamespace(compression_ratio=2.8))
    assert not whisper_worker.segment_is_uncertain(SimpleNamespace(avg_logprob=-0.1))
    assert note_quality._same_note('老师明确说明了无需买教材。', '无需买教材。')
    assert not note_quality._same_note('需要买教材。', '无需买教材。')
    s = [seg('Unintelligible technical word.')]
    issues = coverage.transcript_issues([{'segment_id': 0, 'reason': '术语不通顺'}], s, 0, 1)
    assert issues and s[0]['is_uncertain'] and issues[0]['evidence'][0]['uncertain']


def setup_class():
    sem = db.create_semester('验证', '2026-09-01', '2027-01-01')
    course = db.create_course(sem, 'Finance')
    sid = db.get_or_create_session(course, '2026-09-01')
    db.replace_segments(sid, [seg('bond price')])
    chapter = {'title': '债券', 'summary': '债券价格', 'points': [], 'evidence_segment_ids': ['0']}
    db.save_analysis(sid, [chapter], [], {'chapters': [chapter], 'review': []}, 'test')
    return sid, db.list_segments(sid)[0]


def test_correction_keeps_original_history_and_invalidates_obsolete_summary():
    sid, s = setup_class()
    note_editing.correct_segment(s['id'], {'text': 'bond prices', 'expected_text': s['text']})
    fixed = db.list_segments(sid)[0]
    assert fixed['original_text'] == 'bond price' and fixed['text'] == 'bond prices'
    assert len(db.list_revisions(sid)) == 1 and not db.list_topics(sid)
    assert db.get_session(sid)['status'] == 'needs_analysis'
    old = json.loads(db.read_conn().execute('SELECT payload FROM session_revisions WHERE session_id=?', (sid,)).fetchone()[0])
    assert old['topics'][0]['summary'] == '债券价格'


def test_edit_conflict_and_active_job_do_not_overwrite():
    sid, s = setup_class()
    with pytest.raises(ValueError, match='变化'):
        note_editing.correct_segment(s['id'], {'text': 'new', 'expected_text': 'old'})
    db.create_job(sid, 'analyze', status='pending')
    with pytest.raises(ValueError, match='正在处理'):
        note_editing.correct_segment(s['id'], {'text': 'new', 'expected_text': s['text']})
    assert db.list_segments(sid)[0]['text'] == 'bond price' and not db.list_revisions(sid)


def test_manual_note_is_labelled_and_search_data_updated():
    sid, _ = setup_class()
    note_editing.correct_chapter(sid, 0, {'title': '债券价格', 'summary': '利率变化影响价格。', 'points': [],
        'expected_title': '债券', 'expected_summary': '债券价格', 'expected_points': []})
    notes = json.loads(db.get_session(sid)['notes_data'])
    assert notes['chapters'][0]['manual_edit'] and len(db.list_revisions(sid)) == 1
    assert db.list_topics(sid)[0]['summary'] == '利率变化影响价格。'


def test_restart_repairs_previously_orphaned_session():
    sid, _ = setup_class()
    db.update_session(sid, status='transcribing')
    db.create_job(sid, 'transcribe', status='failed')
    db.mark_interrupted_jobs()
    assert db.get_session(sid)['status'] == 'failed'
    assert db.list_segments(sid)[0]['text'] == 'bond price'
