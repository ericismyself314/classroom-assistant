"""Durable local microphone recording fragments, independent of the WebView origin."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

from . import config, db, hashutil

_LOCK = threading.RLock()
_ACTIVE = {}
_INDEX = {}
_ACTIVE_SECONDS = 15
_MAX_CHUNK = 32 * 1024 * 1024


class DraftError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _root():
    path = os.path.join(config.get_config().dirs.data_dir, 'recording-drafts')
    os.makedirs(path, exist_ok=True)
    return path


def _dir(draft_id):
    if not re.fullmatch(r'[a-f0-9]{32}', draft_id):
        raise DraftError('录音草稿不存在', 404)
    return os.path.join(_root(), draft_id)


def _write_json(path, value):
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def _read(draft_id):
    try:
        with open(os.path.join(_dir(draft_id), 'draft.json'), encoding='utf-8') as f:
            draft = json.load(f)
        if not isinstance(draft, dict) or draft.get('id') != draft_id:
            raise DraftError('录音草稿元数据异常，原始文件已保留', 409)
        if draft.get('status') == 'deleting':
            _resolve_delete_intent(draft)
        return draft
    except FileNotFoundError:
        raise DraftError('录音草稿不存在', 404)


def _save(draft):
    _write_json(os.path.join(_dir(draft['id']), 'draft.json'), draft)


def _raw_chunks(draft_id):
    directory = _dir(draft_id)
    names = sorted(n for n in os.listdir(directory) if re.fullmatch(r'\d{10}\.chunk', n))
    for index, name in enumerate(names):
        if name != f'{index:010d}.chunk':
            raise DraftError('录音草稿分片不连续，请保留草稿并联系支持', 409)
    return [os.path.join(directory, n) for n in names]


def _chunks(draft_id):
    directory = _dir(draft_id)
    archived = os.path.join(directory, 'archived.webm')
    if os.path.isfile(archived):
        return [archived]
    recovery = [os.path.join(directory, name) for name in os.listdir(directory)
                if re.fullmatch(r'recovery\.(webm|mp4|wav)', name)]
    if len(recovery) > 1:
        raise DraftError('录音恢复副本异常，原始草稿已保留', 409)
    return recovery or _raw_chunks(draft_id)


def _index(draft_id):
    key = _dir(draft_id)
    if key not in _INDEX:
        parts = _chunks(draft_id)
        _INDEX[key] = (len(parts), sum(os.path.getsize(path) for path in parts))
    return _INDEX[key]


def _saved_file(draft):
    from .recording_library import file_info
    sid = draft.get('session_id')
    if not sid:
        return None
    prefix = 'draft-' + draft['id'] + '.'
    for entry in db.audio_files_for_session(sid):
        if os.path.basename(entry['path']).startswith(prefix):
            info = file_info(entry)
            if info['availability'] == 'available':
                info['recorded_at'] = draft.get('created_at')
                return info
    return None


def _digest(paths):
    digest = hashlib.sha256()
    for path in paths:
        with open(path, 'rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
    return digest.hexdigest()


def draft_file_id(filename):
    match = re.fullmatch(r'draft-([a-f0-9]{32})\.(?:webm|mp4|wav)', filename)
    return match.group(1) if match else None


def archive_recovery(sid, entry):
    """Move the saved microphone receipt's recovery copy to the verified speech file."""
    identifier = draft_file_id(os.path.basename(entry['path']))
    if not identifier or not os.path.isfile(os.path.join(_dir(identifier), 'draft.json')):
        return
    with _LOCK:
        draft = _read(identifier)
        if draft.get('session_id') != sid or draft.get('status') != 'saved':
            return
        directory = _dir(identifier)
        recovery = os.path.join(directory, 'archived.webm')
        if not os.path.isfile(recovery) or not os.path.samefile(recovery, entry['path']):
            if not (draft.get('recovery_hash') == entry['hash'] and os.path.isfile(recovery)
                    and _digest([recovery]) == entry['hash']):
                tmp = recovery + '.link-tmp'
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    try:
                        os.link(entry['path'], tmp)
                    except OSError:
                        shutil.copyfile(entry['path'], tmp)
                    os.replace(tmp, recovery)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
        for name in os.listdir(directory):
            if re.fullmatch(r'\d{10}\.chunk|recovery\.(webm|mp4|wav)', name):
                os.unlink(os.path.join(directory, name))
        if draft.get('recovery_hash') != entry['hash']:
            draft.update(compacted=True, recovery_hash=entry['hash'])
            _save(draft)
        _INDEX.pop(directory, None)


def _compact_saved(draft):
    if draft.get('status') != 'saved' or draft.get('interrupted'):
        return
    prefix = 'draft-' + draft['id'] + '.'
    entry = next((entry for entry in db.audio_files_for_session(draft.get('session_id'))
                  if os.path.basename(entry['path']).startswith(prefix) and os.path.isfile(entry['path'])), None)
    if not entry:
        return
    if entry.get('archived'):
        archive_recovery(draft['session_id'], entry)
        return
    recovery = os.path.join(_dir(draft['id']), 'recovery' + os.path.splitext(entry['path'])[1])
    try:
        raw = _raw_chunks(draft['id'])
        if not raw and os.path.isfile(recovery) and os.path.samefile(entry['path'], recovery):
            return
        digest = _digest([entry['path']])
        if entry.get('hash') and digest != entry['hash']:
            return
        parts = raw or _chunks(draft['id'])
        if parts and _digest(parts) != digest:
            return
        temporary = recovery + '.link-tmp'
        try:
            os.link(entry['path'], temporary)
            os.replace(temporary, recovery)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        # Keep a second name for accidental path loss without a second data copy.
        draft.update(compacted=True, recovery_hash=digest)
        _save(draft)
        for path in raw:
            try:
                os.unlink(path)
            except OSError:
                pass
        _INDEX.pop(_dir(draft['id']), None)
    except (OSError, DraftError):
        # Filesystems without hard links retain the original recoverable fragments.
        return


def _needs_compaction(draft):
    if draft.get('status') != 'saved' or draft.get('interrupted'):
        return False
    if not draft.get('compacted'):
        return True
    prefix = 'draft-' + draft['id'] + '.'
    entry = next((entry for entry in db.audio_files_for_session(draft.get('session_id'))
                  if os.path.basename(entry['path']).startswith(prefix) and os.path.isfile(entry['path'])), None)
    if not entry:
        return False
    if entry.get('archived'):
        return not os.path.isfile(os.path.join(_dir(draft['id']), 'archived.webm'))
    recovery = os.path.join(_dir(draft['id']), 'recovery' + os.path.splitext(entry['path'])[1])
    try:
        return not os.path.samefile(entry['path'], recovery)
    except OSError:
        return True


def _clear_deleted_audio(draft):
    directory = _dir(draft['id'])
    for name in os.listdir(directory):
        if re.fullmatch(r'\d{10}\.chunk(?:\.tmp)?|(?:archived\.webm|recovery\.(webm|mp4|wav))(?:\.link-tmp)?', name):
            try:
                os.unlink(os.path.join(directory, name))
            except OSError:
                pass
    _ACTIVE.pop(draft['id'], None)
    _INDEX.pop(directory, None)


def _resolve_delete_intent(draft):
    paths = {os.path.normcase(os.path.abspath(path)) for path in draft.get('delete_paths', [])}
    current = db.audio_files_for_session(draft.get('delete_session_id'))
    referenced = any(os.path.normcase(os.path.abspath(entry['path'])) in paths for entry in current)
    draft['status'] = draft.get('delete_previous_status', 'saved') if referenced else 'deleted'
    for field in ('delete_paths', 'delete_previous_status', 'delete_session_id'):
        draft.pop(field, None)
    _save(draft)
    if not referenced:
        _clear_deleted_audio(draft)


@contextmanager
def deleting_audio(session_paths):
    """Commit deletion receipts only after the caller's file/DB work succeeds."""
    with _LOCK:
        originals = []
        pending = []
        try:
            with db.write_txn():
                if any(job.get('session_id') in session_paths for job in db.active_jobs()):
                    raise DraftError('这节课正在处理，完成或取消后再删除录音。', 409)
                for name in os.listdir(_root()):
                    if not re.fullmatch(r'[a-f0-9]{32}', name):
                        continue
                    try:
                        draft = _read(name)
                    except (DraftError, OSError, ValueError):
                        continue
                    if draft.get('status') == 'deleted':
                        continue
                    prefix = 'draft-' + name + '.'
                    sid = draft.get('session_id') or ('sess_' + name)
                    paths = session_paths.get(sid, [])
                    matched = [path for path in paths if os.path.basename(path).startswith(prefix)]
                    if not matched:
                        continue
                    originals.append(dict(draft))
                    draft.update(status='deleting', delete_previous_status=draft['status'],
                                 delete_session_id=sid, delete_paths=matched)
                    _save(draft)
                    pending.append(draft)
                yield
        except BaseException:
            for original in originals:
                try:
                    _save(original)
                except OSError:
                    pass
            raise
        else:
            for draft in pending:
                draft['status'] = 'deleted'
                draft['deleted_at'] = db.now_iso()
                for field in ('delete_paths', 'delete_previous_status', 'delete_session_id'):
                    draft.pop(field, None)
                _save(draft)
                _clear_deleted_audio(draft)


def reset_runtime_state():
    with _LOCK:
        _ACTIVE.clear()
        _INDEX.clear()


def has_active_recordings():
    now = time.monotonic()
    return any(now - stamp < _ACTIVE_SECONDS and os.path.isfile(os.path.join(_dir(identifier), 'draft.json'))
               for identifier, stamp in _ACTIVE.items())


def _public(draft):
    count, total = _index(draft['id'])
    result = {**draft, 'chunk_count': count, 'bytes': total,
            'active': time.monotonic() - _ACTIVE.get(draft['id'], -1e30) < _ACTIVE_SECONDS}
    if draft['status'] in {'deleted', 'deleting'}:
        result.update(status='saved', deleted=True)
    elif draft['status'] == 'saved':
        file = _saved_file(draft)
        if file is None:
            result['status'] = 'recording'
        else:
            result['file'] = file
    return result


def _saved_receipt(draft):
    receipt = _public(draft)
    if receipt['status'] != 'saved' or not receipt.get('file'):
        raise DraftError('暂时无法确认录音原文件，草稿已保留。', 409)
    return receipt


def fragment_location(draft_id):
    with _LOCK:
        root = os.path.normcase(os.path.realpath(_root()))
        directory = os.path.normcase(os.path.realpath(_dir(draft_id)))
        if os.path.dirname(directory) != root:
            raise DraftError('草稿位置不在应用录音目录内。', 403)
        draft = _read(draft_id)
        if draft['status'] in {'deleted', 'deleting'}:
            raise DraftError('草稿已主动移除。', 410)
        names = [n for n in os.listdir(directory) if re.fullmatch(r'archived\.webm|recovery\.(?:wav|webm|mp4)|\d{10}\.chunk(?:\.tmp)?', n)]
        for name in sorted(names):
            path = os.path.realpath(os.path.join(directory, name))
            if os.path.normcase(os.path.dirname(path)) == directory and os.path.isfile(path):
                return path
        raise DraftError('找不到草稿录音片段。', 404)


def library_drafts(registered_files):
    """Read the persisted receipts without compacting audio or probing every saved chunk."""
    results = []
    with _LOCK:
        for identifier in os.listdir(_root()):
            if not re.fullmatch(r'[a-f0-9]{32}', identifier):
                continue
            try:
                draft = _read(identifier)
                if draft['status'] in {'deleted', 'deleting'}:
                    continue
                sid = draft.get('session_id') or draft.get('target_session_id') or ('sess_' + identifier)
                file = registered_files.get((sid, identifier))
                if file is not None and draft.get('created_at'):
                    file['recorded_at'] = draft['created_at']
                if file is not None and file['availability'] == 'available':
                    if draft.get('interrupted'):
                        file['interrupted'] = True
                    continue
                if time.monotonic() - _ACTIVE.get(identifier, -1e30) < _ACTIVE_SECONDS:
                    continue
                directory = _dir(identifier)
                names = os.listdir(directory)
                partial = any(re.fullmatch(r'\d{10}\.chunk\.tmp', name) for name in names)
                problem = None
                try:
                    count, size = _index(identifier)
                except DraftError as error:
                    problem = str(error)
                    fragments = [name for name in names if re.fullmatch(r'\d{10}\.chunk|recovery\.(?:webm|mp4|wav)', name)]
                    count = len(fragments)
                    size = sum(os.path.getsize(os.path.join(directory, name)) for name in fragments)
                if not count and not partial:
                    continue
                if file is not None:
                    file['recovery_draft_id'] = identifier
                results.append({key: draft.get(key) for key in ('id', 'title', 'course_id', 'session_date', 'created_at')}
                               | {'session_id': sid if file is not None else draft.get('target_session_id'),
                                  'chunk_count': count, 'bytes': size, 'partial_write': partial,
                                  'problem': problem, 'linked_file': file is not None})
            except (KeyError, OSError, ValueError, DraftError):
                continue
    return sorted(results, key=lambda item: item.get('created_at') or '', reverse=True)


def create(body):
    with _LOCK:
        cid, sid = body.get('course_id'), body.get('session_id')
        course = db.get_course(cid) if isinstance(cid, str) else None
        if not course or not isinstance(body.get('session_date'), str) or not body['session_date']:
            raise DraftError('请选择课程和课堂日期')
        session = db.get_session(sid) if sid else None
        if sid and (not session or session['course_id'] != cid):
            raise DraftError('课堂不存在或不属于所选课程')
        mime = body.get('mime_type') or 'audio/webm'
        if not isinstance(mime, str) or mime.split(';')[0] not in ('audio/webm', 'audio/mp4', 'audio/wav'):
            raise DraftError('不支持的录音格式')
        draft = {'id': uuid.uuid4().hex, 'course_id': cid, 'session_id': sid or None,
                 'target_session_id': sid or None,
                 'session_date': body['session_date'], 'title': str(body.get('title') or '')[:255],
                 'mime_type': mime, 'status': 'recording', 'created_at': db.now_iso()}
        os.makedirs(_dir(draft['id']))
        _save(draft)
        _ACTIVE[draft['id']] = time.monotonic()
        return _public(draft)


def list_drafts():
    with _LOCK:
        drafts = []
        may_maintain = not has_active_recordings() and not db.active_jobs()
        maintained = False
        for name in os.listdir(_root()):
            if not re.fullmatch(r'[a-f0-9]{32}', name):
                continue
            try:
                draft = _read(name)
                if draft.get('status') == 'deleted' and not maintained and _chunks(name):
                    _clear_deleted_audio(draft)
                    maintained = True
                if may_maintain and not maintained and _needs_compaction(draft):
                    _compact_saved(draft)
                    maintained = True
                drafts.append(_public(draft))
            except (OSError, ValueError, DraftError):
                continue
        return {'drafts': drafts}


def heartbeat(draft_id):
    with _LOCK:
        draft = _read(draft_id)
        if draft['status'] != 'recording':
            raise DraftError('这段录音已经保存', 409)
        _ACTIVE[draft_id] = time.monotonic()
        return _public(draft)


def put_chunk(draft_id, index, data):
    with _LOCK:
        draft = _read(draft_id)
        if draft['status'] != 'recording':
            raise DraftError('这段录音已经保存', 409)
        if not data or len(data) > _MAX_CHUNK:
            raise DraftError('录音分片为空或超过大小上限')
        count, total = _index(draft_id)
        if index > count:
            raise DraftError('录音分片顺序不连续', 409)
        path = os.path.join(_dir(draft_id), f'{index:010d}.chunk')
        if index < count:
            try:
                with open(path, 'rb') as f:
                    if f.read() != data:
                        raise DraftError('录音分片与已保存数据不一致', 409)
            except FileNotFoundError:
                raise DraftError('录音草稿分片缺失，原始草稿已保留', 409)
        else:
            temporary = path + '.tmp'
            with open(temporary, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
            _INDEX[_dir(draft_id)] = (count + 1, total + len(data))
        _ACTIVE[draft_id] = time.monotonic()
        return _public(draft)


def finalize(draft_id, interrupted=False, capture_interrupted=False):
    with _LOCK:
        draft = _read(draft_id)
        if draft['status'] in {'deleted', 'deleting'}:
            raise DraftError('这段录音已主动删除，不能再次恢复', 410)
        if draft['status'] == 'saved' and _saved_file(draft):
            _compact_saved(draft)
            return _saved_receipt(draft)
        if interrupted and _public(draft)['active']:
            raise DraftError('该录音仍在进行，请稍后重试', 409)
        parts = _chunks(draft_id)
        if not parts:
            raise DraftError('草稿没有可恢复的录音分片', 409)
        course = db.get_course(draft['course_id'])
        if not course:
            raise DraftError('原课程已不存在，草稿已保留', 409)
        sid = draft.get('target_session_id')
        existing_target = bool(sid)
        if existing_target:
            sess = db.get_session(sid)
            if not sess or sess['course_id'] != draft['course_id']:
                raise DraftError('原课堂已不存在，草稿已保留', 409)
        else:
            # Stable ID avoids both same-day merging and duplicate sessions after a crash.
            sid = 'sess_' + draft_id
        if any(job.get('session_id') == sid for job in db.active_jobs()):
            raise DraftError('课堂正在处理，录音草稿已保留，请稍后重试', 409)
        ext = {'audio/webm': '.webm', 'audio/mp4': '.mp4', 'audio/wav': '.wav'}[draft['mime_type'].split(';')[0]]
        directory = config.get_config().dirs.audio_dir_for(course['semester_id'], draft['course_id'], sid)
        dest = os.path.join(directory, 'draft-' + draft_id + ext)
        if draft.get('status') == 'saved':
            archived = next((e for e in db.audio_files_for_session(sid) if e.get('archived') and
                             draft_file_id(os.path.basename(e['path'])) == draft_id), None)
            if archived:
                expected = os.path.join(directory, 'voice-archive', 'draft-' + draft_id + '.webm')
                if os.path.normcase(os.path.realpath(archived['path'])) != os.path.normcase(os.path.abspath(expected)):
                    raise DraftError('录音恢复位置不匹配，恢复副本已保留。', 409)
                os.makedirs(os.path.dirname(expected), exist_ok=True)
                dest = expected
        # A deterministic destination and DB path check make retries safe even after a lost response.
        with db.write_txn():
            if any(job.get('session_id') == sid for job in db.active_jobs()):
                raise DraftError('课堂正在处理，录音草稿已保留，请稍后重试', 409)
            sess = db.get_session(sid)
            if not sess and not existing_target:
                now = db.now_iso()
                db.get_conn().execute(
                    'INSERT INTO class_sessions(id,course_id,session_date,title,status,created_at,updated_at) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (sid, draft['course_id'], draft['session_date'], draft['title'], 'waiting_upload', now, now))
                sess = db.get_session(sid)
            if not sess or sess['course_id'] != draft['course_id']:
                raise DraftError('原课堂已不存在，草稿已保留', 409)
            if sess.get('audio_manifest'):
                try:
                    raw = json.loads(sess['audio_manifest'])
                    if not isinstance(raw, list) or any(not isinstance(e, dict) or not e.get('path') for e in raw):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise DraftError('课堂原录音清单异常，草稿已保留', 409)
            entries = db.audio_files_for_session(sess)
            already_saved = any(os.path.normcase(e['path']) == os.path.normcase(dest) for e in entries)
            if not already_saved or not _saved_file(dict(draft, session_id=sid)):
                digest = hashlib.sha256()
                temporary = dest + '.tmp'
                with open(temporary, 'wb') as out:
                    for part in parts:
                        with open(part, 'rb') as inp:
                            for block in iter(lambda: inp.read(1024 * 1024), b''):
                                out.write(block)
                                digest.update(block)
                    out.flush()
                    os.fsync(out.fileno())
                if hashutil.is_audio_corrupted(temporary):
                    raise DraftError('录音暂时无法解码，原始草稿已保留', 422)
                if already_saved:
                    old_entry = next(e for e in entries if os.path.normcase(e['path']) == os.path.normcase(dest))
                    if old_entry.get('hash') and old_entry['hash'] != digest.hexdigest():
                        raise DraftError('草稿与课堂录音校验值不一致，原始草稿已保留', 409)
                os.replace(temporary, dest)
                if not already_saved:
                    duration = hashutil.audio_duration_seconds(dest)
                    local_start = datetime.fromisoformat(draft['created_at']).astimezone().strftime('%Y-%m-%d %H时%M分%S秒')
                    entries.append({'path': dest, 'name': '课堂录音 ' + local_start + ext,
                                    'bytes': os.path.getsize(dest), 'duration': duration, 'hash': digest.hexdigest()})
                    lengths = [e.get('duration') for e in entries]
                    total = sum(float(v) for v in lengths) if all(v is not None for v in lengths) else None
                    from .api import _audio_collection_hash
                    db.update_session(sid, audio_path=entries[0]['path'], audio_hash=_audio_collection_hash(entries),
                                      audio_duration_seconds=total, audio_manifest=json.dumps(entries, ensure_ascii=False),
                                      status='ready_to_process', source_type='audio')
        draft.update(status='saved', session_id=sid,
                     interrupted=bool(interrupted or capture_interrupted or draft.get('interrupted')))
        _save(draft)
        _ACTIVE.pop(draft_id, None)
        _compact_saved(draft)
        return _saved_receipt(draft)
