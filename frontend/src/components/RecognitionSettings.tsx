import { useEffect, useState } from 'react';
import { api } from '../api';

export interface RecognitionValue { language_mode: string; vocabulary: string }
export function useRecognition(courseId: string) {
  const [value, setValue] = useState<RecognitionValue>({ language_mode: 'auto', vocabulary: '' });
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setReady(false); setError('');
    api.getCourse(courseId).then(({ course }) => {
      if (live) { setValue({ language_mode: course.language_mode || 'auto', vocabulary: course.vocabulary || '' }); setReady(true); }
    }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [courseId]);
  return { value, setValue, ready, error, save: () => api.updateCourse(courseId, value) };
}
export function RecognitionSettings({ value, onChange, disabled = false }: {
  value: RecognitionValue; onChange: (v: RecognitionValue) => void; disabled?: boolean;
}) {
  return <fieldset className="recognition-settings" disabled={disabled}>
    <label htmlFor="recognition-language">老师主要用什么语言讲课？</label>
    <select id="recognition-language" value={value.language_mode} onChange={e => onChange({ ...value, language_mode: e.target.value })}>
      <option value="auto">自动识别（不确定时使用）</option>
      <option value="en">英文授课</option><option value="zh">中文授课</option>
      <option value="mix">中英混合（分段识别，较慢）</option>
    </select>
    <p className="small muted">知道授课语言时请直接选择，可减少误识别。会记住这门课的设置。</p>
    <details><summary>课程术语 · 可选</summary>
      <label htmlFor="recognition-terms">填写本课常见的专有名词，逗号分隔</label>
      <textarea id="recognition-terms" rows={3} maxLength={800} value={value.vocabulary}
        onChange={e => onChange({ ...value, vocabulary: e.target.value })} placeholder="填写课件中的真实术语或人名；不填也可以" />
      <div className="small muted">只帮助辨词，不会把这些词直接写进课堂总结。</div>
    </details>
  </fieldset>;
}
