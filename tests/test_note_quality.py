"""Focused quality regressions, standalone and independent of real user data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from backend.ai import note_quality as q, whisper_worker as w, segment_analysis as a, prompts

SEGMENTS = [{"text": "Bond prices fall when interest rates rise.", "start_seconds": 12, "end_seconds": 20}]
TOPIC = {"title": "债券价格与利率", "summary": "利率上升时债券价格下跌。", "evidence": [
    {"segment_id": "0", "quote": "Bond prices fall when interest rates rise."}]}

class Reviewer:
    def chat(self, *args, **kw):
        return '{"supported_ids":["0"],"groups":[[0,1]]}'

def test_language_settings():
    assert w.recognition_options('en')['language'] == 'en'
    assert w.recognition_options('zh')['language'] == 'zh'
    assert w.recognition_options('mix')['multilingual'] is True
    assert w.recognition_options('auto')['language_detection_segments'] == 5
    assert w.recognition_options('en', 'duration')['hotwords'] == 'duration'
    try: w.check_detected_language('ms', 'auto')
    except ValueError: pass
    else: raise AssertionError('Unexpected language accepted')

def test_real_quote_and_times():
    accepted, pending, rejected = q.review_topics(Reviewer(), [{**TOPIC, 'start_seconds': 999}], SEGMENTS)
    assert len(accepted) == 1 and accepted[0]['start_seconds'] == 12
    assert not pending and rejected == 0

def test_source_ids_produce_original_quotes():
    topic = {'title': TOPIC['title'], 'summary': TOPIC['summary'], 'evidence_segment_ids': [0]}
    accepted, _, _ = q.review_topics(Reviewer(), [topic], SEGMENTS)
    assert accepted[0]['evidence'][0]['quote'] == SEGMENTS[0]['text']
    assert q.evidence_for({**topic, 'evidence_segment_ids': [99]}, SEGMENTS) is None

def test_uncertain_source_cannot_confirm_notice():
    from backend.ai import verification
    source = [{'text': '下周三的期中考试定在10月15日。', 'is_uncertain': True}]
    item = {'category': 'exam', 'title': '期中考试', 'details': '已经确定考试', 'confidence': 'explicit',
            'evidence': [{'segment_id': '0', 'quote': source[0]['text']}]}
    result = verification.apply_verification([item], source)
    assert not result['confirmed'] and result['pending'][0]['confidence'] == 'uncertain'

def test_topic_without_usable_evidence_is_still_organized():
    fake = {'title': '消费者购买决策', 'summary': '旧示例', 'evidence': [{'segment_id': '0', 'quote': '消费者购买决策'}]}
    accepted, pending, rejected = q.review_topics(Reviewer(), [fake], SEGMENTS)
    assert len(accepted) == 1 and accepted[0]['evidence'] == []
    assert not pending and rejected == 0

def test_uncertainty_survives():
    uncertain = [{**SEGMENTS[0], 'is_uncertain': True}]
    accepted, pending, _ = q.review_topics(Reviewer(), [TOPIC], uncertain)
    assert len(accepted) == 1 and not pending
    assert '听辨存疑' in a._fmt_segments(uncertain)

def test_summary_is_kept_without_second_model_review():
    class Reject:
        def chat(self, *args, **kw): return '{"supported_ids":[]}'
    accepted, pending, _ = q.review_topics(Reject(), [{**TOPIC, 'summary': '股价一定上涨'}], SEGMENTS)
    assert len(accepted) == 1 and not pending

def test_exam_scope_is_kept_as_an_organized_topic():
    accepted, pending, _ = q.review_topics(Reviewer(), [{**TOPIC, 'summary': '考试内容包括债券。'}], SEGMENTS)
    assert len(accepted) == 1 and not pending

def test_global_ids_and_null_timestamps():
    segs = [{'text': SEGMENTS[0]['text']}]
    topic = {**TOPIC, 'evidence': [{**TOPIC['evidence'][0], 'segment_id': '8'}]}
    accepted, _, _ = q.review_topics(Reviewer(), [topic], segs, 8)
    assert accepted[0]['start_seconds'] is None
    assert q.evidence_for(topic, segs, 9) is None

def test_quote_across_sentence_boundary():
    segs = [{'text': 'Bond prices fall', 'start_seconds': 1, 'end_seconds': 3},
            {'text': 'when interest rates rise.', 'start_seconds': 3, 'end_seconds': 6, 'is_uncertain': True}]
    evidence = q.evidence_for(TOPIC, segs)
    assert evidence[0]['end_seconds'] == 6 and evidence[0]['uncertain']
    assert evidence[0]['segment_ids'] == ['0', '1']
    assert q.evidence_for({'evidence': [{'segment_id': '0', 'quote': 'when interest rates rise.'}]}, segs) is None

def test_organization_cannot_invent_content():
    accepted, _, _ = q.review_topics(Reviewer(), [TOPIC], SEGMENTS)
    chapters = q.organize_chapters(Reviewer(), accepted * 2)
    assert len(chapters) == 1 and chapters[0]['title'] == TOPIC['title']
    class Invalid:
        def chat(self, *args, **kw): return '{"groups":[[0,999]]}'
    assert len(q.organize_chapters(Invalid(), accepted * 2)) == 2

def test_prompts_have_no_factual_example():
    prompt = prompts.SYSTEM_ANALYZER.format(class_date='2026-09-01')
    assert '消费者' not in prompt and '第二次作业' not in prompt and '{{' not in prompt

if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('PASS', name)
