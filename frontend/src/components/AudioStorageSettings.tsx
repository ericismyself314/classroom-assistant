import { useEffect, useRef, useState } from 'react';
import { api, type AudioStorageStatus } from '../api';

export function AudioStorageSettings({ formatSize, onFinished }: {
  formatSize: (bytes: number) => string; onFinished: () => void;
}) {
  const [state, setState] = useState<AudioStorageStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const inFlight = useRef(0);
  const refreshDisk = useRef(onFinished);
  refreshDisk.current = onFinished;
  const remaining = (state?.pending || 0) + (state?.working || 0);

  useEffect(() => {
    let live = true;
    const fetchState = async () => {
      const request = ++inFlight.current;
      try {
        const next = await api.audioStorage();
        if (!live || request !== inFlight.current) return;
        setState(next);
        setError('');
        if (remaining && !next.working && !next.pending) refreshDisk.current();
      } catch (e: any) {
        if (live && request === inFlight.current) setError(e.message || '暂时无法读取录音空间。');
      }
    };
    void fetchState();
    const timer = window.setInterval(fetchState, remaining ? 2500 : 15000);
    return () => { live = false; window.clearInterval(timer); };
  }, [remaining, retry]);

  async function change(action: () => Promise<AudioStorageStatus>) {
    ++inFlight.current;
    setBusy(true);
    try { const next = await action(); ++inFlight.current; setState(next); setError(''); }
    catch (e: any) { setError(e.message || '操作失败，请重试。'); }
    finally { setBusy(false); }
  }

  return <div className="audio-storage-settings">
    <h3>录音保留方式</h3>
    <p>转写和翻译完成后，用已校验的语音压缩版替换应用内的原音，仍可点时间回听。</p>
    {state && <>
      <label className="retention-option"><input type="checkbox" role="switch" checked={state.text_only} disabled={busy}
        aria-describedby="retention-help" onChange={e => { const enabled = e.currentTarget.checked; void change(() => api.setAudioRetention(enabled)); }} />
        <strong>7天后只留文字</strong></label>
      <p id="retention-help">开启后，已整理的旧录音再保留 7 天，新录音从整理完成起算。到期后在软件打开时清理应用内的音频，保留转写、译文和笔记。</p>
      <div className="archive-action"><button type="button" disabled={busy || remaining > 0 || !state.eligible}
        onClick={() => void change(api.compressRecordings)}>{remaining ? '正在压缩…' : '压缩已有录音'}</button>
        <span role="status">{remaining ? `还剩 ${remaining} 节课` : state.eligible ? `${state.eligible} 节已整理的课堂可压缩` : '当前没有待压缩的课堂'}</span></div>
      {(state.done > 0 || state.unchanged > 0 || state.failed > 0) && <p aria-live="polite">
        {[`累计节省 ${formatSize(state.saved_bytes)}`, state.unchanged ? `${state.unchanged} 节保留原文件` : '',
          state.failed ? `${state.failed} 节未完成，可重新压缩` : ''].filter(Boolean).join(' · ')}
        {state.failed > 0 && state.error && <><br />{state.error}</>}
      </p>}
    </>}
    {!state && !error && <p role="status">正在读取录音空间…</p>}
    {error && <p role="alert">{error} <button type="button" onClick={() => setRetry(n => n + 1)}>重试</button></p>}
  </div>;
}
