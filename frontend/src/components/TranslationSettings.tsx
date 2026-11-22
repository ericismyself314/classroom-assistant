import { useRef, useState } from 'react';
import { api } from '../api';

type Values = Record<string, string>;
type Feedback = { text: string; error?: boolean };

export function TranslationSettings({ initial, disabled }: { initial: Values; disabled: boolean }) {
  const [saved, setSaved] = useState(initial);
  const [mode, setMode] = useState(initial.translation_provider === 'api' ? 'api' : 'local');
  const [address, setAddress] = useState(initial.translation_base_url || '');
  const [model, setModel] = useState(initial.translation_model || '');
  const [key, setKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const revision = useRef(0);
  const locked = saving || disabled;
  const activeAPI = saved.translation_provider === 'api';
  const hasSavedKey = saved.translation_api_key_set === '1';
  const sameService = address.trim() === saved.translation_base_url && model.trim() === saved.translation_model;
  const canConnect = !!(address.trim() && model.trim() && (key.trim() || (hasSavedKey && sameService)));

  const edit = () => {
    revision.current += 1;
    setFeedback(null);
  };
  const connection = () => ({
    translation_base_url: address.trim(), translation_model: model.trim(),
    ...(key.trim() ? { translation_api_key: key.trim() } : {}),
  });
  const persist = async (payload: Record<string, string | boolean>, success: string) => {
    setSaving(true);
    try {
      const result = await api.saveSettings(payload);
      const next = result.translation as Values;
      setSaved(next);
      setMode(next.translation_provider);
      setAddress(next.translation_base_url);
      setModel(next.translation_model);
      setKey('');
      setFeedback({ text: success });
    } catch (error) {
      setFeedback({ text: error instanceof Error ? error.message : '保存失败，请重试。', error: true });
    } finally { setSaving(false); }
  };
  const chooseLocal = () => {
    edit();
    setKey('');
    if (activeAPI) {
      void persist({ translation_provider: 'local' }, '已改用本机翻译。');
    } else {
      setMode('local');
      setAddress(saved.translation_base_url || '');
      setModel(saved.translation_model || '');
    }
  };
  const test = async () => {
    const testedRevision = revision.current;
    setFeedback(null);
    setTesting(true);
    try {
      await api.testTranslation(connection());
      if (testedRevision === revision.current) setFeedback({ text: '连接成功，样例已翻成中文。' });
    } catch (error) {
      if (testedRevision === revision.current) setFeedback({
        text: error instanceof Error ? error.message : '连接失败，请检查地址和密钥。', error: true,
      });
    } finally { setTesting(false); }
  };
  const clear = () => {
    if (!window.confirm(activeAPI ? '清除已保存的翻译密钥，并改用本机翻译？' : '清除已保存的翻译密钥？')) return;
    edit();
    void persist({ translation_provider: 'local', translation_clear_key: true }, '翻译密钥已清除，当前使用本机翻译。');
  };

  return (
    <section className="card settings-ready-card" aria-labelledby="translation-heading">
      <div className="model-card-head">
        <h3 id="translation-heading">转写翻译</h3>
        <span className="badge">{activeAPI ? '当前使用 API' : '当前使用本机'}</span>
      </div>
      <div className="segmented model-actions" role="group" aria-label="翻译方式">
        <button type="button" aria-pressed={mode === 'local'} className={mode === 'local' ? 'active' : ''} disabled={locked} onClick={chooseLocal}>本机翻译</button>
        <button type="button" aria-pressed={mode === 'api'} className={mode === 'api' ? 'active' : ''} disabled={locked} onClick={() => { edit(); setMode('api'); }}>兼容 API</button>
      </div>
      {mode === 'local' ? (
        <p className="muted model-actions">英文和中英混合的转写会在这台电脑上翻成中文。</p>
      ) : (
        <>
          <p className="muted model-actions">启用后，待翻译的转写文字会发给下方服务。录音仍在本机识别，课堂笔记沿用原来的整理方式。</p>
          <div className="grid cols-2">
            <div>
              <label htmlFor="translation-address">API 基础地址</label>
              <input id="translation-address" type="url" autoComplete="off" spellCheck={false} disabled={locked}
                placeholder="https://…/v1" value={address} onChange={event => { edit(); setAddress(event.target.value); setKey(''); }} />
            </div>
            <div>
              <label htmlFor="translation-model">模型名称</label>
              <input id="translation-model" autoComplete="off" spellCheck={false} disabled={locked}
                placeholder="填写服务商给出的模型名称" value={model} onChange={event => { edit(); setModel(event.target.value); setKey(''); }} />
            </div>
          </div>
          <label htmlFor="translation-key">API 密钥</label>
          <input id="translation-key" type="password" autoComplete="new-password" spellCheck={false} disabled={locked}
            placeholder={hasSavedKey && sameService ? '已加密保存，留空可继续使用' : '粘贴此地址和模型的密钥'}
            value={key} onChange={event => { edit(); setKey(event.target.value); }} />
          <p className="small muted">点击测试只会发送样例 “Hello.”。密钥保存在本机 Windows 账户中。</p>
          <div className="row wrap model-actions">
            <button className="primary" disabled={locked || testing || !canConnect || (activeAPI && sameService && !key)}
              onClick={() => { edit(); void persist({ translation_provider: 'api', ...connection() },
                '已保存，下次翻译将使用所选 API。'); }}>
              {saving ? '保存中…' : activeAPI ? '保存 API 设置' : '保存并启用 API 翻译'}
            </button>
            <button disabled={locked || testing || !canConnect} onClick={test}>{testing ? '正在测试…' : '测试连接'}</button>
          </div>
        </>
      )}
      {(hasSavedKey || saved.translation_api_key_stored === '1') && <details className="advanced">
        <summary>已保存的密钥</summary>
        <button className="danger model-actions" disabled={locked} onClick={clear}>
          {activeAPI ? '清除密钥并改用本机' : '清除翻译密钥'}
        </button>
      </details>}
      {feedback && <div className={`hint ${feedback.error ? 'settings-error' : ''}`} role={feedback.error ? 'alert' : 'status'}>{feedback.text}</div>}
    </section>
  );
}
