import { useState } from 'react';
import { recordingLibrary } from '../recordingLibrary';
import type { RecordingFile } from '../recordingLibrary';
import { Modal } from './Modal';

export function DeleteRecordingButton({ sessionId, file, disabled, beforeDelete, onDeleted }: {
  sessionId: string; file: RecordingFile; disabled?: boolean; beforeDelete: () => void; onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function remove() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      beforeDelete();
      await recordingLibrary.deleteStagedFile(sessionId, file);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '删除失败，请重试。'); return;
    } finally { setBusy(false); }
    setOpen(false); onDeleted();
  }
  return <>
    <button type="button" className="ghost danger" disabled={disabled} onClick={() => { setError(''); setOpen(true); }}>删除这段录音</button>
    {open && <Modal title="删除这段录音？" onClose={() => { if (!busy) setOpen(false); }}
      footer={<><button type="button" disabled={busy} onClick={() => setOpen(false)}>取消</button>
        <button type="button" className="danger" disabled={busy} onClick={() => void remove()}>{busy ? '正在删除…' : '确认删除'}</button></>}>
      <p>「{file.name}」将从本机删除，无法撤销。其他录音和课堂笔记会保留。</p>
      {error && <p role="alert" className="quality-warning">{error}</p>}
    </Modal>}
  </>;
}

export function RevealRecordingButton({ sessionId, index, fileKey, disabled = false, label = '在文件夹中显示' }: {
  sessionId: string; index: number; fileKey?: string; disabled?: boolean; label?: string;
}) {
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');
  async function reveal() {
    setOpening(true); setError('');
    try { await recordingLibrary.reveal(sessionId, index, fileKey); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '暂时无法显示文件。'); }
    finally { setOpening(false); }
  }
  return <span className="recording-reveal">
    <button type="button" className="ghost" onClick={reveal} disabled={disabled || opening}>
      {opening ? '正在定位…' : label}
    </button>
    {error && <span role="alert" className="recording-action-error">{error}</span>}
  </span>;
}
