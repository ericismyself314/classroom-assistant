"""Atomic user corrections with original content, revisions and concurrency guards."""
import json

from . import db

ACTIVE = {'pending', 'running', 'file_check', 'transcribing', 'analyzing', 'indexing', 'cancelling'}


def _editable(sid):
    session = db.get_session(sid)
    if not session:
        raise ValueError('课堂记录不存在')
    if session['status'] in ACTIVE or any(j['session_id'] == sid for j in db.active_jobs()):
        raise ValueError('课堂正在处理，请停止或完成后再修改')
    return session


def correct_segment(segment_id, body):
    text = body.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError('请输入有效的转写文字（不超过 20000 字）')
    with db.write_txn() as conn:
        row = conn.execute('SELECT * FROM transcript_segments WHERE id=?', (segment_id,)).fetchone()
        if not row:
            raise ValueError('转写片段不存在')
        sid = row['session_id']
        _editable(sid)
        if body.get('expected_text') != row['text']:
            raise ValueError('这段文字已变化，请刷新后重新编辑')
        if text.strip() == row['text'] and not row['is_uncertain']:
            return
        db.snapshot_session(sid, '修改转写前')
        conn.execute('UPDATE transcript_segments SET text=?, display_text=NULL, original_text=COALESCE(original_text,text), '
                     'is_uncertain=0, updated_at=? WHERE id=?', (text.strip(), db.now_iso(), segment_id))
        # Prior summaries cite obsolete wording; keep them in history, not current results.
        db.replace_topics(sid, [])
        db.replace_items(sid, [])
        db.update_session(sid, notes_data=json.dumps({'version': 2, 'chapters': [], 'review': [],
                          'transcript_edited': True}, ensure_ascii=False), status='needs_analysis',
                          processing_error=None)
        db.index_session(sid)


def correct_chapter(sid, index, body):
    with db.write_txn():
        session = _editable(sid)
        notes = json.loads(session.get('notes_data') or '{}')
        chapters = notes.get('chapters') or []
        if not 0 <= index < len(chapters):
            raise ValueError('章节不存在，请刷新页面')
        chapter = chapters[index]
        if (body.get('expected_title') != chapter.get('title') or
                body.get('expected_summary') != chapter.get('summary') or
                body.get('expected_points') != [p['text'] for p in chapter.get('points', [])]):
            raise ValueError('章节内容已变化，请刷新后重新编辑')
        title, summary = body.get('title'), body.get('summary')
        points = body.get('points')
        if (not isinstance(title, str) or not title.strip() or len(title) > 200 or
                not isinstance(summary, str) or not summary.strip() or len(summary) > 20000 or
                not isinstance(points, list) or len(points) != len(chapter.get('points', [])) or
                any(not isinstance(t, str) or not t.strip() or len(t) > 10000 for t in points)):
            raise ValueError('请填写章节标题、总结和各条笔记，不能留空或超出长度限制')
        db.snapshot_session(sid, '手动修改章节前')
        chapter.update(title=title.strip(), summary=summary.strip(), manual_edit=True)
        for point, value in zip(chapter.get('points', []), points):
            point.update(text=value.strip(), manual_edit=True)
        db.replace_topics(sid, chapters)
        db.update_session(sid, notes_data=json.dumps(notes, ensure_ascii=False))
        db.index_session(sid)
