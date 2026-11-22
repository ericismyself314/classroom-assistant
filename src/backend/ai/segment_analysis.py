"""分段分析流程（见需求文档第九章）。

对长课堂转写，不一次性塞给模型：
1. 按段落长度拆分；
2. 每段分别提取候选主题 + 重要事项；
3. 合并重复内容（用本地去重 + 模型最终合并两次保险）；
4. 保留每条结论对应的原始片段与时间戳。
"""
from __future__ import annotations

import json
import re

from .. import dateparse, important, schemas
from ..logutil import get_logger
from . import prompts, note_quality, coverage
from .llm_provider import TranslationAPIError

LOG = get_logger("analysis")


def needs_chinese_display(segments: list[dict], session_meta: dict) -> bool:
    """只在英文/混合课堂（或明显英文的文字导入）调用翻译模型。"""
    language = str(session_meta.get("transcript_language") or "").lower()
    if language in {"en", "mix"}:
        return True
    if language == "zh":
        return False
    text = " ".join(str(s.get("text") or "") for s in segments[:30])
    letters = sum(ch.isascii() and ch.isalpha() for ch in text)
    han = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
    return letters >= 24 and letters > han * 2


def _translation_rows(raw) -> dict[int, str] | None:
    """Validate a compact translation response before anything is persisted."""
    if not isinstance(raw, dict) or not isinstance(raw.get("translations"), list):
        return None
    result: dict[int, str] = {}
    seen = set()
    duplicates = set()
    for row in raw["translations"]:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        if index in seen:
            duplicates.add(index)
        seen.add(index)
        text = str(row.get("text") or "").strip()
        if index >= 0 and text:
            result[index] = text
    for index in duplicates:
        result.pop(index, None)
    return result or None


def _partial_translation_rows(text) -> dict[int, str] | None:
    """Recover complete array entries when a translation JSON reply is truncated."""
    if not isinstance(text, str):
        return None
    start = re.match(r'^\s*\{\s*"translations"\s*:\s*\[', text)
    if not start:
        return None
    decoder = json.JSONDecoder()
    position = start.end()
    rows = []
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        try:
            row, end = decoder.raw_decode(text, position)
        except (ValueError, TypeError):
            break
        if not isinstance(row, dict):
            break
        position = end
        while position < len(text) and text[position].isspace():
            position += 1
        if position < len(text) and text[position] not in ',]':
            break
        rows.append(row)
        if position == len(text) or text[position] == ']':
            break
        position += 1
    recovered = {}
    duplicates = set()
    seen = set()
    for row in rows:
        index = row.get("index")
        if type(index) is int:
            pass
        elif isinstance(index, str) and index.isdecimal():
            index = int(index)
        else:
            continue
        if index in seen:
            duplicates.add(index)
        seen.add(index)
        value = row.get("text")
        if index >= 0 and isinstance(value, str) and value.strip():
            recovered[index] = value.strip()
    for index in duplicates:
        recovered.pop(index, None)
    return recovered or None


def _single_translation_text(data) -> str | None:
    """从（可能不完整的）模型响应里取第一条非空翻译文字，忽略编号。"""
    if isinstance(data, dict):
        tr = data.get("translations")
        if isinstance(tr, list):
            for row in tr:
                if isinstance(row, dict) and str(row.get("text") or "").strip():
                    return str(row["text"]).strip()
    return None


def _translation_reply_rows(response):
    return (_translation_rows(schemas._extract_json(response)) or
            _partial_translation_rows(response))


def _translation_input(provider, segments, start_index=0):
    if getattr(provider, "name", None) == "translation_api":
        return "\n".join(f"[{i}] {s['text']}" for i, s in enumerate(segments, start_index))
    return _fmt_segments(segments, start_index)


def _translate_chunk(provider, chunk: list[dict], offset: int, retries: int = 2, cache=None) -> dict[int, str]:
    """翻译一个块，返回 {全局编号: 中文}。整块都翻不出时返回 {}。

    本地小模型（如 Qwen3-4B）翻译大段经常丢条目或返回截断 JSON。这里整块重试几次；
    仍缺失的片段交给调用方逐段补翻，因此「一个块失败」绝不会拖累整节课。
    """
    user = prompts.TRANSLATION_PROMPT.format(segments=_translation_input(provider, chunk))
    key = cache.key(provider, prompts.TRANSLATION_SYSTEM, user, json_mode=True, temperature=0) if cache else None
    for attempt in range(retries):
        try:
            response = cache.get(key, lambda value: any(
                0 <= i < len(chunk) for i in (_translation_reply_rows(value) or {}))) if cache else None
            if response is None:
                response = provider.chat(prompts.TRANSLATION_SYSTEM, user, json_mode=True, temperature=0)
            rows = _translation_reply_rows(response)
        except (InterruptedError, TranslationAPIError):
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.warning("中文展示稿整块翻译调用失败（尝试 %d/%d）offset=%d: %s",
                        attempt + 1, retries, offset, exc)
            continue
        if rows:
            valid = {offset + i: t for i, t in rows.items() if 0 <= i < len(chunk) and str(t).strip()}
            if valid:
                if cache:
                    cache.put(key, response)
                return valid
            LOG.warning("中文展示稿整块翻译返回不完整（尝试 %d/%d）offset=%d，转交逐段补翻",
                        attempt + 1, retries, offset)
        else:
            LOG.warning("中文展示稿整块翻译无法解析（尝试 %d/%d）offset=%d", attempt + 1, retries, offset)
    return {}


def _translate_one_short(provider, segment: dict, global_index: int, retries: int = 3, cache=None) -> str | None:
    """翻译单个片段。小模型对单段最稳，用于补翻整块翻译漏掉的条目。"""
    line = _translation_input(provider, [segment], global_index)
    user = prompts.TRANSLATION_PROMPT.format(segments=line)
    key = cache.key(provider, prompts.TRANSLATION_SYSTEM, user, json_mode=True, temperature=0) if cache else None
    for attempt in range(retries):
        try:
            response = cache.get(key, lambda value: _single_translation_text(
                schemas._extract_json(value))) if cache else None
            if response is None:
                response = provider.chat(prompts.TRANSLATION_SYSTEM, user, json_mode=True, temperature=0)
            text = _single_translation_text(schemas._extract_json(response))
        except (InterruptedError, TranslationAPIError):
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.warning("单段翻译调用失败（尝试 %d/%d）seg=%d: %s", attempt + 1, retries, global_index, exc)
            continue
        if text:
            if cache:
                cache.put(key, response)
            return text
    return None


def _translation_parts(text: str, max_chars: int = 260) -> list[str]:
    """Split legacy coarse ASR text before sending it to the small local LLM."""
    value = str(text or "").strip()
    parts = []
    while len(value) > max_chars:
        cut = max(value.rfind(mark, 0, max_chars) for mark in
                  ("。", "！", "？", ".", "!", "?", "；", ";", "，", ",", " "))
        if cut < max_chars // 2:
            cut = max_chars
        else:
            cut += 1
        parts.append(value[:cut].strip())
        value = value[cut:].strip()
    if value:
        parts.append(value)
    return parts


def _translate_one(provider, segment: dict, global_index: int, retries: int = 3, cache=None) -> str | None:
    """Translate one source segment, including old oversized Qwen segments."""
    parts = _translation_parts(segment.get("text", ""))
    translated = []
    for part in parts:
        text = _translate_one_short(provider, {**segment, "text": part}, global_index, retries, cache=cache)
        if not text:
            return None
        translated.append(text)
    return "".join(translated) if translated else None


def translate_for_display(provider, segments: list[dict], session_meta: dict, progress_cb=None, cache=None) -> list[dict]:
    """Return Chinese display copies without overwriting the ASR source text.

    ``text`` remains the recoverable original in SQLite. The returned copies use
    Chinese text so every derived note, source quote, and uncertainty card is
    readable in Chinese as well.

    稳定性：本地小模型翻译大段时常丢条目或返回截断 JSON。这里逐块翻译、单块失败
    不中断整轮；每块缺失的片段再逐段补翻，只有真正翻不出的才回退英文原文
    （display_text=None），绝不再因「少一条」就让整节课失败。
    """
    if not segments:
        return []
    if not needs_chinese_display(segments, session_meta):
        return [{**s, "display_text": s.get("text"), "text": s.get("text", "")} for s in segments]
    if provider is None:
        raise RuntimeError("英文转写需要可用的本地文字模型来生成中文展示稿")

    total = len(segments)
    translated: dict[int, str] = {}
    chunks = split_segments(segments, max_chars=2600)
    offset = 0
    for ci, chunk in enumerate(chunks):
        label = f"翻译第 {ci + 1}/{len(chunks)} 块"
        if progress_cb:
            progress_cb(label, offset / total)
        rows = _translate_chunk(provider, chunk, offset, cache=cache)
        translated.update(rows)
        processed = offset + len(rows)
        if progress_cb and rows:
            progress_cb(label, processed / total)
        missing = [i for i in range(offset, offset + len(chunk)) if i not in translated]
        # A partial reply may omit several entries: batch just those before
        # falling back to individual requests, retaining their original order.
        if rows and len(missing) >= 3:
            batch = _translate_chunk(provider, [segments[i] for i in missing], 0, retries=1, cache=cache)
            translated.update({missing[i]: text for i, text in batch.items()})
            processed += len(batch)
            if progress_cb and batch:
                progress_cb(label, processed / total)
        # 逐段补翻本块仍缺失的片段（小模型对单段最稳）
        for i in range(offset, offset + len(chunk)):
            if i not in translated:
                t = _translate_one(provider, segments[i], i, cache=cache)
                if t:
                    translated[i] = t
                elif getattr(provider, "name", None) == "translation_api":
                    raise TranslationAPIError("API 遗漏了课堂片段，补译后仍无结果。请检查模型后继续。")
                processed += 1
                if progress_cb:
                    progress_cb(f"补译第 {ci + 1}/{len(chunks)} 块", processed / total)
        offset += len(chunk)

    out = []
    fallback_count = 0
    for i, segment in enumerate(segments):
        display = translated.get(i)
        if display:
            out.append({**segment, "display_text": display, "text": display})
        else:
            fallback_count += 1
            # display_text=None 让界面明确回退到可核对的 ASR 原文，且不会把英文误标成中文译文。
            out.append({**segment, "display_text": None, "text": segment.get("text", "")})
    if fallback_count:
        LOG.warning("中文展示稿采用英文原文兜底：translated=%d fallback=%d total=%d",
                    len(translated), fallback_count, total)
    if progress_cb:
        progress_cb("翻译为中文展示稿", 1.0)
    return out


def _fmt_segments(segments: list[dict], start_index: int = 0) -> str:
    """格式化片段给模型看。

    start_index 为全局偏移：analyze_session 分块分析时，每个 chunk 用全局序号，
    这样模型返回的 evidence.segment_id 指向整段转写的全局位置，下游证据核查才能正确定位。
    """
    lines = []
    for i, s in enumerate(segments):
        start = s.get("start_seconds")
        end = s.get("end_seconds")
        if start is None or end is None:
            time_label = "(无时间戳)"
        else:
            time_label = f"({float(start):.1f}-{float(end):.1f}s)"
        warning = "【听辨存疑】" if s.get("is_uncertain") else ""
        lines.append(f"[{i + start_index}] {time_label} {warning}{s.get('text', '')}")
    return "\n".join(lines)


def split_segments(segments: list[dict], max_chars: int = 2800) -> list[list[dict]]:
    chunks = []
    cur = []
    cur_len = 0
    for s in segments:
        t = len(s["text"])
        if cur and cur_len + t > max_chars:
            chunks.append(cur)
            cur = []
            cur_len = 0
        cur.append(s)
        cur_len += t
    if cur:
        chunks.append(cur)
    return chunks


def _fallback_topic(chunk: list[dict], chunk_index: int) -> dict:
    """Keep a readable result when the local model does not return JSON."""
    text = " ".join(str(s.get("text") or "").strip() for s in chunk).strip()
    if len(text) > 1800:
        text = text[:1800].rsplit(" ", 1)[0].rstrip() + " …"
    return {
        "title": f"课堂内容（第 {chunk_index + 1} 部分）",
        "summary": text or "这一部分没有可整理的文字。",
        "points": [],
        "important": False,
        "start_seconds": next((s.get("start_seconds") for s in chunk if s.get("start_seconds") is not None), None),
        "end_seconds": next((s.get("end_seconds") for s in reversed(chunk) if s.get("end_seconds") is not None), None),
    }


def _chunk_result(parsed):
    """Accept topic-only replies; important items are optional."""
    if isinstance(parsed, list):
        parsed = {"topics": parsed, "items": []}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("topics"), list):
        return None
    return {**parsed, "items": parsed.get("items") if isinstance(parsed.get("items"), list) else []}


def _cacheable_analysis_reply(response, window, offset, session_meta):
    raw = _chunk_result(schemas._extract_json(response))
    if raw is None:
        return False
    accepted, _, dropped = note_quality.review_topics(None, raw['topics'], window, offset)
    if not accepted or dropped:
        return False
    with_evidence = {**raw, 'items': _evidence_items(raw['items'], window, offset)}
    _, error = schemas.normalize_items(with_evidence, session_meta['session_date'])
    return not error


def _add_chunk_bounds(topics: list[dict], chunk: list[dict]) -> list[dict]:
    """给没有证据链接的主题保留这一小段的时间范围。"""
    start = next((s.get("start_seconds") for s in chunk if s.get("start_seconds") is not None), None)
    end = next((s.get("end_seconds") for s in reversed(chunk) if s.get("end_seconds") is not None), None)
    out = []
    for topic in topics:
        out.append({**topic,
                    "start_seconds": topic.get("start_seconds") if topic.get("start_seconds") is not None else start,
                    "end_seconds": topic.get("end_seconds") if topic.get("end_seconds") is not None else end})
    return out


def _evidence_items(raw_items: list[dict], window: list[dict], offset: int) -> list[dict]:
    """Convert model-selected IDs into evidence made from the real transcript."""
    out = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        ids = item.get("evidence_segment_ids")
        if "evidence" not in item and isinstance(ids, list):
            evidence = note_quality.evidence_for({"evidence_segment_ids": ids}, window, offset)
            if evidence:
                item["evidence"] = evidence
        out.append(item)
    return out


def _coarse_topics(topics: list[dict], limit: int = 5) -> list[dict]:
    """Collapse many chunk topics into a few readable timeline/knowledge blocks."""
    if not topics:
        return []
    if len(topics) <= limit:
        return topics
    group_size = max(1, (len(topics) + limit - 1) // limit)
    chapters = []
    for start in range(0, len(topics), group_size):
        selected = topics[start:start + group_size]
        first = selected[0]
        title = first.get("title") or f"课堂内容（第 {len(chapters) + 1} 部分）"
        if len(selected) > 1:
            title = f"{title}等内容"
        summaries = []
        points = []
        for topic in selected:
            summary = str(topic.get("summary") or "").strip()
            if summary and summary not in summaries:
                summaries.append(summary)
            points.extend(topic.get("points") or [])
        unique_points = []
        for point in sorted(points, key=lambda p: not bool(p.get("important"))):
            text = str(point.get("text") or "").strip()
            if text and text not in {p.get("text") for p in unique_points}:
                unique_points.append(point)
            if len(unique_points) >= 4:
                break
        evidence = [e for topic in selected for e in (topic.get("evidence") or [])]
        chapters.append({
            "title": title,
            "summary": "；".join(summaries)[:900],
            "points": unique_points,
            "important": any(bool(t.get("important")) or any(bool(p.get("important")) for p in (t.get("points") or [])) for t in selected),
            "evidence": evidence,
            "evidence_segment_ids": list(dict.fromkeys(j for e in evidence for j in e.get("segment_ids", []))),
            "start_seconds": min((t.get("start_seconds") for t in selected if t.get("start_seconds") is not None), default=None),
            "end_seconds": max((t.get("end_seconds") for t in selected if t.get("end_seconds") is not None), default=None),
        })
    return chapters[:limit]


def _clean_synthesis(raw, fallback: list[dict]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    timeline_raw = raw.get("timeline")
    knowledge_raw = raw.get("knowledge")
    if not title or not isinstance(timeline_raw, list) or not isinstance(knowledge_raw, list):
        return None
    def clean_rows(rows):
        result = []
        for i, row in enumerate(rows[:6]):
            if not isinstance(row, dict):
                continue
            title_i = str(row.get("title") or "").strip()
            summary = str(row.get("summary") or "").strip()
            if not title_i or not summary:
                continue
            base = fallback[min(i, len(fallback) - 1)] if fallback else {}
            points = []
            for point in row.get("points") if isinstance(row.get("points"), list) else []:
                if isinstance(point, dict) and str(point.get("text") or "").strip():
                    points.append({"kind": "concept", "text": str(point["text"]).strip(),
                                   "important": bool(point.get("important", False))})
                elif isinstance(point, str) and point.strip():
                    points.append({"kind": "concept", "text": point.strip(), "important": False})
            result.append({
                "title": title_i,
                "summary": summary,
                "points": points[:4],
                "important": bool(row.get("important", False)),
                "evidence": base.get("evidence", []),
                "evidence_segment_ids": base.get("evidence_segment_ids", []),
                "start_seconds": row.get("start_seconds") if row.get("start_seconds") is not None else base.get("start_seconds"),
                "end_seconds": row.get("end_seconds") if row.get("end_seconds") is not None else base.get("end_seconds"),
            })
        return result
    knowledge = clean_rows(knowledge_raw)
    timeline = clean_rows(timeline_raw)
    if not knowledge or not timeline:
        return None
    return {"title": title, "timeline": timeline, "knowledge": knowledge}


def _fallback_notes(course: str, topics: list[dict]) -> dict:
    chapters = _coarse_topics(topics)
    title = chapters[0]["title"] if chapters else (course or "本节课课堂整理")
    return {
        "version": 3,
        "title": title,
        "timeline": [{k: c.get(k) for k in ("title", "summary", "start_seconds", "end_seconds")} for c in chapters],
        "knowledge": chapters,
        "chapters": chapters,
    }


def _cacheable_synthesis_reply(response, fallback):
    raw = schemas._extract_json(response)
    clean = _clean_synthesis(raw, fallback)
    return bool(clean and all(len(clean[key]) == len(raw[key]) for key in ('timeline', 'knowledge')))


def _synthesize(provider, topics: list[dict], session_meta: dict, cache=None) -> dict:
    fallback = _fallback_notes(session_meta.get("course_name", ""), topics)
    if provider is None or not topics:
        return fallback
    drafts = json.dumps([{
        "title": t.get("title"), "summary": t.get("summary"),
        "important": t.get("important", False),
        "points": [{"text": p.get("text"), "important": p.get("important", False)} for p in (t.get("points") or [])],
        "start_seconds": t.get("start_seconds"), "end_seconds": t.get("end_seconds"),
    } for t in topics], ensure_ascii=False)
    system = prompts.SYNTHESIS_SYSTEM.format()
    user = prompts.SYNTHESIS_PROMPT.format(course=session_meta.get("course_name", ""),
                                          class_date=session_meta["session_date"], drafts=drafts)
    key = cache.key(provider, system, user, json_mode=True, temperature=0.1) if cache else None
    try:
        response = cache.get(key, lambda value: _cacheable_synthesis_reply(
            value, _coarse_topics(topics))) if cache else None
        if response is None:
            response = provider.chat(system, user, json_mode=True, temperature=0.1)
        raw = schemas._extract_json(response)
        merged = _clean_synthesis(raw, _coarse_topics(topics))
        if merged:
            if cache and _cacheable_synthesis_reply(response, _coarse_topics(topics)):
                cache.put(key, response)
            return {**fallback, **merged, "chapters": merged["knowledge"]}
    except InterruptedError:
        raise
    except Exception as e:  # noqa: BLE001
        LOG.warning("课堂整体整理失败，保留分段合并结果: %s", e)
    return fallback


def analyze_session(provider, segments: list[dict], session_meta: dict, progress_cb=None, cache=None):
    """返回 (overview, items)。

    overview = {"overview_title", "topics": [...]}
    items = [...]（已合并去重 + 相对日期归一化）
    """
    chunks = split_segments(segments)
    all_topics = []
    all_items = []
    review = []
    rejected = 0
    # 模板中的双括号和日期占位符必须展开，不能把 {{...}} 当作 JSON 示例发给模型。
    system = prompts.SYSTEM_ANALYZER.format(class_date=session_meta["session_date"])

    offset = 0
    for ci, chunk in enumerate(chunks):
        if progress_cb:
            progress_cb(f"整理第 {ci + 1}/{len(chunks)} 块", offset / len(segments))
        lo, hi = coverage.context_window(segments, offset, offset + len(chunk))
        window = segments[lo:hi]
        user = prompts.CHUNK_PROMPT.format(
            course=session_meta.get("course_name", ""),
            class_date=session_meta["session_date"],
            segments=_fmt_segments(window, start_index=lo),
        )
        raw = None
        for attempt in range(2):
            try:
                temperature = 0.2 if attempt == 0 else 0
                key = cache.key(provider, system, user, json_mode=True, temperature=temperature) if cache else None
                response = cache.get(key, lambda value: _cacheable_analysis_reply(
                    value, window, lo, session_meta)) if cache else None
                if cache and attempt == 0 and response is None:
                    retry_key = cache.key(provider, system, user, json_mode=True, temperature=0)
                    response = cache.get(retry_key, lambda value: _cacheable_analysis_reply(
                        value, window, lo, session_meta))
                    if response is not None:
                        key = retry_key
                if response is None:
                    response = provider.chat(system, user, json_mode=True, temperature=temperature)
                raw = _chunk_result(schemas._extract_json(response))
                if raw is not None:
                    break
                LOG.warning("分段 %d 输出格式无效，尝试 %d/2", ci, attempt + 1)
            except InterruptedError:
                raise
            except Exception as e:  # noqa: BLE001
                LOG.warning("分段 %d 分析调用失败，尝试 %d/2: %s", ci, attempt + 1, e)
        successful_reply = raw is not None
        if raw is None:
            LOG.warning("分段 %d 未返回可用 JSON；保留该段原文作为笔记", ci)
            raw = {"topics": [_fallback_topic(chunk, ci)], "items": []}
        accepted, pending, dropped = note_quality.review_topics(provider, raw["topics"], window, lo)
        cacheable = successful_reply and bool(accepted) and not dropped
        if not accepted:
            accepted = [_fallback_topic(chunk, ci)]
        accepted = _add_chunk_bounds(accepted, chunk)
        all_topics.extend(accepted)
        review.extend(pending)
        rejected += dropped
        raw_with_evidence = {**raw, "items": _evidence_items(raw.get("items") or [], window, lo)}
        its, its_err = schemas.normalize_items(raw_with_evidence, session_meta["session_date"])
        if cache and cacheable and not its_err:
            cache.put(key, response)
        if its:
            all_items.extend(its)
        elif its_err:
            LOG.warning("分段 %d 事项解析失败: %s", ci, its_err)
        offset += len(chunk)

    # A transcript organizer should not launch a second audit model or turn a
    # partial formatting problem into a failed lesson.
    coverage_stats = {"signal_passages": 0, "reviewed_passages": 0,
                      "pending_passages": 0, "recovered_items": 0}
    if progress_cb:
        progress_cb('合并笔记与课程要求', 0.95)
    final_items = _finalize(provider, all_items, session_meta)
    final_items = schemas.apply_date_normalization(final_items, session_meta["session_date"])

    notes = _synthesize(provider, all_topics, session_meta, cache=cache)
    chapters = notes.get("knowledge") or notes.get("chapters") or []
    notes.update({"review": review, "coverage": coverage_stats,
                  "rejected_count": rejected,
                  "uncertain_segments": sum(bool(s.get("is_uncertain")) for s in segments)})
    overview = {"overview_title": notes.get("title") or "本节课内容概览", "topics": chapters,
                "notes": notes}
    if progress_cb:
        progress_cb("analyzing", 1.0)
    return overview, final_items


def _finalize(provider, items: list[dict], session_meta: dict) -> list[dict]:
    # Free-form global rewriting could omit a condition or an entire requirement.
    # Conservative deterministic merging retains every distinct detail and source.
    return important.merge_items(items)
