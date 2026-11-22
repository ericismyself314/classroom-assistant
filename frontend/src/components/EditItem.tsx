import { useState } from 'react';
import { api } from '../api';
import { CATEGORY_LABELS, type Item } from '../types';
import { Modal } from './Modal';

function quotes(item: Item): string[] {
  try {
    const evidence = JSON.parse(item.evidence_data || '[]');
    return Array.isArray(evidence) ? evidence.map(e => e?.quote).filter(q => typeof q === 'string' && q.trim()) : [];
  } catch { return []; }
}

export function EditItem({item, onClose, onSave}: {item: Item; onClose: () => void; onSave: (patch: Partial<Item>) => void}) {
  const [title, setTitle] = useState(item.title);
  const [details, setDetails] = useState(item.details || '');
  const [category, setCategory] = useState(item.category);
  const [date, setDate] = useState(item.normalized_date || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const evidence = quotes(item);
  async function save() {
    if (!title.trim() || busy) return;
    setBusy(true); setError('');
    const patch = {title: title.trim(), details: details.trim(), category, normalized_date: date || null};
    try { await api.updateItem(item.id, patch); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '保存失败，请重试。'); setBusy(false); return; }
    onSave(patch); onClose();
  }
  return <Modal title="编辑重要事项" onClose={() => { if (!busy) onClose(); }}
    footer={<><button disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || !title.trim()} onClick={save}>{busy ? '保存中…' : '保存'}</button></>}>
    <div className="ir-dialog">
      <label>标题<input value={title} required onChange={e => setTitle(e.target.value)} /></label>
      <label>说明<textarea rows={3} value={details} onChange={e => setDetails(e.target.value)} /></label>
      <label>分类<select className="app-select" value={category} onChange={e => setCategory(e.target.value)}>{Object.entries(CATEGORY_LABELS).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>{category === 'assignment' ? '截止日期' : '事项日期'}<input type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
      {(evidence.length > 0 || item.raw_date_text) && <details><summary>查看课堂原话</summary>
        {item.raw_date_text && <p>原话日期「{item.raw_date_text}」</p>}
        {evidence.map((q,i) => <blockquote key={i}>{q}</blockquote>)}</details>}
      {error && <p role="alert">{error}</p>}
    </div>
  </Modal>;
}
