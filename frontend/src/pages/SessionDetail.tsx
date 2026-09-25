import { useEffect, useState } from 'react';
import { api } from '../api';
import { Session, Segment, Topic, Item, AudioFile, CATEGORY_LABELS, CONFIDENCE_LABELS } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { AudioPlayer } from '../components/AudioPlayer';
import { Modal } from '../components/Modal';
import { View } from '../App';
import { ClassNotes, ReviewNotes, Sources } from '../components/ClassNotes';
import { RecognitionSettings, useRecognition } from '../components/RecognitionSettings';
import { chineseEvidence, displaySummary, displayText, displayTitle, hasChinese } from '../chineseDisplay';

const PROCESSING = new Set(['waiting_model', 'file_check', 'transcribing', 'analyzing', 'indexing']);

function fmt(s: number | null): string {
  if (s == null) return '';
  const m = Math.floor(s / 60); const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

export function SessionDetail({ sid, navigate }: { sid: string; navigate: (v: View) => void }) {
  const [data, setData] = useState<any>(null);
  const [job, setJob] = useState<any>(null);
  const [seek, setSeek] = useState<{ seconds: number } | null>(null);
  const jump = (seconds: number | null) => { if (seconds != null) setSeek({ seconds }); };
  const [duration, setDuration] = useState<number | null>(null);
  const [deletingAudio, setDeletingAudio] = useState(false);
  const [deletingAudioIndex, setDeletingAudioIndex] = useState<number | null>(null);
  const [recognitionOpen, setRecognitionOpen] = useState(false);
  const [history, setHistory] = useState<any>(null);
  const [err, setErr] = useState('');
  const [find, setFind] = useState('');
  const [editingSegment, setEditingSegment] = useState<Segment | null>(null);
  const [editingChapter, setEditingChapter] = useState<number | null>(null);

  const load = () => api.sessionDetail(sid).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [sid]);

  useEffect(() => {
    if (!data) return;
    if (PROCESSING.has(data.session.status)) {
      const t = setInterval(async () => {
        try {
          const j = await api.sessionJob(sid);
          setJob(j.job);
          const d = await api.sessionDetail(sid);
          setData(d);
          if (!PROCESSING.has(d.session.status)) { clearInterval(t); setJob(j.job); }
        } catch { /* ignore */ }
      }, 1500);
      return () => clearInterval(t);
    }
  }, [data]);

  if (!data) return <div className="empty">{err || '加载中…'}</div>;
  const { session, course, audio_files, segments, topics, items } = data as {
    session: Session; course: any; audio_files?: AudioFile[]; segments: Segment[]; topics: Topic[]; items: Item[];
  };
  const processing = PROCESSING.has(session.status);
  const wrongLanguage = session.source_type === 'audio' && !!session.transcript_language && !['en', 'zh', 'mix'].includes(session.transcript_language);
  const pending = items.filter(i => !i.is_confirmed && (i.verification_status === 'pending' || i.confidence === 'uncertain'));
  const confirmed = items.filter(i => !pending.includes(i));
  const sessionDisplayTitle = displayTitle(session.title || data.notes?.title || course.name, '');

  const reprocess = (mode: string) => { api.process(sid, { mode }).then(() => load()).catch((e) => setErr(e.message)); };
  const renameTitle = async () => {
    const current = session.title || data.notes?.title || course.name;
    const next = window.prompt('修改课程标题', current)?.trim();
    if (!next || next === current) return;
    try { await api.updateSession(sid, { title: next }); await load(); }
    catch (e: any) { setErr(e.message); }
  };
  const cancel = () => { if (job) api.cancelJob(job.id).then(() => load()); };
  const openAudioWorkspace = (source: 'microphone' | 'upload') => navigate({
    name: 'audio', courseId: session.course_id, sessionId: sid, date: session.session_date,
    title: sessionDisplayTitle, source,
  });
  const del = async () => {
    if (confirm('确定删除这次课堂吗？录音与转写将一并从本机删除。')) {
      await api.deleteSession(sid); navigate({ name: 'courses', courseId: session.course_id });
    }
  };
  const deleteAudio = async () => {
    const confirmText = segments.length
      ? '确定删除课堂助手内部保存的这节课全部录音吗？转写、中文整理和总结会保留；另存到其他文件夹的副本不受影响。删除后不能再根据录音重新转写。'
      : '确定删除已暂存的课堂录音吗？这节课还没有转写文字；删除后可重新上传。另存到其他文件夹的副本不受影响。';
    if (!confirm(confirmText)) return;
    setDeletingAudio(true);
    setErr('');
    try {
      await api.deleteSessionAudio(sid);
      await load();
    } catch (e: any) {
      setErr(e.message || '删除录音失败');
    } finally {
      setDeletingAudio(false);
    }
  };
  const deleteStagedAudio = async (file: AudioFile) => {
    if (!confirm(`确定删除暂存录音“${file.name}”吗？其它录音不会受影响。`)) return;
    setDeletingAudioIndex(file.index);
    setErr('');
    try {
      await api.deleteStagedAudioFile(sid, file.index);
      await load();
    } catch (e: any) {
      setErr(e.message || '删除这段录音失败');
    } finally {
      setDeletingAudioIndex(null);
    }
  };
  const presentationText = (s: Segment) => displayText(s.display_text, hasChinese(s.text) ? s.text : '');
  const filtered = find ? segments.filter((s) => presentationText(s).includes(find)) : segments;

  return (
    <div className="session-notes">
      <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <div>
          <div className="title-line"><h1 className="page-title">{sessionDisplayTitle}</h1>
            <button className="ghost title-edit" disabled={processing} onClick={renameTitle}>改标题</button></div>
          <p className="page-sub">
            {session.session_date}{session.week_number ? ` · 第${session.week_number}周` : ''} · {session.start_time || ''}–{session.end_time || ''} · <StatusBadge status={session.status} />
            {(duration || session.audio_duration_seconds) ? ` · 录音 ${fmt(duration || session.audio_duration_seconds)}` : ''}
          </p>
        </div>
        <div className="row">
          <button onClick={() => setRecognitionOpen(true)} disabled={processing}>识别设置{session.audio_path ? ' / 重新转写' : ''}</button>
          <button onClick={() => reprocess('analyze')} disabled={processing || wrongLanguage || !segments.length}>仅重新分析</button>
          <button className="danger" onClick={del} disabled={processing}>删除</button>
        </div>
      </div>

      {err && <p role="alert" className="quality-warning">{err}</p>}
      {wrongLanguage && <div className="quality-warning" role="alert"><b>这次转写的语言可能识别错了（{session.transcript_language}）</b>
        <p>旧总结暂不作为可靠笔记展示。请在「识别设置」选择老师实际使用的语言，再重新转写；旧结果会保留在历史版本。</p>
      </div>}
      {processing && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="row"><b>正在处理…</b>
            {job?.stage && <span className="muted small">（{job.stage}）</span>}
            <button className="ghost danger" style={{ marginLeft: 'auto' }} onClick={cancel}>取消</button>
          </div>
        </div>
      )}
      {session.status === 'failed' && session.processing_error && (
        <div className="card" style={{ marginBottom: 14, borderColor: '#eccfca' }}>
          <b>处理失败</b>
          <div className="small muted" style={{ marginTop: 6 }}>{session.processing_error}</div>
          <div className="small muted">可点击「重新转写」或「仅重新分析」重试，已有数据不会丢失。</div>
        </div>
      )}

      {session.status === 'ready_to_process' && session.audio_path && <div className="card audio-staged-banner">
        <div><b>录音已保存 · 尚未开始处理</b>
          <div className="small muted" style={{ marginTop: 4 }}>{audio_files?.length || 1} 段录音保存在这台电脑上。你可以现在转写并整理，或先在下方预听。</div>
        </div>
        <div className="row wrap">
          <button className="primary" disabled={processing} onClick={() => reprocess('transcribe')}>开始转写并整理</button>
          <button className="ghost danger" disabled={processing || deletingAudio} onClick={deleteAudio}>{deletingAudio ? '正在删除…' : '删除暂存录音'}</button>
        </div>
      </div>}

      {session.status === 'ready_to_process' && !!audio_files?.length && <div className="card staged-audio-list">
        <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <div><b>已上传录音 · {audio_files.length} 段</b><div className="small muted">尚未处理，可以逐段删除；开始处理后会合并为一节课。</div></div>
        </div>
        {audio_files.map((file, index) => <div className="staged-audio-row" key={`${file.index}-${file.name}`}>
          <span className="audio-file-index">{String(index + 1).padStart(2, '0')}</span>
          <span className="audio-file-meta"><b title={file.name}>{file.name}</b><small>{file.bytes == null ? '音频文件' : `${(file.bytes / 1024 / 1024).toFixed(1)} MB`}</small></span>
          <button className="ghost danger audio-remove" disabled={deletingAudioIndex != null || deletingAudio}
            onClick={() => deleteStagedAudio(file)}>{deletingAudioIndex === file.index ? '删除中…' : '删除'}</button>
        </div>)}
      </div>}

      <div className="session-audio-heading"><h2>本节录音</h2><span>录制新内容与回听已有录音，入口分开显示</span></div>
      <div className="session-audio-actions">
        <section className="card session-audio-card session-record-card">
          <div className="session-media-head">
            <span className="session-media-icon record-icon" aria-hidden="true">🎙️</span>
            <div><span className="audio-eyebrow">新增录音</span><h2>电脑录音 / 上传</h2></div>
          </div>
          <p>电脑录制时可暂停、继续；停止后先试听，再保存到电脑或加入本节课。</p>
          <div className="session-recording-steps" aria-label="电脑录音步骤">
            <span><i>1</i> 开始录音</span><span><i>2</i> 暂停 / 继续</span><span><i>3</i> 停止并试听</span>
          </div>
          <div className="session-recording-buttons">
            <button className="primary" disabled={processing} onClick={() => openAudioWorkspace('microphone')}>电脑麦克风录音</button>
            <button className="ghost" disabled={processing} onClick={() => openAudioWorkspace('upload')}>上传已有录音</button>
          </div>
        </section>

        <section className="card session-audio-card session-play-card">
          <div className="session-media-head">
            <span className="session-media-icon play-icon" aria-hidden="true">▶</span>
            <div><span className="audio-eyebrow">回听已保存内容</span><h2>播放录音</h2></div>
          </div>
          <p>试听本节已保存的音频；如果有多段录音，可在播放器中切换。</p>
          {session.audio_path ? <AudioPlayer sessionId={sid} files={audio_files} seekSignal={seek}
            onDuration={!audio_files || audio_files.length <= 1 ? setDuration : undefined} />
            : <div className="session-playback-empty">
              <span className="session-playback-symbol" aria-hidden="true">♫</span>
              <b>本节还没有录音</b><span className="small muted">录制或上传后，可在这里播放和核对。</span>
            </div>}
        </section>
      </div>
      {session.audio_path && session.status === 'completed' && segments.length > 0 && <div className="audio-retention card">
        <div>
          <b>转写文字会保留</b>
          <div className="small muted" style={{ marginTop: 3 }}>仅删除课堂助手内部的录音文件，完整转写、中文整理和总结会保留；另存到其他文件夹的副本仍在原处。</div>
        </div>
        <button className="ghost danger" disabled={processing || deletingAudio} onClick={deleteAudio}>{deletingAudio ? '正在删除…' : '删除录音，保留文字'}</button>
      </div>}

      <ClassNotes notes={wrongLanguage ? null : data.notes} segments={segments} onSeek={jump} processing={processing} onEdit={setEditingChapter}
        beforeKnowledge={<>
          <div className="section-heading"><h2>非知识点内容</h2><span className="small muted">老师提到的作业、考试、通知与安排</span></div>
          {!wrongLanguage && <ItemsBlock items={confirmed} segments={segments} onChange={load} onSeek={jump} teacherVoice />}
        </>} />
      {!data.notes && topics.length > 0 && <details className="legacy-notes"><summary>查看旧版总结 · 未按新规则核对</summary>
        {topics.map(t => <div className="topic" key={t.id}><b>{displayTitle(t.title, chineseEvidence(t, segments))}</b><p>{displaySummary(t.summary, chineseEvidence(t, segments))}</p></div>)}
      </details>}

      <div className="section-heading"><h2>待核对</h2><span className="small muted">不把听不清、含义不明的内容当成结论</span></div>
      <ItemsBlock items={wrongLanguage ? items : pending} segments={segments} onChange={load} onSeek={jump} />
      <ReviewNotes notes={wrongLanguage ? null : data.notes} segments={segments} onSeek={jump} />

      <details className="transcript-fold">
      <summary>完整转写 <span className="small muted">{segments.length} 段 · 点击展开与搜索</span></summary>
      <div className="row" style={{ justifyContent: 'space-between', marginTop: 16 }}>
        <h3 style={{ margin: 0 }}>完整转写</h3>
        <input style={{ width: 240 }} placeholder="在本页查找…" value={find} onChange={(e) => setFind(e.target.value)} />
      </div>
      <div className="transcript">
        {filtered.length === 0 && <div className="muted small" style={{ padding: 12 }}>无转写内容。</div>}
        {filtered.map((s) => (
          <div className={`seg ${s.is_uncertain ? 'uncertain' : ''}`} key={s.id}>
            <button className="time ghost" disabled={s.start_seconds == null || !session.audio_path} onClick={() => jump(s.start_seconds)}>{s.start_seconds == null ? '文字导入' : fmt(s.start_seconds)}</button>
            <div style={{ flex: 1 }}>{presentationText(s)}
              {data.notes?.review?.some((r: any) => r.source === 'transcript' && r.segment_index === s.sequence_number) && <span className="badge warn">用词或含义待核对</span>}
              {s.display_text && s.text && <details className="small muted"><summary>查看英文识别原文</summary>{s.text}</details>}
              {s.original_text && s.original_text !== s.text && <details className="small muted"><summary>查看修改前的英文识别原文</summary>{s.original_text}</details>}
            </div>
            <button className="ghost" disabled={processing} onClick={() => setEditingSegment(s)}>纠正文字</button>
          </div>
        ))}
      </div>
      </details>
      {!!data.revisions?.length && <details className="transcript-fold"><summary>历史版本 · {data.revisions.length} 份</summary>
        <p className="small muted">重新处理前的转写、总结和事项保存在本机，不会混进当前笔记。</p>
        {data.revisions.map((r: any) => <button key={r.id} className="ghost" onClick={async () => {
          try { const res = await fetch(`/api/sessions/${sid}/revisions/${r.id}`); if (!res.ok) throw new Error('读取历史版本失败'); setHistory(await res.json()); }
          catch (e: any) { setErr(e.message); }
        }}>{new Date(r.created_at).toLocaleString()} · {r.reason}</button>)}
      </details>}
      {history && <Modal title="历史版本（只读）" onClose={() => setHistory(null)}>
        <p className="small muted">{history.session.transcript_language} · {history.session.transcription_model}</p>
        <h3>旧总结</h3>{history.topics.map((t: any) => <p key={t.id}><b>{t.title}</b><br />{t.summary}</p>)}
        <h3>旧事项</h3>{history.items.map((i: any) => <p key={i.id}>{i.title} {i.is_completed ? ' · 已完成' : ''}<br />{i.details}</p>)}
        <details><summary>旧转写 · {history.segments.length} 段</summary>{history.segments.map((s: any) => <p key={s.id}>{fmt(s.start_seconds)} {s.display_text || '早期版本未生成中文展示稿'}</p>)}</details>
      </Modal>}
      {recognitionOpen && <RecognitionModal courseId={session.course_id} hasAudio={!!session.audio_path} onClose={() => setRecognitionOpen(false)} onSaved={() => load()} onTranscribe={() => { setRecognitionOpen(false); reprocess('transcribe'); }} />}
      {editingSegment && <CorrectionModal title="纠正英文识别原文" fields={[editingSegment.text]} labels={['英文识别原文']}
        explanation="默认展示稿会是中文；如英文识别有误，可回听后在这里修正。保存后点击「仅重新分析」会重新生成中文展示稿，修改前原文会保留。"
        onClose={() => setEditingSegment(null)} onSave={async values => {
          await api.updateSegment(editingSegment.id, values[0], editingSegment.text); setEditingSegment(null); await load();
        }} />}
      {editingChapter != null && data.notes?.chapters?.[editingChapter] && <CorrectionModal title="修改章节笔记"
        fields={[data.notes.chapters[editingChapter].title, data.notes.chapters[editingChapter].summary, ...data.notes.chapters[editingChapter].points.map((p: any) => p.text)]}
        labels={['章节标题', '章节总结', ...data.notes.chapters[editingChapter].points.map((_: any, i: number) => `笔记 ${i + 1}`)]}
        explanation="修改会标注为你手动整理的内容，保留原话出处和修改前版本。"
        onClose={() => setEditingChapter(null)} onSave={async values => {
          const c = data.notes.chapters[editingChapter];
          await api.updateChapter(sid, editingChapter, { title: values[0], summary: values[1], points: values.slice(2),
            expected_title: c.title, expected_summary: c.summary, expected_points: c.points.map((p: any) => p.text) });
          setEditingChapter(null); await load();
        }} />}
    </div>
  );
}

function CorrectionModal({ title, fields, labels, explanation, onClose, onSave }: {
  title: string; fields: string[]; labels: string[]; explanation: string; onClose: () => void; onSave: (values: string[]) => Promise<void>;
}) {
  const [values, setValues] = useState(fields);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async () => { setBusy(true); setError(''); try { await onSave(values); } catch (e: any) { setError(e.message); } finally { setBusy(false); } };
  return <Modal title={title} onClose={() => { if (!busy) onClose(); }} footer={<>
    <button disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || values.some(v => !v.trim())} onClick={save}>{busy ? '保存中…' : '保存修改'}</button>
  </>}><p className="small muted">{explanation}</p>
    {values.map((value, i) => <label key={i} style={{ display: 'block', marginBottom: 12 }}>{labels[i]}
      <textarea aria-label={labels[i]} rows={i === 0 && fields.length > 1 ? 2 : 4} value={value} disabled={busy} style={{ width: '100%' }}
        onChange={e => setValues(values.map((old, j) => i === j ? e.target.value : old))} /></label>)}
    {error && <p role="alert" className="quality-warning">{error}</p>}
  </Modal>;
}

function RecognitionModal({ courseId, hasAudio, onClose, onSaved, onTranscribe }: {
  courseId: string; hasAudio: boolean; onClose: () => void; onSaved: () => void; onTranscribe: () => void;
}) {
  const recognition = useRecognition(courseId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const save = async (transcribe: boolean) => {
    setBusy(true); setError('');
    try { await recognition.save(); onSaved(); if (transcribe) onTranscribe(); else onClose(); }
    catch (e: any) { setError(e.message); setBusy(false); }
  };
  return <Modal title="识别设置" onClose={onClose} footer={<>
    <button disabled={busy} onClick={onClose}>取消</button>
    <button disabled={busy || !recognition.ready} onClick={() => save(false)}>仅保存设置</button>
    {hasAudio && <button className="primary" disabled={busy || !recognition.ready} onClick={() => save(true)}>保存并重新转写</button>}
  </>}>
    <RecognitionSettings value={recognition.value} onChange={recognition.setValue} disabled={busy || !recognition.ready} />
    <p className="small muted">重新转写会处理整段录音，长课堂需要一些时间。旧转写和总结会先存入历史版本。只保存设置不会改变旧结果。</p>
    {(error || recognition.error) && <p role="alert">{error || recognition.error}</p>}
  </Modal>;
}

function ItemsBlock({ items, segments = [], onChange, onSeek, teacherVoice = false }: { items: Item[]; segments?: Segment[]; onChange: () => void; onSeek: (s: number) => void; teacherVoice?: boolean }) {
  const [editing, setEditing] = useState<Item | null>(null);
  if (items.length === 0) return <div className="muted small">暂无此类事项；以已完成的分析及课堂原话为准。</div>;
  return (
    <div>
      {items.map((it) => {
        let evidence: any[] = [];
        try { evidence = it.evidence_data ? JSON.parse(it.evidence_data) : []; } catch { evidence = []; }
        const evidenceText = chineseEvidence({ evidence }, segments);
        const title = displayTitle(it.title, evidenceText);
        const details = displayText(it.details, evidenceText);
        const rawDate = displayText(it.raw_date_text, '未提供明确日期');
        return (
        <div className={`item ${it.confidence === 'uncertain' ? 'uncertain' : ''}`} key={it.id}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <div>
              <span className="tag">{CATEGORY_LABELS[it.category] || '其它'}</span>
              <span className="it">{title}</span>
            </div>
            <span className={`badge ${it.confidence === 'uncertain' ? 'warn' : 'green'}`}>{CONFIDENCE_LABELS[it.confidence]}</span>
          </div>
          {it.details && (teacherVoice ? <div className="teacher-voice"><div className="small muted">老师的话（中文整理）</div><div>{details}</div></div> : <div className="small" style={{ marginTop: 4 }}>{details}</div>)}
          <div className="small muted" style={{ marginTop: 4 }}>
            {it.raw_date_text && <>原话「{rawDate}」 </>}
            {it.normalized_date && <>→ {it.normalized_date} </>}
            {it.action_required ? '· 需要你采取行动' : ''}
            {it.is_confirmed ? ' · 已确认' : ''}{it.is_completed ? ' · 已完成' : ''}
          </div>
          {it.evidence_data && (() => {
            try {
              const ev = JSON.parse(it.evidence_data);
              if (ev.length) return <Sources evidence={ev} onSeek={onSeek} segments={segments} />;
            } catch { /* */ }
            return null;
          })()}
          <div className="row" style={{ marginTop: 6 }}>
            <button className="ghost" onClick={() => setEditing({ ...it })}>修改</button>
            <button className="ghost" onClick={() => api.updateItem(it.id, { is_confirmed: it.is_confirmed ? 0 : 1 }).then(onChange)}>
              {it.is_confirmed ? '取消确认' : '确认'}
            </button>
            <button className="ghost" onClick={() => api.updateItem(it.id, { is_completed: it.is_completed ? 0 : 1 }).then(onChange)}>
              {it.is_completed ? '标记未完成' : '标记完成'}
            </button>
            <button className="ghost danger" onClick={() => api.deleteItem(it.id).then(onChange)}>删除误判</button>
          </div>
        </div>
        );
      })}
      {editing && <EditItem item={editing} onClose={() => setEditing(null)} onSave={onChange} />}
    </div>
  );
}

function EditItem({ item, onClose, onSave }: { item: Item; onClose: () => void; onSave: () => void }) {
  const [title, setTitle] = useState(item.title);
  const [details, setDetails] = useState(item.details || '');
  const [category, setCategory] = useState(item.category);
  const [date, setDate] = useState(item.normalized_date || '');
  const save = () => api.updateItem(item.id, { title, details, category, normalized_date: date || null }).then(() => { onSave(); onClose(); });
  return (
    <Modal title="修改重要事项" onClose={onClose}
      footer={<><button className="ghost" onClick={onClose}>取消</button><button className="primary" onClick={save}>保存</button></>}>
      <label>分类</label>
      <select value={category} onChange={(e) => setCategory(e.target.value)}>
        {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <label>标题</label><input value={title} onChange={(e) => setTitle(e.target.value)} />
      <label>说明</label><textarea rows={3} value={details} onChange={(e) => setDetails(e.target.value)} />
      <label>标准化日期（可空）</label><input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
    </Modal>
  );
}
