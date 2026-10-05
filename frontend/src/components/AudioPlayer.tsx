import { useEffect, useRef, useState } from 'react';
import { AudioFile } from '../types';

const SINGLE_AUDIO_FILE: AudioFile[] = [{ index: 0, name: '课堂录音', bytes: null, duration: null }];

export function AudioPlayer({ sessionId, files, seekSignal, onDuration }: {
  sessionId: string; files?: AudioFile[]; seekSignal: { seconds: number } | null;
  onDuration?: (n: number) => void;
}) {
  const ref = useRef<HTMLAudioElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [pendingSeek, setPendingSeek] = useState<number | null>(null);
  const audioFiles = files?.length ? files : SINGLE_AUDIO_FILE;
  const activeFile = audioFiles[activeIndex] || audioFiles[0];

  useEffect(() => {
    if (activeIndex >= audioFiles.length) {
      setActiveIndex(0);
      setPendingSeek(null);
    }
  }, [activeIndex, audioFiles.length]);

  useEffect(() => {
    if (seekSignal == null) return;
    let index = 0;
    let remaining = seekSignal.seconds;
    for (let i = 0; i < audioFiles.length; i += 1) {
      const duration = audioFiles[i].duration || 0;
      if (i === audioFiles.length - 1 || remaining < duration) { index = i; break; }
      remaining -= duration;
    }
    setPendingSeek(Math.max(0, remaining));
    setActiveIndex(index);
  }, [seekSignal, audioFiles]);

  useEffect(() => {
    if (pendingSeek == null || !ref.current || ref.current.readyState < 1) return;
    ref.current.currentTime = pendingSeek;
    ref.current.play().catch(() => {});
    setPendingSeek(null);
  }, [activeIndex, pendingSeek]);

  const src = audioFiles.length > 1
    ? `/api/sessions/${sessionId}/audio/${activeFile.index}`
    : `/api/sessions/${sessionId}/audio`;

  return (
    <div className="audio-bar">
      {audioFiles.length > 1 && <div className="row" style={{ marginBottom: 8, justifyContent: 'space-between' }}>
        <label style={{ margin: 0 }}>录音分段</label>
        <select value={activeIndex} onChange={(e) => { setPendingSeek(null); setActiveIndex(Number(e.target.value)); }}>
          {audioFiles.map((file, index) => <option key={file.index} value={index}>{index + 1}. {file.name}</option>)}
        </select>
      </div>}
      <audio ref={ref} controls preload="metadata" src={src} onLoadedMetadata={e => {
        if (Number.isFinite(e.currentTarget.duration)) onDuration?.(e.currentTarget.duration);
        if (pendingSeek != null) {
          e.currentTarget.currentTime = pendingSeek;
          e.currentTarget.play().catch(() => {});
          setPendingSeek(null);
        }
      }} />
    </div>
  );
}
