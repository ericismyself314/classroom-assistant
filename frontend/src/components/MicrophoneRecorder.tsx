import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import { RecordingOrbit } from './RecordingOrbit';
import './recordingOrbit.css';
import { RecordingQueue } from '../recordingQueue';
import { recordingDrafts } from '../recordingDrafts';
import { finishRecording } from '../finishRecording';
import { RecordingStop } from '../recordingStop';
import { recordingDuration, recordingLibrary, type SavedRecording } from '../recordingLibrary';
import { DeleteRecordingButton, RevealRecordingButton } from './RecordingFileActions';
import { savedAudioClock } from '../savedAudioClock';

type Phase = 'idle' | 'starting' | 'recording' | 'paused' | 'stopping' | 'failed' | 'ready';
function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map((n) => String(n).padStart(2, '0')).join(':');
}

export function RecorderStart({ onStart, onImport, disabled, visible }: { onStart: () => void; onImport: () => void; disabled?: boolean; visible: boolean }) {
  return <div className="orbit-console">
    <p className="capture-device"><Icon name="mic" />电脑麦克风</p>
    <RecordingOrbit idle visible={visible} actionLabel="开始录音" onActivate={onStart} disabled={disabled} /><span className="orbit-time">00:00:00</span>
    <div className="orbit-controls"><button type="button" disabled={disabled} onClick={onStart}><Icon name="mic" />开始录音</button>
      <button type="button" disabled={disabled} onClick={onImport}>导入已有录音</button></div>
  </div>;
}

export function MicrophoneRecorder({ courseId, courseName, sessionId, sessionDate, title, visible, onRecordingChange, onRecordingNotice, onConfirmed, onSaved, onProcess, onImport, onView, disabled = false }: {
  courseId: string; courseName: string; sessionId?: string; sessionDate: string; title: string; visible: boolean;
  onRecordingChange: (active: boolean) => void; onRecordingNotice: (message: string) => void; onConfirmed: () => void;
  onSaved: (sessionId: string) => Promise<void>; onProcess: (sessionId: string) => void; onImport: () => void;
  onView: (saved: SavedRecording) => void; disabled?: boolean;
}) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [elapsedMs, setElapsedMs] = useState(0);
  const [error, setError] = useState('');
  const [interruption, setInterruption] = useState('');
  const [receipt, setReceipt] = useState<SavedRecording | null>(null);
  const [replaying, setReplaying] = useState(false);
  const playerRef = useRef<HTMLAudioElement>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [meterStream, setMeterStream] = useState<MediaStream | null>(null);
  const [hasWritten, setHasWritten] = useState(false);
  const [savedSeconds, setSavedSeconds] = useState(0);
  const clockRef = useRef<ReturnType<typeof savedAudioClock>>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const queueRef = useRef<RecordingQueue | null>(null);
  const draftRef = useRef('');
  const lastSavedId = useRef('');
  const startingRef = useRef(false);
  const finishingRef = useRef(false);
  const stopReasonRef = useRef(new RecordingStop());
  const heartbeatRef = useRef<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const elapsedBeforePauseRef = useRef(0);
  const callbacks = useRef({ onRecordingChange, onRecordingNotice, onConfirmed, onSaved });
  callbacks.current = { onRecordingChange, onRecordingNotice, onConfirmed, onSaved };

  useEffect(() => { if (!visible) playerRef.current?.pause(); }, [visible]);
  useEffect(() => {
    if (phase !== 'recording') return;
    const timer = window.setInterval(() => setElapsedMs(elapsedBeforePauseRef.current + (startedAtRef.current === null ? 0 : performance.now() - startedAtRef.current)), 500);
    return () => window.clearInterval(timer);
  }, [phase]);
  useEffect(() => () => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') { try { recorder.stop(); } catch { /* already stopped */ } }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    clockRef.current?.close();
    if (heartbeatRef.current !== null) window.clearInterval(heartbeatRef.current);
  }, []);
  useEffect(() => {
    const requestFragment = () => {
      const recorder = recorderRef.current;
      if (recorder?.state === 'recording') {
        try { recorder.requestData(); } catch { /* The device may already be stopping. */ }
      }
    };
    const onHidden = () => { if (document.visibilityState === 'hidden') requestFragment(); };
    document.addEventListener('visibilitychange', onHidden);
    window.addEventListener('pagehide', requestFragment);
    return () => {
      document.removeEventListener('visibilitychange', onHidden);
      window.removeEventListener('pagehide', requestFragment);
    };
  }, []);

  const release = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setMeterStream(null);
  };
  const markInterrupted = (reason: string) => {
    const firstReason = stopReasonRef.current.interrupt(reason);
    setInterruption(firstReason);
    callbacks.current.onRecordingNotice(`录音已中断：${firstReason}。正在尝试保存已有部分，请返回录音页查看。`);
  };
  const finish = async () => {
    if (finishingRef.current || !queueRef.current || !draftRef.current) return;
    finishingRef.current = true;
    setPhase('stopping'); setError('');
    try {
      const outcome = await finishRecording(
        () => queueRef.current!.flush(),
        async () => {
          const saved = await recordingDrafts.finalize(draftRef.current, false, !!stopReasonRef.current.stopped());
          if (saved.file?.availability !== 'available') throw new Error('尚未收到原文件的保存回执');
          lastSavedId.current = saved.session_id;
          setReceipt(saved);
          callbacks.current.onConfirmed();
          return saved;
        },
        (sessionId) => callbacks.current.onSaved(sessionId),
      );
      if (heartbeatRef.current !== null) window.clearInterval(heartbeatRef.current);
      heartbeatRef.current = null;
      draftRef.current = '';
      clockRef.current?.close();
      setRefreshFailed(outcome.refreshFailed);
      const reason = stopReasonRef.current.stopped();
      if (reason) callbacks.current.onRecordingNotice(`录音因${reason}中断。已写入的部分可在「我的录音」回听。`);
      if (outcome.refreshFailed) setError('录音文件已保存。课堂详情暂时无法刷新，可先打开「我的录音」。');
      setPhase('ready');
      callbacks.current.onRecordingChange(false);
    } catch (err) {
      setPhase('failed');
      const reason = stopReasonRef.current.stopped();
      callbacks.current.onRecordingNotice(reason
        ? `录音已中断：${reason}。保存未完成，请返回录音页重试。`
        : '录音保存未完成，请返回录音页重试；已确认写入的片段仍在本机。');
      setError(`保存未完成：${err instanceof Error ? err.message : '本地服务不可用'}。已确认写入的分片仍在本机；请重试保存，关闭应用可能丢失尚未写入的部分。`);
    } finally { finishingRef.current = false; }
  };
  const start = async () => {
    if (startingRef.current) return;
    startingRef.current = true;
    stopReasonRef.current = new RecordingStop();
    setInterruption('');
    callbacks.current.onRecordingChange(true);
    setPhase('starting'); setError(''); setSavedSeconds(0); setHasWritten(false); setRefreshFailed(false);
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('当前窗口不支持麦克风录音。');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream; setMeterStream(stream);
      const preferredType = ['audio/webm;codecs=opus', 'audio/webm'].find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, preferredType ? { mimeType: preferredType, audioBitsPerSecond: 96000 } : undefined);
      const draft = await recordingDrafts.create({ course_id: courseId, session_id: sessionId || lastSavedId.current || null, session_date: sessionDate, title, mime_type: recorder.mimeType || preferredType || 'audio/webm' });
      draftRef.current = draft.id;
      await recordingDrafts.heartbeat(draft.id);
      heartbeatRef.current = window.setInterval(() => {
        void recordingDrafts.heartbeat(draft.id).catch(() => {
          if (draftRef.current !== draft.id || finishingRef.current) return;
          markInterrupted('无法连接本机录音服务');
          setError('无法确认本机录音服务，正在停止并尝试保存已录内容。');
          if (recorder.state !== 'inactive') { setPhase('stopping'); recorder.stop(); }
        });
      }, 5000);
      try {
        clockRef.current = savedAudioClock(recorder.mimeType || preferredType || 'audio/webm', seconds => setSavedSeconds(Math.floor(seconds)));
      } catch { clockRef.current = null; }
      const clock = clockRef.current;
      const queue = new RecordingQueue(async (index, blob) => {
        await recordingDrafts.write(draft.id, index, blob);
        setHasWritten(true);
        clock?.append(blob);
      });
      queueRef.current = queue; recorderRef.current = recorder;
      stream.getAudioTracks().forEach((track) => track.addEventListener('ended', () => {
        if (stopReasonRef.current.isUserStopped()) return;
        markInterrupted('麦克风输入已断开');
        if (recorder.state !== 'inactive') { setPhase('stopping'); recorder.stop(); }
      }));
      elapsedBeforePauseRef.current = 0; startedAtRef.current = performance.now(); setElapsedMs(0);
      recorder.ondataavailable = (event) => {
        if (!event.data.size) return;
        queue.add(event.data);
        void queue.flush().catch((err) => {
          markInterrupted(`录音写入本机失败：${err instanceof Error ? err.message : '服务不可用'}`);
          setError(`录音写入本机失败：${err instanceof Error ? err.message : '服务不可用'}。录音已停止，请重试保存。`);
          if (recorder.state !== 'inactive') { setPhase('stopping'); recorder.stop(); }
        });
      };
      recorder.onerror = () => {
        markInterrupted('录音设备发生错误');
        setError('录音设备发生错误，正在尝试保存已有内容。');
        if (recorder.state !== 'inactive') recorder.stop();
      };
      recorder.onstop = () => {
        const reason = stopReasonRef.current.stopped();
        if (reason) markInterrupted(reason);
        setElapsedMs(elapsedBeforePauseRef.current + (startedAtRef.current === null ? 0 : performance.now() - startedAtRef.current));
        startedAtRef.current = null;
        release();
        void finish();
      };
      recorder.start(1000); setPhase('recording');
    } catch (err) {
      release(); clockRef.current?.close(); if (heartbeatRef.current !== null) window.clearInterval(heartbeatRef.current); heartbeatRef.current = null; setPhase('idle'); callbacks.current.onRecordingChange(false);
      const name = err instanceof DOMException ? err.name : '';
      setError(name === 'NotAllowedError' ? '没有获得麦克风权限，请允许访问后重试。' : name === 'NotFoundError' ? '没有找到可用麦克风。' : `无法开始录音：${err instanceof Error ? err.message : '请检查设备和本机服务。'}`);
    } finally { startingRef.current = false; }
  };
  const pauseOrResume = () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    if (recorder.state === 'recording') {
      elapsedBeforePauseRef.current += startedAtRef.current === null ? 0 : performance.now() - startedAtRef.current;
      startedAtRef.current = null; recorder.requestData(); recorder.pause(); setElapsedMs(elapsedBeforePauseRef.current); setPhase('paused');
    } else if (recorder.state === 'paused') {
      startedAtRef.current = performance.now(); recorder.resume(); setPhase('recording');
    }
  };
  const stop = () => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') { stopReasonRef.current.stopByUser(); setPhase('stopping'); recorder.stop(); }
  };
  const active = phase === 'recording' || phase === 'paused';
  return <section className="capture-stage" aria-label="电脑麦克风录音" data-phase={phase}>
    {phase === 'idle' ? <RecorderStart visible={visible} onStart={start} onImport={onImport} disabled={disabled} />
      : receipt ? <div className="capture-result">
        <h2 role="status"><Icon name="check" />{receipt.interrupted ? '已保存中断前的录音' : '录音已保存'}<span>{recordingDuration(receipt.file.duration)}</span></h2>
        <div className="capture-destination"><button className="capture-link" type="button" onClick={() => onView(receipt)}>我的录音</button>
          <Icon name="chevron" width="12" height="12" /><span>{courseName}</span><span>·</span><time dateTime={sessionDate}>{sessionDate}</time></div>
        {receipt.interrupted && <p className="small muted">中断前尚未写入的声音可能缺失。</p>}
        <div className="capture-actions">
          <button type="button" className="primary" disabled={disabled || phase !== 'ready' || refreshing} onClick={() => onProcess(receipt.session_id)}>开始转写并整理</button>
          <button type="button" className="capture-link" aria-expanded={replaying} onClick={() => setReplaying(!replaying)}>{replaying ? '收起回听' : '回听'}</button>
          <RevealRecordingButton sessionId={receipt.session_id} index={receipt.file.index} fileKey={receipt.file.file_key} label="打开文件夹" />
          <button type="button" className="capture-link" disabled={disabled || phase !== 'ready' || refreshing} onClick={() => {
            setReceipt(null); setReplaying(false); setError(''); setPhase('idle');
          }}>再录一段</button>
          <DeleteRecordingButton sessionId={receipt.session_id} file={receipt.file} disabled={disabled || phase !== 'ready' || refreshing}
            beforeDelete={() => { playerRef.current?.pause(); setReplaying(false); }}
            onDeleted={() => {
              setReceipt(null); setPhase('idle'); setError(''); setInterruption('');
              callbacks.current.onConfirmed();
              void callbacks.current.onSaved(receipt.session_id).catch(() => setError('录音已删除，课堂信息暂时未能刷新。'));
            }} />
        </div>
        {replaying && <audio ref={playerRef} controls autoPlay preload="metadata" aria-label="回听刚保存的录音"
          src={recordingLibrary.audioUrl(receipt.session_id, receipt.file.index, receipt.file.file_key)}
          onError={() => setError('暂时无法读取这段录音，可打开文件夹查看。')} />}
        {refreshFailed && <button type="button" disabled={refreshing} onClick={async () => {
          setRefreshing(true); callbacks.current.onRecordingChange(true);
          try { await callbacks.current.onSaved(receipt.session_id); setRefreshFailed(false); setError(''); }
          catch { setError('课堂详情暂时无法刷新，可先打开「我的录音」。'); }
          finally { setRefreshing(false); callbacks.current.onRecordingChange(false); }
        }}>{refreshing ? '正在刷新…' : '重试刷新课堂'}</button>}
      </div> : <div className="orbit-console">
        <p className="capture-device"><Icon name="mic" />电脑麦克风</p>
        <p className="capture-status" role="status"><i />{phase === 'recording' ? '正在录音' : phase === 'paused' ? '已暂停' : phase === 'stopping' ? '正在保存' : phase === 'failed' ? '保存未完成' : '正在打开麦克风…'}</p>
        <RecordingOrbit stream={meterStream} running={phase === 'recording'} visible={visible}
          actionLabel={phase === 'paused' ? '继续录音' : phase === 'recording' ? '暂停录音' : '录音控制暂不可用'}
          disabled={!active} onActivate={pauseOrResume} />
        <span className="orbit-time">{formatDuration(phase === 'starting' ? 0 : elapsedMs)}</span>
        {active && <div className="orbit-controls">
          <button type="button" onClick={pauseOrResume}><Icon name={phase === 'recording' ? 'pause' : 'play'} />{phase === 'recording' ? '暂停录音' : '继续录音'}</button>
          <button type="button" onClick={stop}><Icon name="stop" />结束并保存</button></div>}
        {phase !== 'starting' && <p className="capture-checkpoint">{savedSeconds > 0 ? `已保存至 ${recordingDuration(savedSeconds)}` : hasWritten ? '录音片段已保存到本机' : '等待第一段录音写入本机'}</p>}
        {phase === 'failed' && <button className="primary" type="button" onClick={() => void finish()}>重试保存</button>}
      </div>}
    {interruption && phase !== 'ready' && <p className="quality-warning" role="alert">录音已中断：{interruption}。正在尽力保存已有部分。</p>}
    {error && <p className="quality-warning" role="alert">{error}</p>}
  </section>;
}
