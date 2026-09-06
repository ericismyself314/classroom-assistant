import { useEffect, useRef } from 'react';

export function AudioPlayer({ sessionId, seekSignal, onDuration }: { sessionId: string; seekSignal: { seconds: number } | null; onDuration?: (n: number) => void }) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    if (seekSignal != null && ref.current) {
      ref.current.currentTime = seekSignal.seconds;
      ref.current.play().catch(() => {});
    }
  }, [seekSignal]);
  return (
    <div className="audio-bar">
      <audio ref={ref} controls preload="metadata" src={`/api/sessions/${sessionId}/audio`} onLoadedMetadata={e => {
        if (Number.isFinite(e.currentTarget.duration)) onDuration?.(e.currentTarget.duration);
      }} />
      <div className="small muted" style={{ marginTop: 4 }}>
        速度：使用播放器右键或内置控件切换 0.75x / 1x / 1.25x / 1.5x / 2x；点击转写时间可跳转到对应位置。
      </div>
    </div>
  );
}
