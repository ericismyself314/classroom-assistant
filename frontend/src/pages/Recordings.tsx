import { AudioPlayer } from '../components/AudioPlayer';
import { useEffect, useState } from 'react';
import { SelectionIndicator } from '../components/SelectionIndicator';
import type { View } from '../App';
import { api } from '../api';
import { Icon } from '../components/Icon';
import { DeleteRecordingButton, RevealRecordingButton } from '../components/RecordingFileActions';
import { recordingDrafts } from '../recordingDrafts';
import { recordingDuration, recordingLibrary, recordingLocalTime, recordingSize } from '../recordingLibrary';
import type { RecordingCollection, RecordingFile, RecordingSession, RecoveryDraft } from '../recordingLibrary';
import './Recordings.css';

const tabs = { all: '全部', pending: '待处理', complete: '已整理', recovery: '待恢复' } as const;
const states: Record<string, string> = {
  completed: '已整理', ready_to_process: '待转写', waiting_upload: '待转写', needs_analysis: '待整理',
  waiting_model: '等待模型', file_check: '正在检查', transcribing: '正在转写', analyzing: '正在整理',
  indexing: '正在建立索引', cancelled: '处理已取消', failed: '处理失败',
};
const fileProblems = {
  missing: '找不到原文件', external: '位于课堂助手录音目录之外', damaged: '文件为空、长度不符或无法解码', unavailable: '文件暂时无法读取',
};
const isPending = (session: RecordingSession) =>
  ['ready_to_process', 'waiting_upload', 'needs_analysis', 'cancelled', 'failed'].includes(session.status);

type Player = { sessionId: string; index: number; fileKey?: string; play: boolean };
type Props = Extract<View, { name: 'recordings' }> & { navigate: (view: View) => void; refreshToken?: number };

export function Recordings({ navigate, courseId, sessionId, fileIndex, fileKey, filter, refreshToken = 0 }: Props) {
  const [collection, setCollection] = useState<RecordingCollection | null>(null);
  const [tab, setTab] = useState<keyof typeof tabs>(filter || 'all');
  const [course, setCourse] = useState(courseId || '');
  const [focusSession, setFocusSession] = useState(sessionId || '');
  const [search, setSearch] = useState('');
  const [player, setPlayer] = useState<Player | null>(sessionId && fileIndex !== undefined
    ? { sessionId, index: fileIndex, fileKey, play: false } : null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState('');
  const [activity, setActivity] = useState('');

  useEffect(() => {
    let current = true;
    setLoading(true); setError('');
    recordingLibrary.list().then(data => { if (current) setCollection(data); })
      .catch(cause => { if (current) setError(cause.message || '读取录音列表失败。'); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [reload, refreshToken]);

  const courses = new Map<string, string>();
  const query = search.trim().toLocaleLowerCase();
  const matches = (values: (string | null)[]) => values.some(value => value?.toLocaleLowerCase().includes(query));
  for (const item of [...(collection?.sessions || []), ...(collection?.drafts || [])]) {
    courses.set(item.course_id, item.course_name + (item.course_code ? ' · ' + item.course_code : ''));
  }
  const sessions = (collection?.sessions || []).filter(s => (!course || s.course_id === course)
    && (!focusSession || s.session_id === focusSession)
    && matches([s.course_name, s.course_code, s.title, s.session_date, ...s.files.flatMap(f => [f.name, recordingLocalTime(f.recorded_at)])]));
  const drafts = (collection?.drafts || []).filter(d => (!course || d.course_id === course)
    && (!focusSession || d.session_id === focusSession) && matches([d.course_name, d.course_code, d.title, d.session_date]));
  const visibleSessions = sessions.filter(s => tab === 'all' || (tab === 'pending' && isPending(s))
    || (tab === 'complete' && s.status === 'completed'));
  const visibleDrafts = tab === 'recovery' || (tab === 'all' && !visibleSessions.length) ? drafts : [];
  const courseLabel = (item: RecordingSession | RecoveryDraft) => item.course_name
    + (item.course_code && item.course_code !== item.course_name ? ' · ' + item.course_code : '');

  async function recover(draft: RecoveryDraft) {
    setActivity(draft.id); setError('');
    try {
      const saved = await recordingDrafts.finalize(draft.id, true);
      navigate({ name: 'recordings', sessionId: saved.session_id, fileIndex: saved.file.index, fileKey: saved.file.file_key });
      setReload(value => value + 1);
    } catch (cause) {
      setError(`恢复未完成。${cause instanceof Error ? cause.message : '请稍后重试。'} 原始片段仍保存在本机。`);
    } finally { setActivity(''); }
  }

  async function process(session: RecordingSession) {
    setActivity(session.session_id); setError('');
    try {
      await api.process(session.session_id, { mode: session.status === 'needs_analysis' ? 'analyze' : 'transcribe' });
      navigate({ name: 'session', id: session.session_id });
    } catch (cause) { setError(cause instanceof Error ? cause.message : '任务未能启动。'); }
    finally { setActivity(''); }
  }

  function listen(id: string, file: RecordingFile) {
    setPlayer({ sessionId: id, index: file.index, fileKey: file.file_key, play: true });
  }
  function recoveryButton(draft: RecoveryDraft) {
    if (draft.problem || draft.chunk_count === 0) return null;
    return <button type="button" disabled={!!activity} onClick={() => void recover(draft)}>
      {activity === draft.id ? '正在校验并恢复…' : '尝试恢复录音'}
    </button>;
  }
  async function revealDraft(draft: RecoveryDraft) {
    setActivity(draft.id + '-folder'); setError('');
    try { await recordingLibrary.revealDraft(draft.id); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '无法打开草稿所在文件夹。'); }
    finally { setActivity(''); }
  }

  return <main className="recording-library">
    <header className="surface-header recordings-header">
      <div><h1 className="page-title">我的录音</h1><p className="page-sub">按课堂日期查看本机录音，包含往期学期。</p></div>
      <button type="button" className="primary" onClick={() => navigate({ name: 'audio', source: 'microphone' })}><Icon name="mic" />去录音</button>
    </header>
    <section className="card recordings-filters" aria-label="筛选录音">
      <div className="recordings-selectors">
        <label className="recordings-search"><Icon name="search" /><input type="search" aria-label="搜索录音" placeholder="搜索课程、课堂、日期或文件名" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <select aria-label="按课程筛选录音" value={course} onChange={event => { setCourse(event.target.value); setFocusSession(''); }}>
          <option value="">全部课程</option>
          {course && !courses.has(course) && <option value={course}>当前课程</option>}
          {[...courses].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select>
        <button type="button" className="ghost" disabled={loading} onClick={() => setReload(n => n + 1)}>刷新</button>
      </div>
      <div className="recordings-tabs selection-group" aria-label="录音状态">
        <SelectionIndicator value={tab} />
        {(Object.entries(tabs) as [keyof typeof tabs, string][]).map(([key, label]) => <button type="button" key={key}
          aria-pressed={tab === key} onClick={() => setTab(key)}>{label}{key === 'recovery' && drafts.length > 0 ? ` · ${drafts.length}` : ''}</button>)}
        {focusSession && <button type="button" className="recordings-clear" onClick={() => { setFocusSession(''); setTab('all'); }}>显示全部课堂</button>}
      </div>
    </section>
    {error && <p className="quality-warning" role="alert">{error}</p>}
    {!collection && loading && <p className="empty" role="status">正在读取本机录音…</p>}
    {tab === 'all' && visibleSessions.length > 0 && drafts.length > 0 && <div className="recordings-draft-notice">
      <span>本机另有 {drafts.length} 段录音草稿。</span><button type="button" className="ghost" onClick={() => setTab('recovery')}>查看待恢复录音</button>
    </div>}
    {visibleDrafts.map(draft => <article key={draft.id} className="card recordings-draft">
      <div className="recordings-heading"><div><p className="recordings-meta">{draft.session_date} · {courseLabel(draft)}</p>
        <h2>{draft.title || draft.course_name}</h2></div><span className="recordings-state recovery">中断草稿</span></div>
      <p className="small muted">录制于 {recordingLocalTime(draft.created_at) || draft.session_date}</p>
      <p>{draft.chunk_count ? `已写入 ${draft.chunk_count} 个片段，共 ${recordingSize(draft.bytes)}。恢复后可回听并查看实际时长。` : '没有完整写入的片段。'}</p>
      <p className="small muted">{draft.problem || (draft.partial_write ? '检测到未写完的片段。恢复仅包含确认写入的部分。' : '关闭或断电前尚未写入的声音可能缺失。')}</p>
      <div className="recordings-actions">{recoveryButton(draft)}
        <button type="button" className="ghost" disabled={!!activity} onClick={() => void revealDraft(draft)}>查看草稿文件夹</button>
      </div>
    </article>)}
    {visibleSessions.map(session => {
      const canProcess = session.status === 'needs_analysis' ? !!session.has_transcript : session.files.every(file => file.availability === 'available');
      const canStart = ['ready_to_process', 'waiting_upload', 'needs_analysis'].includes(session.status);
      return <article key={session.session_id} className="card recordings-session">
        <div className="recordings-heading"><div><p className="recordings-meta"><time>{session.session_date}</time> · {courseLabel(session)}</p>
          <h2>{session.title || session.course_name}</h2></div>
          <span className={`recordings-state ${session.status === 'completed' ? 'complete' : ''}`}>{states[session.status] || '查看课堂状态'}</span>
        </div>
        <ul className="recordings-files">{session.files.map(file => {
          const selected = player?.sessionId === session.session_id && player.index === file.index && (!player.fileKey || player.fileKey === file.file_key);
          const draft = file.recovery_draft_id && collection?.drafts.find(d => d.id === file.recovery_draft_id);
          const localStart = recordingLocalTime(file.recorded_at);
          const oldMicrophoneName = !!localStart && /^课堂录音-\d{4}-\d{2}-\d{2}T/.test(file.name);
          return <li key={file.file_key}>
            <div className="recording-file-line">
              <div className="recording-file-icon"><Icon name="volume" /></div>
              <div className="recording-file-title"><strong title={file.name}>{oldMicrophoneName ? '课堂录音' : file.name}</strong>
                <span>{[oldMicrophoneName ? '录制于 ' + localStart : '', recordingDuration(file.duration), recordingSize(file.bytes), file.interrupted ? '中断后保存的部分' : ''].filter(Boolean).join(' · ')}</span></div>
              {file.availability === 'available' ? <div className="recordings-actions"><button type="button" onClick={() => selected ? setPlayer(null) : listen(session.session_id, file)}>
                {selected ? '收起播放器' : '回听'}</button><RevealRecordingButton sessionId={session.session_id} index={file.index} fileKey={file.file_key} /></div>
                : <span className="recordings-problem">{fileProblems[file.availability]}</span>}
              {session.status === 'ready_to_process' && file.availability !== 'external' && <DeleteRecordingButton
                sessionId={session.session_id} file={file} disabled={!!activity || loading}
                beforeDelete={() => setPlayer(null)} onDeleted={() => setReload(n => n + 1)} />}
            </div>
            {selected && file.availability === 'available' && <div className="recording-inline-player">
              <AudioPlayer key={file.file_key} sessionId={session.session_id} compact autoPlay={player.play}
                initialDuration={file.duration} seekSignal={null} source={recordingLibrary.audioUrl(session.session_id, file.index, file.file_key)} />
            </div>}
            {draft && <div className="recordings-recover-file"><span>发现本机恢复副本。</span>{recoveryButton(draft)}</div>}
          </li>;
        })}</ul>
        <div className="recordings-footer">
          <span className="small muted">{session.semester_name} · {session.files.length} 段录音</span>
          <div className="recordings-actions">
            {canStart && <button type="button" className="primary" disabled={!!activity || !canProcess}
              onClick={() => void process(session)}>{activity === session.session_id ? '正在启动…' : session.status === 'needs_analysis' ? '整理现有转写' : '开始转写并整理'}</button>}
            <button type="button" className="ghost" onClick={() => navigate({ name: 'session', id: session.session_id })}>
              {session.status === 'completed' ? '打开课堂笔记' : ['cancelled', 'failed'].includes(session.status) ? '查看并继续处理' : '打开课堂'}</button>
          </div>
        </div>
      </article>;
    })}
    {collection && !visibleDrafts.length && !visibleSessions.length && <div className="card recordings-empty">
      <Icon name="volume" /><h2>{tab === 'recovery' ? '没有待恢复的录音' : search || course || focusSession || tab !== 'all' ? '没有符合条件的录音' : '这里还没有录音'}</h2>
      {!search && !course && !focusSession && tab === 'all' && <p>结束麦克风录音或导入音频后，可在这里找到原文件。</p>}
    </div>}
  </main>;
}
