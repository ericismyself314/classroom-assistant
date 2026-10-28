import { useEffect, useRef, useState } from 'react';
import { AudioFile } from '../types';
import { Icon } from './Icon';

const SINGLE_AUDIO_FILE: AudioFile[] = [{ index: 0, name: '课堂录音', bytes: null, duration: null }];
const time = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

export function AudioPlayer({ sessionId, files, seekSignal, onDuration, fileSignal }: {
  sessionId: string; files?: AudioFile[]; seekSignal: { seconds: number } | null; onDuration?: (n: number) => void;
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
    let index = 0;
    let remaining = seekSignal.seconds;
    for (let i = 0; i < audioFiles.length; i += 1) {
      const length = audioFiles[i].duration || 0;
      if (i === audioFiles.length - 1 || remaining < length) { index = i; break; }
      remaining -= length;
    }
    setPendingSeek(Math.max(0, remaining)); setActiveIndex(index);
  }, [seekSignal, audioFiles]);
  useEffect(() => {
    if (pendingSeek == null || !ref.current || ref.current.readyState < 1) return;
    ref.current.currentTime = pendingSeek;
    void ref.current.play().catch(() => {});
    setPendingSeek(null);
  }, [activeIndex, pendingSeek]);
  useEffect(() => {
    setPosition(0); setDuration(activeFile.duration || 0); setPlaying(false); setError('');
  }, [sessionId, activeFile.index]);

  const src = audioFiles.length > 1 ? `/api/sessions/${sessionId}/audio/${activeFile.index}` : `/api/sessions/${sessionId}/audio`;
  async function toggle() {
    const audio = ref.current;
    if (!audio) return;
    if (!audio.paused) { audio.pause(); return; }
    try { await audio.play(); setError(''); }
    catch { setError('录音暂时无法播放，请检查文件或稍后重试。'); }
  }
  return <div className="audio-bar glass-player">
    {audioFiles.length > 1 && <label className="audio-file-select">录音分段<select value={activeIndex} onChange={e => { setPendingSeek(null); setActiveIndex(Number(e.target.value)); }}>{audioFiles.map((file, index) => <option key={file.index} value={index}>{index + 1}. {file.name}</option>)}</select></label>}
    <div className="player-controls">
      <button className="player-toggle" aria-label={playing ? '暂停播放' : '播放录音'} onClick={toggle}><Icon name={playing ? 'pause' : 'play'} /></button>
      <input className="player-progress" aria-label="播放进度" type="range" min="0" max={duration || 1} step="0.1" value={Math.min(position, duration || 1)} disabled={!duration} onChange={e => {
        const seconds = Number(e.target.value); if (ref.current) ref.current.currentTime = seconds; setPosition(seconds);
      }} />
      <span className="player-time">{time(position)} / {time(duration)}</span>
      <label className="player-volume"><Icon name="volume" /><input aria-label="音量" type="range" min="0" max="1" step="0.05" value={volume} onChange={e => { const value = Number(e.target.value); setVolume(value); if (ref.current) ref.current.volume = value; }} /></label>
      <select className="player-rate" aria-label="播放速度" value={rate} onChange={e => { const value = Number(e.target.value); setRate(value); if (ref.current) ref.current.playbackRate = value; }}>{[0.75, 1, 1.25, 1.5, 2].map(value => <option key={value} value={value}>{value}×</option>)}</select>
    </div>
    <audio ref={ref} preload="metadata" src={src} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onTimeUpdate={e => setPosition(e.currentTarget.currentTime)} onError={() => setError('无法读取录音文件。')} onLoadedMetadata={e => {
      const audio = e.currentTarget;
      const knownDuration = Number.isFinite(audio.duration) ? audio.duration : activeFile.duration;
      if (knownDuration && knownDuration > 0) { setDuration(knownDuration); onDuration?.(knownDuration); }
      audio.volume = volume; audio.playbackRate = rate;
      if (pendingSeek != null) { audio.currentTime = pendingSeek; void audio.play().catch(() => {}); setPendingSeek(null); }
    }} />
    {error && <p className="quality-warning" role="alert">{error}</p>}
  </div>;
}
