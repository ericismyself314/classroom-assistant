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


def test_truncated_translation_keeps_complete_rows_and_retries_only_missing():
    class TruncatingReply:
        def __init__(self): self.calls = 0
        def chat(self, system, user, json_mode=False, temperature=0):
            self.calls += 1
            ids = [int(i) for i in _re.findall(r'\[(\d+)\]', user)]
            if len(ids) > 1:
                rows = [{'index': i, 'text': f'translation {i}'} for i in ids[:-2]]
                return json.dumps({'translations': rows})[:-2] + ',{"index":18,"text":"unfinished'
            return json.dumps({'translations': [{'index': ids[0], 'text': f'translation {ids[0]}'}]})

    source = [seg(f'Synthetic lesson sentence number {i}.', i) for i in range(20)]
    provider = TruncatingReply()
    out = segment_analysis.translate_for_display(provider, source, {'transcript_language': 'en'})
    assert [s['display_text'] for s in out] == [f'translation {i}' for i in range(20)]
    assert provider.calls == 3


def test_truncated_translation_retries_ambiguous_duplicate_and_incomplete_entries():
    class AmbiguousReply:
        def __init__(self): self.calls = 0
        def chat(self, system, user, json_mode=False, temperature=0):
            self.calls += 1
            ids = [int(i) for i in _re.findall(r'\[(\d+)\]', user)]
            if len(ids) > 1:
                return '{"translations":[{"index":0,"text":"wrong A"},{"index":0,"text":"wrong B"},{"index":1,"text":"correct 1"},{"index":2,"text":"incomplete'
            return json.dumps({'translations': [{'index': ids[0], 'text': f'correct {ids[0]}'}]})

    source = [seg(f'Synthetic lesson sentence {i}.', i) for i in range(3)]
    provider = AmbiguousReply()
    out = segment_analysis.translate_for_display(provider, source, {'transcript_language': 'en'})
    assert [s['display_text'] for s in out] == ['correct 0', 'correct 1', 'correct 2']
    assert provider.calls == 3


def test_translation_chunk_uses_local_indexes_and_maps_to_global_positions():
    class LocalIndexReply:
        def __init__(self): self.calls = 0
        def chat(self, system, user, json_mode=False, temperature=0):
            self.calls += 1
            indexes = [int(i) for i in _re.findall(r'\[(\d+)\]', user)]
            assert indexes == [0, 1]
            return json.dumps({'translations': [
                {'index': 0, 'text': '第一段'}, {'index': 1, 'text': '第二段'}
            ]}, ensure_ascii=False)

    provider = LocalIndexReply()
    result = segment_analysis._translate_chunk(provider, [seg('first'), seg('second')], offset=37)
    assert result == {37: '第一段', 38: '第二段'}
    assert provider.calls == 1


def test_translation_chunk_partial_recovery_maps_only_unambiguous_local_indexes():
    class PartialReply:
        def chat(self, *args, **kwargs):
            return ('{"translations":[{"index":0,"text":"wrong A"},'
                    '{"index":0,"text":"wrong B"},{"index":1,"text":"correct"},'
                    '{"index":2,"text":"unfinished')

    result = segment_analysis._translate_chunk(PartialReply(), [seg('a'), seg('b'), seg('c')], offset=37)
    assert result == {38: 'correct'}


def test_complete_translation_with_duplicate_indexes_is_retranslated():
    rows = {'translations': [
        {'index': 0, 'text': 'wrong A'}, {'index': 0, 'text': 'wrong B'},
        {'index': 1, 'text': 'correct'},
    ]}
    assert segment_analysis._translation_rows(rows) == {1: 'correct'}


def test_missing_translations_are_retried_in_a_batch_with_original_mapping():
    class PartiallyDroppingReply:
        def __init__(self): self.calls = 0
        def chat(self, system, user, json_mode=False, temperature=0):
            self.calls += 1
            matches = _re.findall(r'\[(\d+)\][^\n]*sentence (\d+)', user)
            rows = [{'index': int(index), 'text': f'译文{source}'}
                    for index, source in matches if self.calls > 1 or int(source) % 2 == 0]
            return json.dumps({'translations': rows}, ensure_ascii=False)
    source = [seg(f'Synthetic sentence {i}.', i) for i in range(8)]
    provider = PartiallyDroppingReply()
    out = segment_analysis.translate_for_display(provider, source, {'transcript_language': 'en'})
    assert [s['display_text'] for s in out] == [f'译文{i}' for i in range(8)]
    assert [s['original_text'] for s in out] == [s['original_text'] for s in source]
    assert provider.calls == 2


def test_failed_batch_retry_preserves_individual_fallback_and_source():
    class GroupThenIndividualReply:
        def __init__(self): self.calls = 0
        def chat(self, system, user, json_mode=False, temperature=0):
            self.calls += 1
            matches = _re.findall(r'\[(\d+)\][^\n]*sentence (\d+)', user)
            if self.calls == 1:
                return json.dumps({'translations': [{'index': 0, 'text': '译文0'}]})
            if len(matches) > 1:
                return '{bad json'
            index, source = matches[0]
            if source == '3':
                return '{bad json'
            return json.dumps({'translations': [{'index': int(index), 'text': f'译文{source}'}]})
    source = [seg(f'Synthetic sentence {i}.', i) for i in range(4)]
    out = segment_analysis.translate_for_display(GroupThenIndividualReply(), source, {'transcript_language': 'en'})
    assert [s['display_text'] for s in out] == ['译文0', '译文1', '译文2', None]
    assert out[3]['text'] == source[3]['text']


def test_batch_retry_rejects_duplicate_and_out_of_range_indices():
    class AmbiguousBatchReply:
        def __init__(self): self.calls = 0
        def chat(self, system, user, json_mode=False, temperature=0):
            self.calls += 1
            matches = _re.findall(r'\[(\d+)\][^\n]*sentence (\d+)', user)
            if self.calls == 1:
                return json.dumps({'translations': [{'index': 0, 'text': '译文0'}]})
            if len(matches) > 1:
                return json.dumps({'translations': [
                    {'index': 0, 'text': 'wrong A'}, {'index': 0, 'text': 'wrong B'},
                    {'index': 1, 'text': '译文2'}, {'index': 99, 'text': 'wrong C'}]})
            index, source = matches[0]
            return json.dumps({'translations': [{'index': int(index), 'text': f'译文{source}'}]})
    source = [seg(f'Synthetic sentence {i}.', i) for i in range(4)]
    out = segment_analysis.translate_for_display(AmbiguousBatchReply(), source, {'transcript_language': 'en'})
    assert [s['display_text'] for s in out] == ['译文0', '译文1', '译文2', '译文3']


def test_batch_retry_nonzero_chunk_offset_preserves_corrections_and_negations(monkeypatch):
    sentences = [
        ('Bond prices fall when interest rates rise.', '利率上升时债券价格下跌。'),
        ('Compare two bonds for your assignment.', '作业要求比较两只债券。'),
        ('Submit the PDF by November 20.', '请在11月20日前提交PDF。'),
        ('The report due tomorrow is only an example.', '明天交报告只是举例。'),
        ('I have not announced any quiz.', '我没有宣布任何测验。'),
        ('Each student must upload a copy.', '每位学生必须上传一份。'),
        ('Correction: deadline November 23, not November 20.', '更正：截止日期是11月23日，不是11月20日。'),
    ]
    source = [seg(text, i) for i, (text, _) in enumerate(sentences)]
    translations = dict(sentences)
    monkeypatch.setattr(segment_analysis, 'split_segments', lambda items, max_chars: [items[:2], items[2:]])
    class PartialBatchReply:
        def __init__(self): self.calls = 0
        def chat(self, system, user, json_mode=False, temperature=0):
            self.calls += 1
            rows = []
            for local, text in _re.findall(r'\[(\d+)\]\s*\([^\n]*?\)\s*([^\n]+)', user):
                index = int(local)
                if self.calls == 2 and index not in (0, 3):
                    continue
                if self.calls == 3 and index == 2:
                    continue
                rows.append({'index': index, 'text': translations[text]})
            return json.dumps({'translations': rows}, ensure_ascii=False)
    provider = PartialBatchReply()
    out = segment_analysis.translate_for_display(provider, source, {'transcript_language': 'en'})
    assert [s['display_text'] for s in out] == [text for _, text in sentences]
    assert [(s['start_seconds'], s['end_seconds']) for s in out] == [
        (s['start_seconds'], s['end_seconds']) for s in source]
    assert provider.calls == 4
