import { useEffect, useState } from 'react';

export function MicrophoneWaveform({ stream, active }: { stream: MediaStream | null; active: boolean }) {
  const [levels, setLevels] = useState<number[]>(Array(48).fill(3));
  useEffect(() => {
    if (!stream || !active || typeof AudioContext === 'undefined') {
      setLevels(Array(48).fill(3));
      return;
    }
    let context: AudioContext | null = null;
    let timer: number | undefined;
    try {
      context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      void context.resume().catch(() => {});
      const samples = new Uint8Array(analyser.frequencyBinCount);
      timer = window.setInterval(() => {
        analyser.getByteTimeDomainData(samples);
        setLevels(Array.from({ length: 48 }, (_, i) => {
          const sample = samples[Math.floor(i * samples.length / 48)];
          return Math.min(80, Math.max(3, Math.abs(sample - 128) * 1.8));
        }));
      }, 90);
    } catch { /* Recording still works when an input meter is unavailable. */ }
    return () => { window.clearInterval(timer); if (context) void context.close().catch(() => {}); };
  }, [stream, active]);
  return <div className="mic-waveform" aria-hidden="true">{levels.map((height, index) => <i key={index} style={{ height }} />)}</div>;
}
