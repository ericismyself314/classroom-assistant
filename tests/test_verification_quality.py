"""Synthetic regressions for evidence, correction chronology and calendar dates."""
import pytest

from backend import schemas
from backend.ai import verification as verification


def event(title, when, quote, index, *, details='', evidence_start=None):
    return {'category': 'exam', 'title': title, 'details': details,
            'normalized_date': when, 'confidence': 'explicit', 'action_required': True,
            'evidence': [{'segment_id': index, 'quote': quote,
                          'start_seconds': evidence_start, 'end_seconds': None}]}


def verify(events, texts):
    segments = [{'id': f'source-{i}', 'text': text, 'start_seconds': i * 60.0,
                 'end_seconds': (i + 1) * 60.0} for i, text in enumerate(texts)]
    return verification.apply_verification(events, segments, {'audio_duration_seconds': len(texts) * 60})


def test_changed_negation_cannot_pass_using_shared_quote_ends():
    actual = '考试安排在十一月二十五日，不需要提交额外的报告。'
    invented = '考试安排在十一月二十五日，必须提交额外的报告。'
    result = verify([event('额外报告', None, invented, 0)], [actual])
    assert not result['confirmed']
    assert result['rejected'][0]['rejection_reason'] == 'evidence_not_found_in_source'


def test_changed_number_cannot_pass_using_shared_quote_ends():
    actual = '正式通知，考试安排在十一月二十五日，地点还是原教室。'
    invented = '正式通知，考试安排在十一月二十一日，地点还是原教室。'
    result = verify([event('考试', '2026-11-21', invented, 0)], [actual])
    assert result['rejected'] and not result['confirmed']


def test_exact_quote_still_accepts_punctuation_spacing_and_case_changes():
    assert verification._quote_in_segment('Exam DUE on Monday!', 'The exam due on Monday.')
    assert verification._quote_in_segment('考试定在11月25日', '考试定在 11 月 25 日。')


@pytest.mark.parametrize('reverse', [False, True])
def test_last_spoken_correction_wins_independent_of_model_array_order(reverse):
    texts = ['更正一下，考试已经确定改到十一月二十一日。',
             '再次更正，考试已经确定改到十一月二十五日。']
    events = [event('期中考试', '2026-11-21', texts[0], 0),
              event('期中考试', '2026-11-25', texts[1], 1)]
    if reverse:
        events.reverse()
    result = verify(events, texts)
    assert [row['normalized_date'] for row in result['confirmed']] == ['2026-11-25']
    assert [row['normalized_date'] for row in result['rejected']] == ['2026-11-21']


def test_correction_order_uses_source_not_model_evidence_timestamp():
    texts = ['更正一下，考试已经确定改到十一月二十一日。',
             '再次更正，考试已经确定改到十一月二十五日。']
    result = verify([event('考试', '2026-11-25', texts[1], 'source-1', evidence_start=0),
                     event('考试', '2026-11-21', texts[0], 'source-0', evidence_start=100)], texts)
    assert [row['normalized_date'] for row in result['confirmed']] == ['2026-11-25']


def test_conflicting_dates_with_same_source_position_remain_pending():
    text = '更正一下，考试已经确定改到十一月二十五日。'
    result = verify([event('考试', '2026-11-21', text, 0),
                     event('考试', '2026-11-25', text, 0)], [text])
    assert not result['confirmed'] and not result['rejected']
    assert len(result['pending']) == 2


def test_ambiguous_repeated_quote_does_not_guess_correction_order():
    text = '更正一下，考试已经确定改到另一个日期。'
    result = verify([event('考试', '2026-11-21', text, None),
                     event('考试', '2026-11-25', text, None)], [text, text])
    assert not result['confirmed'] and len(result['pending']) == 2


def test_later_announcement_is_not_overridden_by_earlier_correction():
    texts = ['更正一下，考试已经确定改到十一月二十一日。',
             '正式通知，考试安排在十一月二十五日。']
    result = verify([event('考试', '2026-11-25', texts[1], 1),
                     event('考试', '2026-11-21', texts[0], 0)], texts)
    assert [row['normalized_date'] for row in result['confirmed']] == ['2026-11-25']


def test_correction_chronology_uses_actual_source_times_when_segments_are_unsorted():
    early = '更正一下，考试已经确定改到十一月二十一日。'
    late = '再次更正，考试已经确定改到十一月二十五日。'
    segments = [{'id': 'late', 'text': late, 'start_seconds': 60, 'end_seconds': 120},
                {'id': 'early', 'text': early, 'start_seconds': 0, 'end_seconds': 60}]
    result = verification.apply_verification(
        [event('考试', '2026-11-25', late, 'late'), event('考试', '2026-11-21', early, 'early')],
        segments, {'audio_duration_seconds': 120})
    assert [row['normalized_date'] for row in result['confirmed']] == ['2026-11-25']


@pytest.mark.parametrize('invalid', ['2026-02-31', '2026-02-29', '2026-13-01'])
def test_impossible_calendar_date_is_rejected(invalid):
    text = '正式通知，考试已经确定，下周安排在原教室。'
    result = verify([event('考试', invalid, text, 0)], [text])
    assert not result['confirmed']
    assert result['rejected'][0]['rejection_reason'].startswith('bad_date:')


def test_valid_leap_day_remains_supported():
    text = '正式通知，考试安排在二月二十九日。'
    result = verify([event('考试', '2028-02-29', text, 0)], [text])
    assert result['confirmed']


def test_model_date_conflicting_with_parseable_wording_is_not_asserted():
    rows = [{'raw_date_text': '11月25日', 'normalized_date': '2026-11-21', 'confidence': 'explicit'}]
    normalized = schemas.apply_date_normalization(rows, '2026-11-13')
    assert normalized[0]['confidence'] == 'uncertain'
    assert normalized[0]['normalized_date'] is None
    assert normalized[0]['raw_date_text'] == '11月25日'


def test_invalid_model_date_is_not_retained_as_a_deadline():
    rows = [{'raw_date_text': None, 'normalized_date': '2026-02-31', 'confidence': 'explicit'}]
    normalized = schemas.apply_date_normalization(rows, '2026-11-13')
    assert normalized[0]['normalized_date'] is None
    assert normalized[0]['confidence'] == 'uncertain'


@pytest.mark.parametrize('raw', ['2027年11月25日', '明年11月25日'])
def test_date_reconciliation_does_not_override_an_explicit_other_year(raw):
    rows = [{'raw_date_text': raw, 'normalized_date': '2027-11-25', 'confidence': 'explicit'}]
    normalized = schemas.apply_date_normalization(rows, '2026-11-13')
    assert normalized[0]['normalized_date'] == '2027-11-25'
    assert normalized[0]['confidence'] == 'explicit'
