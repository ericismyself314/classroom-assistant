import json

from backend.ai import segment_analysis
from backend.ai.inference_cache import InferenceCache


class Provider:
    model = {"id": "local-test", "params": "-ngl 99"}

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def model_label(self):
        return "local-test"

    def chat(self, *args, **kwargs):
        self.calls += 1
        return self.reply


def test_cache_key_binds_model_prompts_parameters_and_settings(tmp_path):
    p = Provider("")
    cache = InferenceCache(tmp_path, {"language_mode": "en"})
    key = cache.key(p, "system", "input", temperature=0, json_mode=True)
    assert key != cache.key(p, "changed", "input", temperature=0, json_mode=True)
    assert key != cache.key(p, "system", "changed", temperature=0, json_mode=True)
    assert key != cache.key(p, "system", "input", temperature=0.2, json_mode=True)
    assert key != InferenceCache(tmp_path, {"language_mode": "zh"}).key(
        p, "system", "input", temperature=0, json_mode=True)
    p.model = {**p.model, "params": "-ngl 35"}
    assert key != cache.key(p, "system", "input", temperature=0, json_mode=True)


def test_successful_translation_is_reused_but_changed_source_is_not(tmp_path):
    p = Provider(json.dumps({"translations": [{"index": 0, "text": "译文"}]}))
    cache = InferenceCache(tmp_path, {})
    source = [{"text": "Original", "start_seconds": 0, "end_seconds": 2}]
    assert segment_analysis._translate_chunk(p, source, 3, cache=cache) == {3: "译文"}
    assert segment_analysis._translate_chunk(p, source, 3, cache=cache) == {3: "译文"}
    assert p.calls == 1
    segment_analysis._translate_chunk(p, [{**source[0], "text": "Changed"}], 3, cache=cache)
    assert p.calls == 2


def test_failed_translation_is_not_cached(tmp_path):
    p = Provider("not json")
    cache = InferenceCache(tmp_path, {})
    source = [{"text": "Original"}]
    assert segment_analysis._translate_chunk(p, source, 0, retries=1, cache=cache) == {}
    p.reply = json.dumps({"translations": [{"index": 0, "text": "译文"}]} )
    assert segment_analysis._translate_chunk(p, source, 0, retries=1, cache=cache) == {0: "译文"}
    assert p.calls == 2


def test_invalid_cached_reply_is_retried_with_model_even_with_one_attempt(tmp_path):
    p = Provider(json.dumps({"translations": [{"index": 0, "text": "译文"}]}))
    cache = InferenceCache(tmp_path, {})
    source = [{"text": "Original"}]
    user = segment_analysis.prompts.TRANSLATION_PROMPT.format(segments=segment_analysis._fmt_segments(source))
    key = cache.key(p, segment_analysis.prompts.TRANSLATION_SYSTEM, user, temperature=0, json_mode=True)
    cache.put(key, "valid envelope with invalid model output")
    assert segment_analysis._translate_chunk(p, source, 0, retries=1, cache=cache) == {0: "译文"}
    assert p.calls == 1


def test_corrupt_or_unwritable_cache_falls_back_without_losing_results(tmp_path):
    p = Provider("")
    cache = InferenceCache(tmp_path / "cache", {})
    key = cache.key(p, "s", "u", temperature=0, json_mode=True)
    cache.put(key, "valid response")
    (tmp_path / "cache" / f"{key}.json").write_text("broken", encoding="utf-8")
    assert cache.get(key) is None
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    broken = InferenceCache(blocked, {})
    broken.put(key, "response")
    assert broken.get(key) is None


def test_analysis_and_synthesis_reuse_only_successful_calls(tmp_path):
    class Organizer(Provider):
        def chat(self, system, user, **kwargs):
            self.calls += 1
            if "课堂笔记编辑" in system:
                return json.dumps({"title": "合成课", "timeline": [{"title": "概念", "summary": "解释概念"}],
                                   "knowledge": [{"title": "概念", "summary": "解释概念", "points": []}]})
            return json.dumps({"topics": [{"title": "概念", "summary": "解释概念", "points": [],
                                           "evidence_segment_ids": [0]}], "items": []})
    p = Organizer("")
    cache = InferenceCache(tmp_path, {})
    source = [{"text": "老师解释概念。", "original_text": "老师解释概念。", "start_seconds": 0, "end_seconds": 2}]
    meta = {"session_date": "2026-11-12", "course_name": "合成课"}
    first = segment_analysis.analyze_session(p, source, meta, cache=cache)
    calls = p.calls
    assert segment_analysis.analyze_session(p, source, meta, cache=cache) == first
    assert p.calls == calls
    segment_analysis.analyze_session(p, source, {**meta, "session_date": "2026-11-13"}, cache=cache)
    assert p.calls > calls


def test_interrupted_analysis_resumes_after_successful_block(tmp_path, monkeypatch):
    import pytest
    class InterruptingOrganizer(Provider):
        def __init__(self):
            super().__init__("")
            self.interrupt = True
            self.prompts = []

        def chat(self, system, user, **kwargs):
            self.calls += 1
            self.prompts.append(user)
            if "第二块" in user and self.interrupt:
                raise InterruptedError("cancelled")
            if "课堂笔记编辑" in system:
                return json.dumps({"title": "合成课", "timeline": [{"title": "概念", "summary": "概念解释"}],
                                   "knowledge": [{"title": "概念", "summary": "概念解释", "points": []}]})
            return json.dumps({"topics": [{"title": "概念", "summary": "概念解释", "points": [],
                                           "evidence_segment_ids": [0]}], "items": []})
    p = InterruptingOrganizer()
    source = [{"text": text, "start_seconds": i, "end_seconds": i+1}
              for i, text in enumerate(["第一块", "第二块"])]
    monkeypatch.setattr(segment_analysis, 'split_segments', lambda segments: [[s] for s in segments])
    monkeypatch.setattr(segment_analysis.coverage, 'context_window', lambda segments, start, end: (start, end))
    cache = InferenceCache(tmp_path, {})
    meta = {"session_date": "2026-11-12", "course_name": "合成课"}
    with pytest.raises(InterruptedError):
        segment_analysis.analyze_session(p, source, meta, cache=cache)
    p.interrupt = False
    segment_analysis.analyze_session(p, source, meta, cache=cache)
    assert sum("第一块" in prompt for prompt in p.prompts) == 1
    assert sum("第二块" in prompt for prompt in p.prompts) == 2


def test_structural_reply_rejected_by_topic_review_is_not_cached(tmp_path):
    class InitiallyEmpty(Provider):
        def chat(self, system, user, **kwargs):
            self.calls += 1
            if "课堂笔记编辑" in system:
                return "invalid synthesis"
            return json.dumps({"topics": [{}] if self.calls == 1 else [
                {"title": "有效概念", "summary": "有效的概念解释", "points": []}], "items": []})
    p = InitiallyEmpty("")
    cache = InferenceCache(tmp_path, {})
    source = [{"text": "老师解释概念。", "start_seconds": 0, "end_seconds": 2}]
    meta = {"session_date": "2026-11-12", "course_name": "合成课"}
    segment_analysis.analyze_session(p, source, meta, cache=cache)
    assert list(tmp_path.glob('*.json')) == []
    second, _ = segment_analysis.analyze_session(p, source, meta, cache=cache)
    assert second['topics'][0]['title'] == "有效概念"


def test_provider_failure_fallback_is_not_cached(tmp_path):
    class Offline(Provider):
        def chat(self, *args, **kwargs):
            raise RuntimeError('unavailable')
    cache = InferenceCache(tmp_path, {})
    source = [{"text": "老师解释概念。", "start_seconds": 0, "end_seconds": 2}]
    segment_analysis.analyze_session(Offline(''), source,
                                    {"session_date": "2026-11-12", "course_name": "合成课"}, cache=cache)
    assert list(tmp_path.glob('*.json')) == []


def test_partly_rejected_topics_are_not_cached_as_a_complete_success(tmp_path):
    class PartlyMalformed(Provider):
        def chat(self, system, user, **kwargs):
            if "课堂笔记编辑" in system:
                return "invalid synthesis"
            return json.dumps({"topics": [{"title": "概念", "summary": "有效概念解释"}, {}], "items": []})
    cache = InferenceCache(tmp_path, {})
    source = [{"text": "老师解释概念。", "start_seconds": 0, "end_seconds": 2}]
    segment_analysis.analyze_session(PartlyMalformed(''), source,
                                    {"session_date": "2026-11-12", "course_name": "合成课"}, cache=cache)
    assert list(tmp_path.glob('*.json')) == []


def test_partly_malformed_synthesis_is_not_cached(tmp_path):
    p = Provider(json.dumps({"title": "概念课", "timeline": [{"title": "概念", "summary": "有效解释"}, {}],
                             "knowledge": [{"title": "概念", "summary": "有效解释"}]}))
    cache = InferenceCache(tmp_path, {})
    topics = [{"title": "概念", "summary": "有效解释", "points": []}]
    segment_analysis._synthesize(p, topics, {"session_date": "2026-11-12"}, cache=cache)
    assert list(tmp_path.glob('*.json')) == []


def test_translation_cancellation_propagates_without_caching(tmp_path):
    import pytest
    class Cancelled(Provider):
        def chat(self, *args, **kwargs):
            self.calls += 1
            raise InterruptedError('cancelled')
    p = Cancelled('')
    cache = InferenceCache(tmp_path, {})
    with pytest.raises(InterruptedError):
        segment_analysis._translate_chunk(p, [{'text': 'Original'}], 0, cache=cache)
    with pytest.raises(InterruptedError):
        segment_analysis._translate_one_short(p, {'text': 'Original'}, 0, cache=cache)
    assert p.calls == 2
    assert list(tmp_path.glob('*.json')) == []


def test_success_from_second_analysis_attempt_is_reused_without_failed_attempt(tmp_path):
    class SecondAttempt(Provider):
        def chat(self, system, user, **kwargs):
            self.calls += 1
            if "课堂笔记编辑" in system:
                return json.dumps({"title": "概念课", "timeline": [{"title": "概念", "summary": "解释概念"}],
                                   "knowledge": [{"title": "概念", "summary": "解释概念"}]})
            if kwargs['temperature'] == 0.2:
                return 'invalid model output'
            return json.dumps({"topics": [{"title": "概念", "summary": "解释概念", "points": []}], "items": []})
    p = SecondAttempt('')
    cache = InferenceCache(tmp_path, {})
    source = [{"text": "老师解释概念。", "start_seconds": 0, "end_seconds": 2}]
    meta = {"session_date": "2026-11-12", "course_name": "合成课"}
    first = segment_analysis.analyze_session(p, source, meta, cache=cache)
    calls = p.calls
    assert segment_analysis.analyze_session(p, source, meta, cache=cache) == first
    assert p.calls == calls
