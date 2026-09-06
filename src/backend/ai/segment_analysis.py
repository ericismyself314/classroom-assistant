"""分段分析流程（见需求文档第九章）。

对长课堂转写，不一次性塞给模型：
1. 按段落长度拆分；
2. 每段分别提取候选主题 + 重要事项；
3. 合并重复内容（用本地去重 + 模型最终合并两次保险）；
4. 保留每条结论对应的原始片段与时间戳。
"""
from __future__ import annotations

import json

from .. import dateparse, important, schemas
from ..logutil import get_logger
from . import prompts, note_quality, coverage

LOG = get_logger("analysis")


def _needs_chinese_display(segments: list[dict], session_meta: dict) -> bool:
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
    for row in raw["translations"]:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        text = str(row.get("text") or "").strip()
        if index >= 0 and text:
            result[index] = text
    return result or None


def translate_for_display(provider, segments: list[dict], session_meta: dict, progress_cb=None) -> list[dict]:
    """Return Chinese display copies without overwriting the ASR source text.

    ``text`` remains the recoverable original in SQLite. The returned copies use
    Chinese text so every derived note, source quote, and uncertainty card is
    readable in Chinese as well.
    """
    if not segments:
        return []
    if not _needs_chinese_display(segments, session_meta):
        return [{**s, "display_text": s.get("text"), "text": s.get("text", "")} for s in segments]
    if provider is None:
        raise RuntimeError("英文转写需要可用的本地文字模型来生成中文展示稿")

    translated: dict[int, str] = {}
    chunks = split_segments(segments, max_chars=2600)
    offset = 0
    for ci, chunk in enumerate(chunks):
        if progress_cb:
            progress_cb("翻译为中文展示稿", ci / max(len(chunks), 1))
        response = provider.chat(
            prompts.TRANSLATION_SYSTEM,
            prompts.TRANSLATION_PROMPT.format(segments=_fmt_segments(chunk, offset)),
            json_mode=True,
            temperature=0,
        )
        rows = _translation_rows(schemas._extract_json(response))
        expected = set(range(offset, offset + len(chunk)))
        if rows is None or not expected.issubset(rows):
            raise RuntimeError("中文展示稿生成不完整；已保留英文原文，请稍后仅重新分析重试")
        translated.update({i: rows[i] for i in expected})
        offset += len(chunk)

    out = []
    for i, segment in enumerate(segments):
        display = translated.get(i)
        if not display:
            raise RuntimeError("中文展示稿生成不完整；已保留英文原文，请稍后仅重新分析重试")
        out.append({**segment, "display_text": display, "text": display})
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


def _synthesize(provider, topics: list[dict], session_meta: dict) -> dict:
    fallback = _fallback_notes(session_meta.get("course_name", ""), topics)
    if provider is None or not topics:
        return fallback
    drafts = json.dumps([{
        "title": t.get("title"), "summary": t.get("summary"),
        "important": t.get("important", False),
        "points": [{"text": p.get("text"), "important": p.get("important", False)} for p in (t.get("points") or [])],
        "start_seconds": t.get("start_seconds"), "end_seconds": t.get("end_seconds"),
    } for t in topics], ensure_ascii=False)
    try:
        raw = schemas._extract_json(provider.chat(
            prompts.SYNTHESIS_SYSTEM.format(),
            prompts.SYNTHESIS_PROMPT.format(course=session_meta.get("course_name", ""),
                                            class_date=session_meta["session_date"], drafts=drafts),
            json_mode=True, temperature=0.1))
        merged = _clean_synthesis(raw, _coarse_topics(topics))
        if merged:
            return {**fallback, **merged, "chapters": merged["knowledge"]}
    except InterruptedError:
        raise
    except Exception as e:  # noqa: BLE001
        LOG.warning("课堂整体整理失败，保留分段合并结果: %s", e)
    return fallback


def analyze_session(provider, segments: list[dict], session_meta: dict, progress_cb=None):
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
            progress_cb("analyzing", (ci / max(len(chunks), 1)) if chunks else 1.0)
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
                response = provider.chat(system, user, json_mode=True, temperature=0.2 if attempt == 0 else 0)
                raw = _chunk_result(schemas._extract_json(response))
                if raw is not None:
                    break
                LOG.warning("分段 %d 输出格式无效，尝试 %d/2", ci, attempt + 1)
            except InterruptedError:
                raise
            except Exception as e:  # noqa: BLE001
                LOG.warning("分段 %d 分析调用失败，尝试 %d/2: %s", ci, attempt + 1, e)
        if raw is None:
            LOG.warning("分段 %d 未返回可用 JSON；保留该段原文作为笔记", ci)
            raw = {"topics": [_fallback_topic(chunk, ci)], "items": []}
        accepted, pending, dropped = note_quality.review_topics(provider, raw["topics"], window, lo)
        if not accepted:
            accepted = [_fallback_topic(chunk, ci)]
        accepted = _add_chunk_bounds(accepted, chunk)
        all_topics.extend(accepted)
        review.extend(pending)
        rejected += dropped
        raw_with_evidence = {**raw, "items": _evidence_items(raw.get("items") or [], window, lo)}
        its, its_err = schemas.normalize_items(raw_with_evidence, session_meta["session_date"])
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

    notes = _synthesize(provider, all_topics, session_meta)
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
