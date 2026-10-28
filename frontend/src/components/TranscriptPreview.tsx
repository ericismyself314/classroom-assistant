import { useEffect, useState } from 'react';
import { api } from '../api';
import { displayText, hasChinese } from '../chineseDisplay';
import { Segment } from '../types';

export function TranscriptPreview({ sessionId }: { sessionId?: string }) {
  const [data, setData] = useState<any>(null);
  const [tab, setTab] = useState<'transcript' | 'notes'>('transcript');
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    setData(null); setError('');
    async function load() {
      if (!sessionId) return;
      try {
        const result = await api.sessionDetail(sessionId);
        if (!live) return;
        setData(result); setError('');
        if (['waiting_model', 'file_check', 'transcribing', 'analyzing', 'indexing'].includes(result.session.status)) timer = window.setTimeout(load, 2500);
      } catch (e: any) { if (live) setError(e.message || '转写读取失败'); }
    }
    void load();
    return () => { live = false; window.clearTimeout(timer); };
  }, [sessionId]);
  const segments: Segment[] = data?.segments || [];
  const chapters: any[] = data?.notes?.knowledge || data?.notes?.chapters || [];
  return <section className="transcript-preview" aria-label="已保存课堂内容">
    <div className="segmented" role="group" aria-label="课堂内容视图">
      <button aria-pressed={tab === 'transcript'} className={tab === 'transcript' ? 'active' : ''} onClick={() => setTab('transcript')}>转写原文</button>
      <button aria-pressed={tab === 'notes'} className={tab === 'notes' ? 'active' : ''} onClick={() => setTab('notes')}>中文整理</button>
    </div>
    {error ? <p className="quality-warning" role="alert">{error}</p> : tab === 'transcript' && segments.length ? <div className="preview-segments">{segments.slice(0, 8).map(segment => <div key={segment.id}><time>{segment.start_seconds == null ? '文字稿' : `${Math.floor(segment.start_seconds / 60)}:${String(Math.floor(segment.start_seconds % 60)).padStart(2, '0')}`}</time><p>{displayText(segment.display_text, hasChinese(segment.text) ? segment.text : '')}{segment.text && <details><summary>识别原文</summary>{segment.text}</details>}</p></div>)}</div>
      : tab === 'notes' && chapters.length ? <div className="preview-notes">{chapters.slice(0, 5).map((chapter, index) => <article key={index}><h3>{displayText(chapter.title, '')}</h3><p>{displayText(chapter.summary, '')}</p></article>)}</div>
      : <div className="preview-placeholder"><h3>{sessionId ? '暂无已保存的内容' : '课堂转写与整理'}</h3><p>结束录音并提交处理后，转写和课堂整理会显示在这里。</p><span>录音过程中不会自动生成实时转写。</span></div>}
    {(segments.length > 8 || chapters.length > 5) && <p className="small muted">更多内容可在课堂详情查看。</p>}
  </section>;
}
