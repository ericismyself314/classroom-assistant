import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { api } from '../api';
import { MicrophoneRecorder, RecorderStart } from '../components/MicrophoneRecorder';
import { RecognitionSettings, useRecognition } from '../components/RecognitionSettings';
import { MAX_AUDIO_FILES, mergeAudioFiles, totalAudioBytes } from '../audioFiles';
import { localDateISO } from '../localDate';
import { recordingCourseNow } from '../schedule';
import { Course, Session } from '../types';
import { View } from '../App';
import { TranscriptPreview } from '../components/TranscriptPreview';
import { Icon } from '../components/Icon';

const NEW_SESSION = '__new__';
const styledPicker = globalThis.CSS?.supports('appearance: base-select') && CSS.supports('selector(::picker(select))');
const sessionStates: Record<string, string> = { ready_to_process: '待处理', completed: '已完成', failed: '处理失败' };

function courseLabel(course: Course): string {
  const name = course.name.trim();
  const code = course.code?.trim();
  if (!name) return code || '未命名课程';
  return code && code.toLowerCase() !== name.toLowerCase() ? `${name} · ${code}` : name;
}

function audioCount(session?: Session): number {
  if (!session) return 0;
  if (session.audio_manifest) {
    try {
      const entries = JSON.parse(session.audio_manifest);
      if (Array.isArray(entries)) return entries.length;
    } catch { /* use the legacy single-file field below */ }
  }
  return session.audio_path ? 1 : 0;
}

export function AudioWorkspace({ navigate, courseId: initialCourseId, sessionId: initialSessionId, date, title, source: initialSource, visible, onRecordingChange, onRecordingNotice, onRecordingSaved }: {
  navigate: (view: View) => void; courseId?: string; sessionId?: string; date?: string; title?: string;
  source?: 'microphone' | 'upload'; visible: boolean; onRecordingChange: (active: boolean) => void; onRecordingNotice: (message: string) => void; onRecordingSaved: () => void;
}) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [courseId, setCourseId] = useState(initialCourseId || '');
  const [sessionId, setSessionId] = useState(initialSessionId || NEW_SESSION);
  const [sessionDate, setSessionDate] = useState(date || localDateISO());
  const [sessionTitle, setSessionTitle] = useState(title || '');
  const [coursesLoading, setCoursesLoading] = useState(true);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [targetLocked, setTargetLocked] = useState(false);
  const [editing, setEditing] = useState(false);
  const [targetVersion, setTargetVersion] = useState(0);
  const [source, setSource] = useState<'microphone' | 'upload'>(initialSource || 'microphone');
  const [selectionHint, setSelectionHint] = useState('');
  const pinned = useRef(false);
  const picker = useRef<HTMLSelectElement>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    api.courses().then(async ({ courses: result }: { courses: Course[] }) => {
      if (initialCourseId && !result.some(item => item.id === initialCourseId)) {
        try {
          const prior = await api.getCourse(initialCourseId);
          if (prior.course?.id === initialCourseId) result = [...result, prior.course];
        } catch { if (live) setError('原课程暂时无法打开，请选择课程。'); }
      }
      if (!live) return;
      setCourses(result);
      setCoursesLoading(false);
    }).catch((e) => {
      if (live) { setError(e.message || '课程列表加载失败'); setCoursesLoading(false); }
    });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (initialCourseId || initialSessionId || pinned.current) return;
    if (!visible) { setCourseId(''); return; }
    let live = true;
    const today = localDateISO();
    api.week(today).then(plan => {
      if (!live || pinned.current) return;
      const now = new Date();
      setCourseId(recordingCourseNow(plan.week || [], localDateISO(now), now.getHours() * 60 + now.getMinutes()));
      setSessionDate(localDateISO(now));
    }).catch(() => { if (live && !pinned.current) setCourseId(''); });
    return () => { live = false; };
  }, [visible, initialCourseId, initialSessionId]);

  useEffect(() => {
    if (!courseId) { setSessions([]); setSessionsLoading(false); return; }
    let live = true;
    setSessionsLoading(true);
    api.sessions(courseId).then(({ sessions: result }) => {
      if (!live) return;
      setSessions(result);
      setSessionId(current => result.some((s: Session) => s.id === current) ? current : NEW_SESSION);
      setSessionsLoading(false);
    }).catch((e) => {
      if (live) { setError(e.message || '课堂列表加载失败'); setSessionsLoading(false); }
    });
    return () => { live = false; };
  }, [courseId]);

  const course = courses.find((item) => item.id === courseId);
  const session = sessions.find((item) => item.id === sessionId);
  const targetDate = session?.session_date || sessionDate;
  const displayDate = targetDate ? new Date(targetDate + 'T12:00:00').toLocaleDateString('zh-CN', {
    year: targetDate.slice(0, 4) === String(new Date().getFullYear()) ? undefined : 'numeric', month: 'long', day: 'numeric',
  }) : '选择日期';
  function manualTarget() {
    pinned.current = true;
    setTargetVersion(n => n + 1);
    setError('');
  }
  function changeCourse(id: string) {
    manualTarget(); setSelectionHint(''); setCourseId(id); setSessions([]); setSessionsLoading(!!id);
    setSessionId(NEW_SESSION); setSessionTitle('');
  }
  function switchSource(value: 'microphone' | 'upload') {
    pinned.current = true; setSource(value); setEditing(false);
  }
  function askForCourse(value: 'microphone' | 'upload') {
    switchSource(value);
    setSelectionHint(value === 'upload' ? '选好课程后即可导入录音。' : '选好课程后即可开始录音。');
    picker.current?.focus();
    try { picker.current?.showPicker?.(); } catch { /* The focused native select still works with the keyboard. */ }
  }
  const courseSelect = <select ref={picker} className="app-select audio-course-select" aria-label="课程" value={course?.id || ''}
    disabled={coursesLoading || !courses.length || targetLocked} onFocus={() => { pinned.current = true; }}
    onChange={e => changeCourse(e.target.value)}>
    {!course && <option value="">{coursesLoading ? '正在读取课程…' : '选择课程'}</option>}
    {courses.map(item => <option key={item.id} value={item.id} style={{ '--course-color': item.color } as CSSProperties}>{courseLabel(item)}</option>)}
  </select>;

  return (
    <div className="audio-workspace">
      <div className="audio-workspace-header">
        <h1 className="page-title">录音与转写</h1>
      </div>
      <div className="card capture-sheet" data-source={source}>
      <section className="capture-target" aria-label="课程与课堂设置">
        <div className="capture-coursebar">
          <Icon name="book" width="18" height="18" />
          {course ? <strong>{courseLabel(course)}</strong> : courseSelect}
          <span className="capture-separator" aria-hidden="true">·</span><time dateTime={targetDate}>{displayDate}</time>
          {session && <span className="capture-append">{session.title && session.title !== course?.name ? `${session.title} · ` : ''}{audioCount(session) ? `已有 ${audioCount(session)} 段录音` : '已有课堂'}</span>}
          {course && !session && sessionTitle && sessionTitle !== course.name && <span className="capture-append">{sessionTitle}</span>}
          {course && <button type="button" className="capture-link" aria-expanded={editing} aria-controls="capture-target-fields" disabled={targetLocked}
            onClick={() => { pinned.current = true; setEditing(!editing); }}>{editing ? '收起' : '更改'}</button>}
        </div>
        {editing && course && <div id="capture-target-fields" className="capture-target-fields"><div className="audio-target-grid">
          <label>课程{courseSelect}</label>
          <label>课堂
            <select className="app-select audio-session-select" value={sessionId} disabled={sessionsLoading || targetLocked}
              onChange={e => { manualTarget(); setSessionId(e.target.value); }}>
              <option value={NEW_SESSION}>新建一节课</option>
              {sessions.map((item) => {
                const title = `${item.session_date} · ${item.title || course?.name || '课堂'}`;
                const status = sessionStates[item.status] || '已有课堂';
                const label = `${title} · ${status}`;
                return <option key={item.id} value={item.id} aria-label={label} data-status={status}>{styledPicker ? title : label}</option>;
              })}
            </select>
          </label>
          {sessionId === NEW_SESSION && <>
            <label>上课日期<input type="date" required disabled={targetLocked} value={sessionDate} onChange={e => { manualTarget(); setSessionDate(e.target.value); }} /></label>
            <label>课堂名称<input disabled={targetLocked} value={sessionTitle} onChange={e => { manualTarget(); setSessionTitle(e.target.value); }} placeholder={course.name} /></label>
          </>}
        </div>
        <div className="capture-existing">
          {session && <><span>{audioCount(session) ? `本节已有 ${audioCount(session)} 段录音，新录音将按顺序追加。` : '新录音将保存到这节课堂。'}</span>
            <button type="button" className="capture-link" onClick={() => navigate({ name: 'session', id: session.id })}>查看课堂记录</button></>}
          <button type="button" className="capture-link" onClick={() => navigate({ name: 'recordings', courseId: course.id })}>查看这门课的录音</button>
        </div>
        </div>}
        {selectionHint && !course && <p role="status" className="small muted">{selectionHint}</p>}
      </section>

      {!course && courses.length > 0 && <section className="capture-stage" aria-label="录音来源">
        {source === 'microphone' ? <RecorderStart visible={visible} onStart={() => askForCourse('microphone')} onImport={() => askForCourse('upload')} />
          : <div className="capture-idle"><p className="capture-device"><Icon name="upload" />导入已有录音</p><div className="capture-actions">
            <button type="button" className="primary" onClick={() => askForCourse('upload')}>选择课程并导入</button>
            <button type="button" className="capture-link" onClick={() => { switchSource('microphone'); setSelectionHint(''); }}>返回麦克风录音</button></div></div>}
      </section>}
      {course && (sessionId === NEW_SESSION || session) && <AudioWorkspaceEditor course={course} session={session} source={source} onSourceChange={switchSource}
        targetVersion={targetVersion} visible={visible} onUseTarget={() => { pinned.current = true; setEditing(false); }}
        sessionDate={sessionDate} sessionTitle={sessionTitle || course.name} navigate={navigate}
        onRecordingChange={onRecordingChange} onRecordingNotice={onRecordingNotice} onRecordingSaved={onRecordingSaved}
        onTargetLock={setTargetLocked}
        onSessionSaved={(saved) => {
          setSessions((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
          setSessionId(saved.id);
        }} />}
      {coursesLoading && <div className="empty">正在加载课程…</div>}
      {!coursesLoading && !courses.length && !error && <div className="empty">添加课程后即可开始录音。 <button onClick={() => navigate({ name: 'courses' })}>我的课程</button></div>}
      {error && <p className="quality-warning" role="alert">{error}</p>}
      </div>
    </div>
  );
}

function AudioWorkspaceEditor({ course, session, sessionDate, sessionTitle, source, onSourceChange, targetVersion, visible, onUseTarget, navigate, onSessionSaved, onTargetLock, onRecordingChange, onRecordingNotice, onRecordingSaved }: {
  course: Course; session?: Session; sessionDate: string; sessionTitle: string; source: 'microphone' | 'upload';
  onSourceChange: (source: 'microphone' | 'upload') => void; targetVersion: number; visible: boolean; onUseTarget: () => void;
  navigate: (view: View) => void; onSessionSaved: (session: Session) => void;
  onTargetLock: (locked: boolean) => void; onRecordingChange: (active: boolean) => void; onRecordingNotice: (message: string) => void;
  onRecordingSaved: () => void;
}) {
  const recognition = useRecognition(course.id);
  const [plan, setPlan] = useState<'now' | 'later'>('now');
  const [files, setFiles] = useState<File[]>([]);
  const filesRef = useRef<File[]>([]);
  const [recordingActive, setRecordingActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadIndex, setUploadIndex] = useState(0);
  const [progress, setProgress] = useState({ loaded: 0, total: 0 });
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [createdSessionId, setCreatedSessionId] = useState('');
  const [storedAudioCount, setStoredAudioCount] = useState(audioCount(session));

  useEffect(() => { onTargetLock(busy || recordingActive); }, [busy, recordingActive, onTargetLock]);

  useEffect(() => {
    setStoredAudioCount(audioCount(session));
    if (!session) setCreatedSessionId('');
  }, [course.id, targetVersion, session?.id, session?.audio_manifest, session?.audio_path]);

  const addFiles = (incoming: File[]) => {
    if (busy) return;
    onUseTarget();
    const current = filesRef.current;
    const result = mergeAudioFiles(current, incoming);
    filesRef.current = result.files;
    setFiles(result.files);
    const warnings = [
      result.truncated ? `一节课最多添加 ${MAX_AUDIO_FILES} 段录音` : '',
      result.unsupportedCount ? `已跳过 ${result.unsupportedCount} 个不支持的文件` : '',
      result.oversizedCount ? `已跳过 ${result.oversizedCount} 个超过 2 GB 的文件` : '',
    ].filter(Boolean);
    setError(warnings.join('；') + (warnings.length ? '。' : ''));
    setMessage(result.duplicateCount ? `已跳过 ${result.duplicateCount} 个重复文件。` : '');
    return result.files.length > current.length;
  };
  const removeFile = (index: number) => {
    const next = filesRef.current.filter((_, i) => i !== index);
    filesRef.current = next;
    setFiles(next);
    setProgress({ loaded: 0, total: totalAudioBytes(next) });
    setError(''); setMessage('');
  };

  const beginAnalysis = async (id: string) => {
    if (busy || recordingActive || (source === 'upload' && files.length)) return;
    setBusy(true); setError('');
    try {
      if (recognition.ready) await recognition.save();
      await api.process(id, { mode: 'transcribe' });
      navigate({ name: 'session', id });
    } catch (e: any) {
      setError(e.message || '无法开始处理');
    } finally { setBusy(false); }
  };

  const upload = async () => {
    if (recordingActive || busy) return;
    if (!session && !sessionDate) { setError('请选择上课日期。'); return; }
    if (!files.length) { setError('请先添加至少一段录音。'); return; }
    if (plan === 'now' && !recognition.ready) { setError(recognition.error || '语音识别设置还在加载，请稍后重试。'); return; }
    setBusy(true); setError(''); setMessage('');
    const totalBytes = totalAudioBytes(files);
    let uploadedBytes = 0;
    let uploadedCount = 0;
    const existingCount = storedAudioCount;
    let sessionForUpload = session;
    try {
      // Persist changed course recognition preferences before creating a new session.
      if (recognition.ready) await recognition.save();
      if (!sessionForUpload) {
        const id = createdSessionId || (await api.createSession({ course_id: course.id, session_date: sessionDate, title: sessionTitle || course.name })).id;
        setCreatedSessionId(id);
        const detail = await api.sessionDetail(id);
        sessionForUpload = detail.session as Session;
      }
      setProgress({ loaded: 0, total: totalBytes });
      for (let i = 0; i < files.length; i += 1) {
        setUploadIndex(i);
        const file = files[i];
        const headers: Record<string, string> = {
          'X-Batch-Upload': '1',
          'X-Batch-Index': String(i),
          'X-Batch-Count': String(files.length),
          'X-Batch-Existing-Count': String(existingCount),
        };
        if (plan === 'later') headers['X-Defer-Processing'] = '1';
        const result: any = await api.uploadAudio(sessionForUpload.id, file, (loaded) => {
          setProgress({ loaded: uploadedBytes + loaded, total: totalBytes });
        }, headers);
        if (result?.error) throw new Error(result.error);
        uploadedBytes += file.size;
        uploadedCount += 1;
        setStoredAudioCount(existingCount + uploadedCount);
        setProgress({ loaded: uploadedBytes, total: totalBytes });
      }
      filesRef.current = []; setFiles([]);
      setCreatedSessionId(sessionForUpload.id);
      setBusy(false); onRecordingSaved();
      navigate({ name: 'session', id: sessionForUpload.id });
    } catch (e: any) {
      if (sessionForUpload) {
        try {
          const latest = await api.sessionDetail(sessionForUpload.id);
          const newRemoteFiles = (latest.audio_files || []).slice(existingCount);
          const storedKeys = new Set(newRemoteFiles.map((item: any) => `${item.name}\u0000${item.bytes}`));
          const remaining = filesRef.current.filter((file) => !storedKeys.has(`${file.name}\u0000${file.size}`));
          filesRef.current = remaining;
          setFiles(remaining);
          setStoredAudioCount((latest.audio_files || []).length);
          onSessionSaved(latest.session as Session);
          if (newRemoteFiles.length) { onRecordingSaved(); setMessage(`已确认 ${newRemoteFiles.length} 段录音保存成功，可重试剩余录音。`); }
        } catch { /* keep the local queue if the status check itself fails */ }
      }
      setError(uploadedCount
        ? `上传在第 ${uploadedCount + 1} 段中断；前 ${uploadedCount} 段已保存。${e.message ? `（${e.message}）` : ''}`
        : e.message || '上传失败，请检查录音格式后重试。');
      setBusy(false);
    }
  };

  const percent = progress.total ? Math.min(100, Math.round(progress.loaded * 100 / progress.total)) : 0;
  const hasStoredAudio = !!session?.audio_path;

  return (
    <>
      {source === 'upload' && session?.status === 'ready_to_process' && hasStoredAudio && <section className="card audio-staged-banner">
        <div><b>本节课已有录音记录，等待处理</b><p className="small muted">{files.length ? '先上传或移除队列中的新录音，再处理本节课。' : '可在「我的录音」回听，也可以启动转写和整理。'}</p></div>
        <button className="primary" disabled={busy || recordingActive || files.length > 0} onClick={() => void beginAnalysis(session.id)}>{busy ? '正在启动…' : '开始转写并整理'}</button>
      </section>}

      <div className={source === 'upload' && files.length > 0 ? 'capture-upload-layout' : 'capture-mic-layout'}>
        <section className="capture-source">
          {source === 'upload' && <><div className="capture-upload-heading"><h2>导入已有录音</h2>
            <button type="button" className="capture-link" disabled={busy} onClick={() => onSourceChange('microphone')}><Icon name="mic" />麦克风录音</button></div>
          <div className="audio-dropzone" onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); addFiles(Array.from(e.dataTransfer.files || [])); }}>
            <div className="capture-upload-icon"><Icon name="upload" /></div>
            <b>把录音拖到这里，或从电脑选择</b>
            <p className="small muted">MP3、M4A、WAV、AAC、FLAC、OGG、WebM、MP4 · 单个文件不超过 2GB</p>
            <label className="btn ghost audio-file-picker">{files.length ? '继续添加录音' : '选择录音文件'}
              <input type="file" multiple hidden accept=".mp3,.m4a,.wav,.aac,.flac,.ogg,.webm,.mp4,.mov" disabled={busy}
                onChange={(e) => { addFiles(Array.from(e.target.files || [])); e.currentTarget.value = ''; }} />
            </label>
          </div></>}
          <div hidden={source !== 'microphone'}>
            <MicrophoneRecorder key={`${course.id}-${targetVersion}`} visible={visible && source === 'microphone'} courseId={course.id} courseName={courseLabel(course)}
              sessionId={session?.id} sessionDate={session?.session_date || sessionDate} title={session?.title || sessionTitle || course.name}
              onRecordingChange={active => { if (active) onUseTarget(); setRecordingActive(active); onRecordingChange(active); }}
              onRecordingNotice={onRecordingNotice} onConfirmed={onRecordingSaved} onImport={() => onSourceChange('upload')} onProcess={beginAnalysis}
              onView={saved => navigate({ name: 'recordings', sessionId: saved.session_id, fileIndex: saved.file.index, fileKey: saved.file.file_key })}
              onSaved={async id => { const detail = await api.sessionDetail(id); onSessionSaved(detail.session as Session); }} disabled={busy || (!session && !sessionDate)} />
          </div>
          {(source === 'microphone' || !files.length) && error && <p className="quality-warning" role="alert">{error}</p>}
          {source === 'upload' && !files.length && message && <p className="hint">{message}</p>}

          <details className="audio-recognition-panel">
            <summary>语音识别设置</summary>
            <p className="small muted">设置将用于本课程的语音识别。</p>
            <RecognitionSettings value={recognition.value} onChange={value => { onUseTarget(); recognition.setValue(value); }} disabled={busy || !recognition.ready} />
            {recognition.error && <p className="quality-warning" role="alert">{recognition.error}</p>}
          </details>
        </section>

        {source === 'upload' && files.length > 0 && <aside className="audio-queue-card capture-queue" aria-label="导入队列">
          <div className="audio-section-title">
            <div><h2>导入顺序</h2></div>
            <span className="small muted">{files.length} 段</span>
          </div>
            <div className="audio-queue-summary">合计 {(totalAudioBytes(files) / 1024 / 1024).toFixed(1)} MB <span>·</span> 顺序就是处理顺序</div>
            <div className="audio-queue-list">
              {files.map((file, index) => <div className="audio-queue-item" key={`${file.name}-${file.lastModified}-${index}`}>
                <span className="audio-file-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="audio-file-meta"><b title={file.name}>{file.name}</b><small>{(file.size / 1024 / 1024).toFixed(1)} MB · {file.type || '音频文件'}</small></span>
                <button className="ghost danger audio-remove" disabled={busy || recordingActive} aria-label={`移除 ${file.name}`} onClick={() => removeFile(index)}>移除</button>
              </div>)}
            </div>
          {session && audioCount(session) > 0 && <div className="audio-existing-note">本节已有 {audioCount(session)} 段录音，新录音会追加，不会覆盖旧文件。</div>}
          <div className="audio-processing-options">
            <div className="audio-section-title"><div><h3>处理方式</h3></div></div>
            <button className={`audio-plan-card ${plan === 'now' ? 'selected' : ''}`} aria-pressed={plan === 'now'} disabled={busy}
              onClick={() => setPlan('now')}>
              <span className="audio-plan-radio" />
              <span><b>立即转写并整理</b><small>识别语音并生成中文课堂笔记</small></span>
            </button>
            <button className={`audio-plan-card ${plan === 'later' ? 'selected' : ''}`} aria-pressed={plan === 'later'} disabled={busy}
              onClick={() => setPlan('later')}>
              <span className="audio-plan-radio" />
              <span><b>先保存，稍后处理</b><small>只上传到本机课堂资料，不启动模型</small></span>
            </button>
            {plan === 'later' && <p className="small muted audio-option-note">保存后可在课堂详情页预听、删除录音或手动开始转写整理。</p>}
          </div>

          {busy && <div className="audio-upload-progress">
            <div className="row" style={{ justifyContent: 'space-between' }}><span>正在上传第 {uploadIndex + 1}/{files.length} 段</span><b>{percent}%</b></div>
            <div className="progress"><div style={{ width: `${percent}%` }} /></div>
            <span className="small muted">{(progress.loaded / 1024 / 1024).toFixed(1)} / {(progress.total / 1024 / 1024).toFixed(1)} MB</span>
          </div>}
          {message && <p className="hint">{message}</p>}
          {error && <p className="quality-warning" role="alert">{error}</p>}
          <button className="primary audio-submit" disabled={busy || recordingActive || !files.length || (plan === 'now' && !recognition.ready)} onClick={upload}>
            {busy ? '正在上传…' : plan === 'now' ? '上传并开始处理' : '仅上传，稍后处理'}
          </button>
        </aside>}
      </div>
      {source === 'upload' && session && <div className="capture-existing-tools">
          {session.status === 'completed' && session.source_type === 'audio' && <button className="ghost audio-reanalyze" disabled={busy || files.length > 0}
            onClick={async () => { try { setBusy(true); await api.process(session.id, { mode: 'analyze' }); navigate({ name: 'session', id: session.id }); } catch (e: any) { setError(e.message || '重新分析失败'); setBusy(false); } }}>
            仅重新分析现有转写
          </button>}
          <details className="queue-preview"><summary>查看已有转写与笔记</summary><TranscriptPreview sessionId={session.id} /></details>
      </div>}
    </>
  );
}
