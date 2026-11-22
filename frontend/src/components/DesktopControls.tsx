import { useEffect, useRef, useState } from 'react';
import './desktopControls.css';

type Action = 'ready' | 'state' | 'resize-top' | 'minimize' | 'size' | 'close';
type Frame = (action: Action) => Promise<{ maximized: boolean } | null>;
type DesktopWindow = Window & { pywebview?: { api?: { desktop_frame?: Frame } } };

export function DesktopControls() {
  const bridge = useRef<Frame>();
  const [maximized, setMaximized] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    let resizeTimer = 0;
    async function connect() {
      const frame = (window as DesktopWindow).pywebview?.api?.desktop_frame;
      if (!frame) return;
      const state = await frame('ready').catch(() => null);
      if (live && state) {
        bridge.current = frame;
        setMaximized(state.maximized);
      }
    }
    function resized() {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(async () => {
        const state = await bridge.current?.('state').catch(() => null);
        if (live && state) setMaximized(state.maximized);
      }, 120);
    }
    void connect();
    window.addEventListener('pywebviewready', connect);
    window.addEventListener('resize', resized);
    return () => {
      live = false;
      window.clearTimeout(resizeTimer);
      window.removeEventListener('pywebviewready', connect);
      window.removeEventListener('resize', resized);
    };
  }, []);

  const hasFrame = maximized !== null;
  useEffect(() => {
    if (!hasFrame) return;
    document.documentElement.classList.add('desktop-frame');
    return () => document.documentElement.classList.remove('desktop-frame');
  }, [hasFrame]);

  if (maximized === null) return null;
  return <>
    <div className="desktop-drag-area" aria-hidden="true" />
    {!maximized && <div className="desktop-resize-top" aria-hidden="true" onPointerDown={event => {
      if (event.button === 0) { event.preventDefault(); void bridge.current?.('resize-top'); }
    }} />}
    <div className="desktop-controls" data-maximized={maximized || undefined} role="group" aria-label="窗口控制">
      <button type="button" title="最小化" aria-label="最小化" onClick={() => void bridge.current?.('minimize')}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 11.5h10" /></svg>
      </button>
      <button type="button" title={maximized ? '还原窗口' : '最大化'} aria-label={maximized ? '还原窗口' : '最大化'}
        onClick={async () => { const state = await bridge.current?.('size'); if (state) setMaximized(state.maximized); }}>
        <svg viewBox="0 0 16 16" aria-hidden="true">{maximized ? <path d="M5 3h8v8h-2M3 5h8v8H3z" /> : <rect x="3" y="3" width="10" height="10" rx=".5" />}</svg>
      </button>
      <button type="button" className="desktop-close" title="关闭窗口" aria-label="关闭窗口" onClick={() => void bridge.current?.('close')}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 3.5 9 9m0-9-9 9" /></svg>
      </button>
    </div>
  </>;
}
