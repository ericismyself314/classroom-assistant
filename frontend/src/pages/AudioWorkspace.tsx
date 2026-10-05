import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { MicrophoneRecorder } from '../components/MicrophoneRecorder';
import { RecognitionSettings, useRecognition } from '../components/RecognitionSettings';
import { MAX_AUDIO_FILES, mergeAudioFiles, totalAudioBytes } from '../audioFiles';
import { localDateISO } from '../localDate';
import { Course, Session } from '../types';
import { View } from '../App';

const NEW_SESSION = '__new__';

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

export function AudioWorkspace({ navigate, courseId: initialCourseId, sessionId: initialSessionId, date, title, source: initialSource }: {
  navigate: (view: View) => void; courseId?: string; sessionId?: string; date?: string; title?: string;
  source?: 'microphone' | 'upload';
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
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    api.courses().then(({ courses: result }) => {
      if (!live) return;
      setCourses(result);
      if (!courseId) setCourseId(initialCourseId || result[0]?.id || '');
      setCoursesLoading(false);
    }).catch((e) => {
      if (live) { setError(e.message || '课程列表加载失败'); setCoursesLoading(false); }
    });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (!courseId) { setSessions([]); return; }
    let live = true;
    setSessionsLoading(true);
    api.sessions(courseId).then(({ sessions: result }) => {
      if (!live) return;
      setSessions(result);
      setSessionId((current) => initialSessionId && result.some((s: Session) => s.id === initialSessionId)
        ? initialSessionId
        : current !== NEW_SESSION && result.some((s: Session) => s.id === current) ? current : NEW_SESSION);
      setSessionsLoading(false);
    }).catch((e) => {
      if (live) { setError(e.message || '课堂列表加载失败'); setSessionsLoading(false); }
    });
    return () => { live = false; };
  }, [courseId, initialSessionId]);

  const course = courses.find((item) => item.id === courseId);
  const session = sessions.find((item) => item.id === sessionId);

  return (
    <div className="audio-workspace">
      <div className="audio-workspace-header">
        <div>
          <div className="audio-eyebrow">课堂助手 · 音频工作台</div>
          <h1 className="page-title">录音与导入</h1>
        </div>
        <button className="ghost" onClick={() => navigate({ name: 'week' })}>返回本周</button>
      </div>

      <section className="card audio-target-card">
        <div className="audio-section-title"><div><h2>课程与课堂</h2></div>
          {session && <button className="ghost" onClick={() => navigate({ name: 'session', id: session.id })}>查看课堂记录</button>}
        </div>
        <div className="audio-target-grid">
          <label>课程
            <select value={courseId} disabled={coursesLoading || !courses.length || targetLocked} onChange={(e) => {
              setCourseId(e.target.value); setSessionId(NEW_SESSION); setError('');
            }}>
              {!courses.length && <option value="">{coursesLoading ? '正在加载课程…' : '请先创建一门课程'}</option>}
              {courses.map((item) => <option key={item.id} value={item.id}>{item.name}{item.code ? ` · ${item.code}` : ''}</option>)}
            </select>
          </label>
          <label>课堂
            <select value={sessionId} disabled={!courseId || sessionsLoading || targetLocked} onChange={(e) => setSessionId(e.target.value)}>
              <option value={NEW_SESSION}>新建一节课</option>
              {sessions.map((item) => <option key={item.id} value={item.id}>{item.session_date} · {item.title || course?.name || '课堂'} · {item.status === 'ready_to_process' ? '待处理' : item.status === 'completed' ? '已完成' : item.status === 'failed' ? '处理失败' : '已有课堂'}</option>)}
            </select>
          </label>
          {sessionId === NEW_SESSION && <>
            <label>上课日期<input type="date" value={sessionDate} onChange={(e) => setSessionDate(e.target.value)} /></label>
            <label>课堂名称<input value={sessionTitle || course?.name || ''} onChange={(e) => setSessionTitle(e.target.value)} placeholder="例如：第 4 周 · 利率风险" /></label>
          </>}
        </div>
        {session && audioCount(session) > 0 && <div className="hint" style={{ marginTop: 12, marginBottom: 0 }}>
          本节课已保存 {audioCount(session)} 段录音；新添加的录音会按顺序追加。处理时将合并整节课的录音。
        </div>}
      </section>

      {courseId && course && <AudioWorkspaceEditor key={courseId} course={course} session={session} initialSource={initialSource}
        sessionDate={sessionDate} sessionTitle={sessionTitle || course.name} navigate={navigate}
        onTargetLock={setTargetLocked}
        onSessionSaved={(saved) => {
          setSessions((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
          setSessionId(saved.id);
        }} />}
      {coursesLoading && <div className="empty">正在加载课程…</div>}
      {!coursesLoading && !courses.length && <div className="empty">请先在“课程”页面创建课程，再开始录音或上传。</div>}
      {error && <p className="quality-warning" role="alert">{error}</p>}
    </div>
  );
}

function AudioWorkspaceEditor({ course, session, sessionDate, sessionTitle, initialSource, navigate, onSessionSaved, onTargetLock }: {
  course: Course; session?: Session; sessionDate: string; sessionTitle: string; initialSource?: 'microphone' | 'upload';
  navigate: (view: View) => void; onSessionSaved: (session: Session) => void;
  onTargetLock: (locked: boolean) => void;
}) {
  const recognition = useRecognition(course.id);
  const [source, setSource] = useState<'upload' | 'microphone'>(initialSource || 'microphone');
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
  }, [session?.id, session?.audio_manifest, session?.audio_path]);

  const addFiles = (incoming: File[]) => {
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
  const removeQueuedFile = (file: File) => {
    const index = filesRef.current.findIndex((item) => item === file || (
      item.name === file.name && item.size === file.size && item.lastModified === file.lastModified
    ));
    if (index < 0) return false;
    removeFile(index);
    return true;
  };

  const beginAnalysis = async () => {
    if (!session || busy || recordingActive || files.length) return;
    setBusy(true); setError('');
    try {
      await api.process(session.id, { mode: 'transcribe' });
      navigate({ name: 'session', id: session.id });
    } catch (e: any) {
      setError(e.message || '无法开始处理');
    } finally { setBusy(false); }
  };

  const upload = async () => {
    if (recordingActive || busy) return;
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
          if (newRemoteFiles.length) setMessage(`已确认 ${newRemoteFiles.length} 段录音保存成功；可重试剩余录音。`);
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
      {session?.status === 'ready_to_process' && hasStoredAudio && <section className="card audio-staged-banner">
        <div><b>已有录音已保存，尚未开始处理</b><p className="small muted">{files.length ? '先上传或移除清单中的新录音，再处理本节课。' : '可以先检查课堂详情，也可以现在开始语音识别和中文整理。'}</p></div>
        <button className="primary" disabled={busy || recordingActive || files.length > 0} onClick={beginAnalysis}>{busy ? '正在启动…' : '开始转写并整理'}</button>
      </section>}

      <div className="audio-workspace-grid">
        <section className="card audio-source-card">
          <div className="audio-section-title">
            <div><h2>录音来源</h2></div>
            <span className="small muted">支持最多 {MAX_AUDIO_FILES} 段，按列表顺序处理</span>
          </div>
          <div className="audio-source-tabs" role="tablist" aria-label="录音来源">
            <button className={source === 'upload' ? 'active' : ''} role="tab" aria-selected={source === 'upload'}
              disabled={recordingActive || busy} onClick={() => setSource('upload')}>上传已有录音</button>
            <button className={source === 'microphone' ? 'active' : ''} role="tab" aria-selected={source === 'microphone'}
              disabled={recordingActive || busy} onClick={() => setSource('microphone')}>电脑麦克风录音</button>
          </div>

          {source === 'upload' ? <div className="audio-dropzone" onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); addFiles(Array.from(e.dataTransfer.files || [])); }}>
            <div className="audio-drop-icon">↑</div>
            <b>把录音拖到这里，或从电脑选择</b>
            <p className="small muted">MP3、M4A、WAV、AAC、FLAC、OGG、WebM、MP4 · 单个文件不超过 2GB</p>
            <label className="btn ghost audio-file-picker">{files.length ? '继续添加录音' : '选择录音文件'}
              <input type="file" multiple hidden accept=".mp3,.m4a,.wav,.aac,.flac,.ogg,.webm,.mp4,.mov" disabled={busy}
                onChange={(e) => { addFiles(Array.from(e.target.files || [])); e.currentTarget.value = ''; }} />
            </label>
          </div> : <div className="audio-microphone-panel">
            <MicrophoneRecorder onAddFile={(file) => addFiles([file])} onRemoveFile={removeQueuedFile} onRecordingChange={setRecordingActive} disabled={busy} />
          </div>}

          <div className="audio-recognition-panel">
            <div><b>语音识别设置</b><span className="small muted">修改后保存为本课程设置，稍后处理也会使用</span></div>
            <RecognitionSettings value={recognition.value} onChange={recognition.setValue} disabled={busy || !recognition.ready} />
            {recognition.error && <p className="quality-warning" role="alert">{recognition.error}</p>}
          </div>
        </section>

        <aside className="card audio-queue-card">
          <div className="audio-section-title">
            <div><h2>本次录音</h2></div>
            <span className={`badge ${files.length ? 'green' : 'gray'}`}>{files.length} 段</span>
          </div>
          {files.length === 0 ? <div className="audio-queue-empty">
            <div className="audio-empty-icon">♫</div><b>还没有添加录音</b>
            <span className="small muted">选择音频文件，或切换到电脑麦克风录音。停止录制后会自动加入这里。</span>
          </div> : <>
            <div className="audio-queue-summary">合计 {(totalAudioBytes(files) / 1024 / 1024).toFixed(1)} MB <span>·</span> 顺序就是处理顺序</div>
            <div className="audio-queue-list">
              {files.map((file, index) => <div className="audio-queue-item" key={`${file.name}-${file.lastModified}-${index}`}>
                <span className="audio-file-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="audio-file-meta"><b title={file.name}>{file.name}</b><small>{(file.size / 1024 / 1024).toFixed(1)} MB · {file.type || '音频文件'}</small></span>
                <button className="ghost danger audio-remove" disabled={busy || recordingActive} aria-label={`移除 ${file.name}`} onClick={() => removeFile(index)}>移除</button>
              </div>)}
            </div>
          </>}

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
          <p className="small muted audio-privacy-note">录音保存在你的电脑上。处理完成后，可在课堂详情删除录音并保留文字。</p>
          {session?.status === 'completed' && session.source_type === 'audio' && <button className="ghost audio-reanalyze" disabled={busy || files.length > 0}
            onClick={async () => { try { setBusy(true); await api.process(session.id, { mode: 'analyze' }); navigate({ name: 'session', id: session.id }); } catch (e: any) { setError(e.message || '重新分析失败'); setBusy(false); } }}>
            仅重新分析现有转写
          </button>}
        </aside>
      </div>
    </>
  );
}
