import { useEffect, useRef, useState } from 'react';
import { AudioFile } from '../types';
import { Icon } from './Icon';

const SINGLE_AUDIO_FILE: AudioFile[] = [{ index: 0, name: '课堂录音', bytes: null, duration: null }];
const time = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

export function AudioPlayer({ sessionId, files, seekSignal, onDuration, fileSignal, source, compact = false, autoPlay = false, initialDuration, onPosition }: {
  sessionId: string; files?: AudioFile[]; seekSignal: { seconds: number } | null; onDuration?: (n: number) => void;
  source?: string; compact?: boolean; autoPlay?: boolean; initialDuration?: number | null; onPosition?: (seconds: number | null) => void;
  fileSignal?: { index: number } | null;
}) {
  const ref = useRef<HTMLAudioElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [pendingSeek, setPendingSeek] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [rate, setRate] = useState(1);
  const [error, setError] = useState('');
  const audioFiles = files?.length ? files : SINGLE_AUDIO_FILE;
  const filesRef = useRef(audioFiles);
  filesRef.current = audioFiles;
  const activeFile = audioFiles[activeIndex] || audioFiles[0];

  useEffect(() => {
    if (!fileSignal) return;
    const index = audioFiles.findIndex(file => file.index === fileSignal.index);
    if (index < 0) return;
    setActiveIndex(index);
    setPendingSeek(0);
  }, [fileSignal, sessionId]);

  useEffect(() => {
    if (activeIndex >= audioFiles.length) { setActiveIndex(0); setPendingSeek(null); }
  }, [activeIndex, audioFiles.length]);
  useEffect(() => {
    if (seekSignal == null) return;
    const audioFiles = filesRef.current;
    let index = 0;
    let remaining = seekSignal.seconds;
    for (let i = 0; i < audioFiles.length; i += 1) {
      const length = audioFiles[i].duration || 0;
      if (i === audioFiles.length - 1 || remaining < length) { index = i; break; }
      remaining -= length;
    }
    setPendingSeek(Math.max(0, remaining)); setActiveIndex(index);
  }, [seekSignal, sessionId]);
  useEffect(() => {
    if (pendingSeek == null || !ref.current || ref.current.readyState < 1) return;
    ref.current.currentTime = pendingSeek;
    void ref.current.play().catch(() => {});
    setPendingSeek(null);
  }, [activeIndex, pendingSeek]);
  useEffect(() => {
    setPosition(0); setDuration(activeFile.duration || initialDuration || 0); setPlaying(false); setError('');
  }, [sessionId, activeFile.index, source, initialDuration]);

  const audioPath = audioFiles.length > 1 ? `/api/sessions/${sessionId}/audio/${activeFile.index}` : `/api/sessions/${sessionId}/audio`;
  const src = source ?? (activeFile.hash ? `${audioPath}?v=${encodeURIComponent(activeFile.hash)}` : audioPath);
  async function toggle() {
    const audio = ref.current;
    if (!audio) return;
    if (!audio.paused) { audio.pause(); return; }
    try { await audio.play(); setError(''); }
    catch { setError('录音暂时无法播放，请检查文件或稍后重试。'); }
  }
  return <div className={`audio-bar glass-player jade-player${compact ? " is-compact" : ""}`}>
    {audioFiles.length > 1 && <label className="audio-file-select">录音分段<select value={activeIndex} onChange={e => { setPendingSeek(null); setActiveIndex(Number(e.target.value)); }}>{audioFiles.map((file, index) => <option key={file.index} value={index}>{index + 1}. {file.name}</option>)}</select></label>}
    <div className="player-controls">
      <button className="player-toggle" aria-label={playing ? '暂停播放' : '播放录音'} onClick={toggle}><Icon name={playing ? 'pause' : 'play'} /></button>
      <input style={{ backgroundSize: `${duration ? Math.min(100, position / duration * 100) : 0}% 100%` }} className="player-progress" aria-label="播放进度" type="range" min="0" max={duration || 1} step="0.1" value={Math.min(position, duration || 1)} disabled={!duration} onChange={e => {
        const seconds = Number(e.target.value); if (ref.current) ref.current.currentTime = seconds; setPosition(seconds);
      }} />
      <span className="player-time">{time(position)} / {duration > 0 ? time(duration) : '—:—'}</span>
      {!compact && <><label className="player-volume"><Icon name="volume" /><input aria-label="音量" type="range" min="0" max="1" step="0.05" value={volume} onChange={e => { const value = Number(e.target.value); setVolume(value); if (ref.current) ref.current.volume = value; }} /></label>
      <select className="player-rate" aria-label="播放速度" value={rate} onChange={e => { const value = Number(e.target.value); setRate(value); if (ref.current) ref.current.playbackRate = value; }}>{[0.75, 1, 1.25, 1.5, 2].map(value => <option key={value} value={value}>{value}×</option>)}</select></>}
    </div>
    <audio key={src} autoPlay={autoPlay} ref={ref} preload="metadata" src={src} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => { setPlaying(false); onPosition?.(null); }} onTimeUpdate={e => {
      const seconds = e.currentTarget.currentTime;
      setPosition(seconds);
      if (!e.currentTarget.ended && (!e.currentTarget.paused || seconds > 0)) {
        const offset = audioFiles.slice(0, activeIndex).reduce((sum, file) => sum + (file.duration || 0), 0);
        onPosition?.(offset + seconds);
      }
    }} onDurationChange={e => {
      const length = e.currentTarget.duration;
      if (Number.isFinite(length) && length > 0) { setDuration(length); onDuration?.(length); }
    }} onError={() => setError('无法读取录音文件。')} onLoadedMetadata={e => {
      const audio = e.currentTarget;
      const knownDuration = Number.isFinite(audio.duration) ? audio.duration : activeFile.duration || initialDuration;
      if (knownDuration && knownDuration > 0) { setDuration(knownDuration); onDuration?.(knownDuration); }
      audio.volume = volume; audio.playbackRate = rate;
      if (pendingSeek != null) { audio.currentTime = pendingSeek; void audio.play().catch(() => {}); setPendingSeek(null); }
    }} />
    {error && <p className="quality-warning" role="alert">{error}</p>}
  </div>;
}
