"""Classroom-note cleanup helpers.

The default job is to organize an existing transcript. Source links are retained
when the model supplies usable ones, but they are optional metadata: a bad link
must never discard an otherwise useful note or make a lesson fail.
"""
import json
import re

from .. import schemas


def _compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def _same_note(a, b):
    # Remove surface filler, not numbers, negation, qualifications or units.
    def norm(t):
        t = re.sub(r'^(老师(?:明确)?(?:说明|指出|介绍)(?:了)?|本节课(?:介绍|讲解)(?:了)?)', '', t.strip())
        return re.sub(r'[\s，。；：、,.!！?？:;]', '', t).casefold()
    return bool(norm(a)) and norm(a) == norm(b)


def _evidence_excerpt(text, limit=280):
    """Return a verifier-safe source excerpt from a coarse ASR segment."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    cut = max(value.rfind(mark, 0, limit) for mark in ("。", "！", "？", ".", "!", "?", "；", ";"))
    if cut < limit // 2:
        cut = limit
    else:
        cut += 1
    return value[:cut].strip()


def evidence_for(candidate, segments, offset=0):
    evidence = candidate.get("evidence")
    if evidence is None:
        # IDs are model choices; quotes and times always come from our own source.
        ids = candidate.get("evidence_segment_ids")
        if not isinstance(ids, list) or not ids:
            return None
        evidence = []
        for sid in ids:
            try:
                index = int(str(sid)) - offset
            except (TypeError, ValueError):
                return None
            if not 0 <= index < len(segments):
                return None
            evidence.append({"segment_id": str(index + offset),
                             "quote": _evidence_excerpt(segments[index]["text"])})
    if not isinstance(evidence, list) or not evidence:
        return None
    result = []
    for entry in evidence:
        if not isinstance(entry, dict):
            return None
        try:
            i = int(str(entry.get("segment_id"))) - offset
        except (ValueError, TypeError):
            return None
        if not 0 <= i < len(segments):
            return None
        quote = str(entry.get("quote") or "").strip()
        compact_quote = _compact(quote)
        if len(compact_quote) < 4:
            return None
        # ASR splits in the middle of sentences. Allow an exact quote to continue
        # across up to four adjacent segments, but it must START in the cited one.
        matched = None
        for end in range(i + 1, min(len(segments), i + 4) + 1):
            span = segments[i:end]
            text = _compact(' '.join(s['text'] for s in span))
            position = text.find(compact_quote)
            if 0 <= position < len(_compact(segments[i]['text'])):
                matched = span
                break
        if matched is None:
            return None
        result.append({"segment_id": str(i + offset), "quote": quote,
                       "segment_ids": [str(j + offset) for j in range(i, i + len(matched))],
                       "start_seconds": matched[0].get("start_seconds"),
                       "end_seconds": matched[-1].get("end_seconds"),
                       "uncertain": any(bool(s.get("is_uncertain")) for s in matched)})
    return result


def review_topics(provider, raw_topics, segments, offset=0):
    """Normalize model topics without treating optional source links as a gate.

    ``provider`` remains for API compatibility. There is intentionally no second
    LLM review: the user wants an organized version of their transcript, even if
    a small local model omits or mangles segment IDs.
    """
    candidates = []
    rejected = 0
    for raw in raw_topics:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        title, summary = str(raw.get("title") or "").strip(), str(raw.get("summary") or "").strip()
        if not title or not summary:
            rejected += 1
            continue
        ids = raw.get("evidence_segment_ids")
        ev = evidence_for({"evidence_segment_ids": ids}, segments, offset) if isinstance(ids, list) else None
        if ev is None and "evidence" in raw:
            ev = evidence_for({"evidence": raw.get("evidence")}, segments, offset)
        ev = ev or []
        base = {"title": title, "summary": summary, "evidence": ev,
                "evidence_segment_ids": list(dict.fromkeys(j for e in ev for j in e["segment_ids"])),
                "start_seconds": min((e["start_seconds"] for e in ev if e["start_seconds"] is not None), default=None),
                "end_seconds": max((e["end_seconds"] for e in ev if e["end_seconds"] is not None), default=None),
                "points": [], "important": bool(raw.get("important", False))}
        points = raw.get("points")
        for point in points if isinstance(points, list) else []:
            if not isinstance(point, dict) or not isinstance(point.get("text"), str):
                continue
            point_ids = point.get("evidence_segment_ids")
            pev = evidence_for({"evidence_segment_ids": point_ids}, segments, offset) if isinstance(point_ids, list) else None
            pev = pev or []
            if _same_note(point['text'], summary):
                continue
            base["points"].append({"kind": point.get("kind") if point.get("kind") in
                                   {"concept", "example", "formula", "caution"} else "concept",
                                   "text": point["text"].strip(), "evidence": pev,
                                   "important": bool(point.get("important", False) or point.get("kind") == "caution")})
        candidates.append(base)
    return candidates, [], rejected


def organize_chapters(provider, topics):
    """Model selects IDs only, so organization cannot invent additional assertions."""
    if provider is None or len(topics) < 2:
        return topics
    raw = None
    try:
        raw = schemas._extract_json(provider.chat(
            '把课堂主题归为连贯章节，保留全部主题，按首次出现顺序排序。只输出 JSON：'
            '{"groups":[[0,1],[2]]}。数字是给定 id；每个 id 恰好出现一次。不要创造文字或新知识。',
            json.dumps([{"id": i, "title": t["title"], "summary": t['summary'][:400]}
                        for i, t in enumerate(topics)], ensure_ascii=False),
            json_mode=True, temperature=0))
    except InterruptedError:
        raise
    except Exception:
        pass
    groups = raw.get("groups") if isinstance(raw, dict) else None
    valid = isinstance(groups, list) and all(isinstance(g, list) and g for g in groups)
    flat = [i for g in groups for i in g] if valid else []
    if not valid or any(type(i) is not int for i in flat) or sorted(flat) != list(range(len(topics))):
        groups = [[i] for i in range(len(topics))]
    chapters = []
    for group in sorted(groups, key=min):
        selected = [topics[i] for i in sorted(group)]
        first = selected[0]
        points = list(first["points"])
        for topic in selected[1:]:
            if not _same_note(topic['summary'], first['summary']):
                points.append({"kind": "concept", "text": topic["summary"], "evidence": topic["evidence"]})
            points.extend(topic["points"])
        unique = []
        for point in points:
            if not _same_note(point['text'], first['summary']) and not any(_same_note(point['text'], q['text']) for q in unique):
                unique.append(point)
        points = unique
        evidence = [e for t in selected for e in t["evidence"]]
        chapters.append({**first, "points": points, "evidence": evidence,
                         "evidence_segment_ids": list(dict.fromkeys(j for e in evidence for j in e["segment_ids"])),
                         "end_seconds": max((e["end_seconds"] for e in evidence if e["end_seconds"] is not None), default=None)})
    return chapters
