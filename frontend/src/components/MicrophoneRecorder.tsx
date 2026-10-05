import { useEffect, useRef, useState } from 'react';

type RecorderPhase = 'idle' | 'recording' | 'paused' | 'ready';

function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((n) => String(n).padStart(2, '0')).join(':');
}

function recordingFilename(extension: string): string {
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
    '-',
    String(now.getHours()).padStart(2, '0'),
    String(now.getMinutes()).padStart(2, '0'),
    String(now.getSeconds()).padStart(2, '0'),
  ].join('');
  return `课堂录音-${stamp}${extension}`;
}

function microphoneError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return '没有获得麦克风权限。请在权限提示中允许访问；若被 Windows 拦截，请到「设置 → 隐私和安全性 → 麦克风」允许桌面应用访问。';
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return '没有找到可用麦克风，请连接或启用麦克风后重试。';
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return '麦克风暂时无法使用，可能正被其他程序占用；关闭占用它的程序后重试。';
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return '当前窗口不支持麦克风录音，请更新课堂助手后重试。';
  }
  return '麦克风启动失败，请检查设备和 Windows 麦克风权限后重试。';
}

export function MicrophoneRecorder({ onAddFile, onRemoveFile, onRecordingChange, disabled = false, hidden = false }: {
  onAddFile: (file: File) => boolean;
  onRemoveFile?: (file: File) => boolean;
  onRecordingChange?: (active: boolean) => void;
  disabled?: boolean;
  hidden?: boolean;
}) {
  const [phase, setPhase] = useState<RecorderPhase>('idle');
  const [elapsedMs, setElapsedMs] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [added, setAdded] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number | null>(null);
  const elapsedBeforePauseRef = useRef(0);
  const previewUrlRef = useRef('');
  const onAddFileRef = useRef(onAddFile);
  const onRemoveFileRef = useRef(onRemoveFile);
  const discardOnStopRef = useRef(false);

  useEffect(() => { onAddFileRef.current = onAddFile; }, [onAddFile]);
  useEffect(() => { onRemoveFileRef.current = onRemoveFile; }, [onRemoveFile]);

  useEffect(() => {
    onRecordingChange?.(phase === 'recording' || phase === 'paused');
  }, [phase, onRecordingChange]);

  useEffect(() => {
    if (phase !== 'recording') return;
    const timer = window.setInterval(() => {
      const startedAt = startedAtRef.current;
      setElapsedMs(elapsedBeforePauseRef.current + (startedAt == null ? 0 : Date.now() - startedAt));
    }, 500);
    return () => window.clearInterval(timer);
  }, [phase]);

  useEffect(() => {
    if (!hidden) return;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
  }, [hidden]);

  useEffect(() => () => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = null;
      try { recorder.stop(); } catch { /* already stopping */ }
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
  }, []);

  const releaseStream = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  const start = async () => {
    setError('');
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError(microphoneError(null));
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const preferredType = ['audio/webm;codecs=opus', 'audio/webm']
        .find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, preferredType ? { mimeType: preferredType, audioBitsPerSecond: 96000 } : undefined);
      recorderRef.current = recorder;
      chunksRef.current = [];
      elapsedBeforePauseRef.current = 0;
      startedAtRef.current = Date.now();
      setElapsedMs(0);
      setFile(null);
      setAdded(false);
      setNotice('');
      discardOnStopRef.current = false;
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = '';
      setPreviewUrl('');
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        setError('录音过程中出现问题，请停止录音并检查麦克风。');
        if (recorder.state !== 'inactive') recorder.stop();
        else {
          releaseStream();
          setPhase('idle');
        }
      };
      recorder.onstop = () => {
        const duration = elapsedBeforePauseRef.current
          + (startedAtRef.current == null ? 0 : Date.now() - startedAtRef.current);
        startedAtRef.current = null;
        releaseStream();
        if (discardOnStopRef.current) {
          discardOnStopRef.current = false;
          chunksRef.current = [];
          setElapsedMs(0);
          setFile(null);
          setAdded(false);
          if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
          previewUrlRef.current = '';
          setPreviewUrl('');
          setPhase('idle');
          setError('');
          setNotice('本段录音已丢弃，没有加入课堂清单。');
          return;
        }
        setElapsedMs(duration);
        const mimeType = recorder.mimeType || preferredType || 'audio/webm';
        const blob = new Blob(chunksRef.current, { type: mimeType });
        chunksRef.current = [];
        if (!blob.size) {
          setPhase('idle');
          setError('没有录到声音，请确认麦克风输入正常后重试。');
          return;
        }
        const extension = mimeType.toLowerCase().includes('mp4') ? '.mp4' : '.webm';
        const recordedFile = new File([blob], recordingFilename(extension), { type: mimeType, lastModified: Date.now() });
        const url = URL.createObjectURL(recordedFile);
        previewUrlRef.current = url;
        setPreviewUrl(url);
        setFile(recordedFile);
        const accepted = onAddFileRef.current(recordedFile);
        setAdded(accepted);
        if (!accepted) setError('录音暂未加入列表，请检查录音数量上限或提示信息。');
        setPhase('ready');
      };
      recorder.start(1000);
      setPhase('recording');
    } catch (err) {
      releaseStream();
      startedAtRef.current = null;
      setPhase('idle');
      setError(microphoneError(err));
    }
  };

  const pauseOrResume = () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    if (phase === 'recording' && recorder.state === 'recording') {
      elapsedBeforePauseRef.current += startedAtRef.current == null ? 0 : Date.now() - startedAtRef.current;
      startedAtRef.current = null;
      recorder.pause();
      setElapsedMs(elapsedBeforePauseRef.current);
      setPhase('paused');
    } else if (phase === 'paused' && recorder.state === 'paused') {
      startedAtRef.current = Date.now();
      recorder.resume();
      setPhase('recording');
    }
  };

  const stop = () => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
  };

  const stopAndDiscard = () => {
    if (!window.confirm('确定停止并丢弃当前这段录音吗？尚未保存的内容将无法恢复。')) return;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      discardOnStopRef.current = true;
      setError('');
      recorder.stop();
    }
  };

  const deleteReadyRecording = () => {
    if (!file) return;
    if (!window.confirm(added ? '确定从本次录音清单移除这段录音吗？' : '确定删除这段尚未加入课堂的录音吗？')) return;
    if (added && !onRemoveFileRef.current?.(file)) {
      setError('没有从课堂清单中移除成功，请在右侧录音清单中重试。');
      return;
    }
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = '';
    setPreviewUrl('');
    setFile(null);
    setAdded(false);
    setElapsedMs(0);
    setError('');
    setNotice('本段录音已删除。');
    setPhase('idle');
  };

  const download = () => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = file.name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  const addToSession = () => {
    if (!file) return;
    if (onAddFileRef.current(file)) {
      setAdded(true);
      setError('');
    } else {
      setError('录音没有加入列表，请检查录音数量上限或提示信息。');
    }
  };

  const recordAnother = () => {
    if (file && !added && !window.confirm('这段录音还没有加入课堂。重新录制会丢弃它，确定继续吗？')) return;
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = '';
    setPreviewUrl('');
    setFile(null);
    setAdded(false);
    setElapsedMs(0);
    elapsedBeforePauseRef.current = 0;
    setError('');
    setNotice('');
    setPhase('idle');
  };

  return (
    <section className="mic-recorder" aria-label="电脑麦克风录音" hidden={hidden}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <b>电脑麦克风录音</b>
        </div>
        {(phase === 'recording' || phase === 'paused') && <span className={`badge ${phase === 'recording' ? 'red' : 'gray'}`}>{phase === 'recording' ? '正在录音' : '已暂停'}</span>}
      </div>
      <div className="row mic-recorder-controls">
        {phase === 'idle' && <button className="primary" type="button" disabled={disabled} onClick={start}>开始录音</button>}
        {(phase === 'recording' || phase === 'paused') && <>
          <span className="mic-recorder-time" aria-live="off">{formatDuration(elapsedMs)}</span>
          <button type="button" disabled={disabled} onClick={pauseOrResume}>{phase === 'recording' ? '暂停录音' : '继续录音'}</button>
          <button className="danger" type="button" disabled={disabled} onClick={stop}>停止并试听</button>
          <button className="ghost danger" type="button" disabled={disabled} onClick={stopAndDiscard}>停止并丢弃本段</button>
        </>}
        {phase === 'ready' && file && <>
          <span className="small muted">{formatDuration(elapsedMs)} · {(file.size / 1024 / 1024).toFixed(1)} MB</span>
          <button type="button" disabled={disabled} onClick={download}>保存到电脑</button>
          {added ? <span className="small muted">已加入本节录音列表</span>
            : <button className="primary" type="button" disabled={disabled} onClick={addToSession}>加入本节录音</button>}
          <button className="ghost danger" type="button" disabled={disabled} onClick={deleteReadyRecording}>{added ? '从本次清单移除' : '删除本段录音'}</button>
          <button className="ghost" type="button" disabled={disabled} onClick={recordAnother}>再录一段</button>
        </>}
      </div>
      {previewUrl && <audio className="mic-recorder-preview" controls preload="metadata" src={previewUrl} />}
      {phase === 'idle' && <div className="small muted mic-recorder-note">首次使用需允许麦克风权限。录音在保存或加入本节课后保留。</div>}
      {notice && <p className="hint" role="status">{notice}</p>}
      {error && <p className="quality-warning" role="alert">{error}</p>}
    </section>
  );
}
