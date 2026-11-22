"""Find registered class recordings and address a file by its class and index."""
from __future__ import annotations

from functools import lru_cache
from datetime import datetime
import hashlib
import os
import re
import stat

from . import config, db, hashutil, recording_drafts


@lru_cache(maxsize=256)
def _legacy_audio_readable(path, size, modified_ns):
    # Old rows have no stored byte count. Probe one frame, once per file version.
    return not hashutil.is_audio_corrupted(path)


def file_info(entry):
    path = entry['path']
    identity = os.path.normcase(os.path.basename(path)) + '\0' + (entry.get('hash') or '')
    result = {'index': entry['index'], 'name': entry['name'], 'duration': entry.get('duration'),
              'bytes': entry.get('bytes'), 'availability': 'external',
              'file_key': entry.get('file_key') or hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}
    old_name = re.fullmatch(r'课堂录音-(\d{4}-\d{2}-\d{2}T\d{2})-(\d{2})-(\d{2})([+-]\d{2})-(\d{2})\.(?:wav|webm|mp4)',
                            entry['name'])
    if old_name:
        date_hour, minute, second, offset_hour, offset_minute = old_name.groups()
        try:
            result['recorded_at'] = datetime.fromisoformat(
                f'{date_hour}:{minute}:{second}{offset_hour}:{offset_minute}').isoformat()
        except ValueError:
            pass
    if not os.path.isabs(path) or not hashutil.is_supported_audio(path):
        return result
    root = os.path.normcase(os.path.abspath(config.get_config().dirs.audio))
    real_root = os.path.normcase(os.path.realpath(root))
    try:
        # Check the stored location before resolving links to avoid probing an external share.
        if os.path.commonpath((root, os.path.normcase(os.path.abspath(path)))) != root:
            return result
        if os.path.commonpath((real_root, os.path.normcase(os.path.realpath(path)))) != real_root:
            return result
        details = os.stat(path)
    except FileNotFoundError:
        result['availability'] = 'missing'
        return result
    except (OSError, ValueError):
        result['availability'] = 'unavailable'
        return result
    if (not stat.S_ISREG(details.st_mode) or not details.st_size
            or (entry.get('bytes') is not None and details.st_size != entry['bytes'])
            or (entry.get('bytes') is None and not _legacy_audio_readable(path, details.st_size, details.st_mtime_ns))):
        result['availability'] = 'damaged'
    else:
        result.update(availability='available', bytes=details.st_size)
    return result


def library():
    rows = db.read_conn().execute(
        """SELECT s.id, s.title, s.session_date, s.status, s.audio_path, s.audio_manifest,
                  s.audio_hash, s.audio_duration_seconds, s.course_id, c.name AS course_name,
                  c.code AS course_code, sem.name AS semester_name,
                  EXISTS(SELECT 1 FROM transcript_segments t WHERE t.session_id=s.id) AS has_transcript
           FROM class_sessions s JOIN courses c ON c.id=s.course_id
           JOIN semesters sem ON sem.id=c.semester_id
           WHERE s.audio_path IS NOT NULL OR s.audio_manifest IS NOT NULL
           ORDER BY s.session_date DESC, s.created_at DESC, s.rowid DESC""").fetchall()
    sessions = []
    draft_files = {}
    for row in rows:
        session = dict(row)
        files = []
        for entry in db.audio_files_for_session(session):
            item = file_info(entry)
            files.append(item)
            match = re.fullmatch(r'draft-([a-f0-9]{32})\.(?:webm|mp4|wav)', os.path.basename(entry['path']))
            if match:
                draft_files[(session['id'], match.group(1))] = item
        if files:
            sessions.append({key: session[key] for key in ('course_id', 'course_name', 'course_code',
                              'semester_name', 'title', 'session_date', 'status', 'has_transcript')}
                            | {'session_id': session['id'], 'files': files})
    drafts = recording_drafts.library_drafts(draft_files)
    courses = {row['id']: row for row in db.read_conn().execute('SELECT id, name, code FROM courses')} if drafts else {}
    for draft in drafts:
        course = courses.get(draft['course_id'])
        draft.update(course_name=course['name'] if course else '原课程已移除',
                     course_code=course['code'] if course else None)
    return {'sessions': sessions, 'drafts': drafts}


def resolve_file(session_id, index, key=None):
    entries = db.audio_files_for_session(session_id)
    entry = next((value for value in entries if value['index'] == index), None)
    if not entry:
        raise recording_drafts.DraftError('课堂中没有这段录音，请刷新我的录音。', 404)
    info = file_info(entry)
    if key is not None and key != info['file_key']:
        raise recording_drafts.DraftError('录音清单已变化，请刷新后重新选择。', 409)
    errors = {'external': ('文件位于课堂助手录音目录之外，无法在这里打开。', 403),
              'missing': ('原录音文件已找不到，请在我的录音查看可恢复草稿。', 404),
              'damaged': ('原文件为空、长度不符或无法解码，请在我的录音检查恢复副本。', 409),
              'unavailable': ('本机暂时无法读取这段录音。', 409)}
    if info['availability'] in errors:
        raise recording_drafts.DraftError(*errors[info['availability']])
    return os.path.realpath(entry['path'])


def _select_in_folder(path):
    # Use the Windows Shell selection API without running the recording or a command.
    if os.name != 'nt':
        raise OSError('此系统不支持 Windows 资源管理器')
    import ctypes
    ole = ctypes.OleDLL('ole32')
    shell = ctypes.OleDLL('shell32')
    ole.CoInitialize(None)
    pidl = ctypes.c_void_p()
    try:
        shell.SHParseDisplayName(ctypes.c_wchar_p(path), None, ctypes.byref(pidl), 0, None)
        shell.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
    finally:
        if pidl.value:
            ctypes.windll.ole32.CoTaskMemFree(pidl)
        ctypes.windll.ole32.CoUninitialize()


def reveal(session_id, index, body):
    if not isinstance(body, dict) or set(body) - {'file_key'}:
        raise recording_drafts.DraftError('请从课堂录音清单选择文件。')
    return show_in_folder(resolve_file(session_id, index, body.get('file_key')))


def show_in_folder(path):
    try:
        _select_in_folder(path)
    except OSError as error:
        raise recording_drafts.DraftError('资源管理器未能显示文件，请稍后重试。', 503) from error
    return {'ok': True}
