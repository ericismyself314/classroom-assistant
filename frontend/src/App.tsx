import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from './api';
import { Onboarding } from './pages/Onboarding';
import { ThisWeek } from './pages/ThisWeek';
import { Courses } from './pages/Courses';
import { SessionDetail } from './pages/SessionDetail';
import { ImportantItems } from './pages/ImportantItems';
import type { ItemGroup } from './itemPriorities';
import { Search, type SearchSource } from './pages/Search';
import { Settings } from './pages/Settings';
import { AudioWorkspace } from './pages/AudioWorkspace';
import { Recordings } from './pages/Recordings';
import { recordingLibrary } from './recordingLibrary';
import { GlobalJobStatus } from './components/GlobalJobStatus';
import { SelectionIndicator } from './components/SelectionIndicator';
const logoUrl = `${import.meta.env.BASE_URL}classroom-assistant-icon.png`;

export type View =
  | { name: 'week' }
  | { name: 'courses'; courseId?: string }
  | { name: 'session'; id: string; fromSearch?: boolean; fromImportant?: ItemGroup; source?: SearchSource }
  | { name: 'audio'; courseId?: string; sessionId?: string; date?: string; title?: string; source?: 'microphone' | 'upload' }
  | { name: 'recordings'; courseId?: string; sessionId?: string; fileIndex?: number; fileKey?: string; filter?: 'all' | 'pending' | 'complete' | 'recovery' }
  | { name: 'important'; group?: ItemGroup }
  | { name: 'search' }
  | { name: 'settings' };

const NAV = [
  { key: 'week', label: '课程总览', icon: 'calendar' },
  { key: 'audio', label: '录音与转写', icon: 'microphone' },
  { key: 'recordings', label: '我的录音', icon: 'recordings' },
  { key: 'important', label: '重要事项', icon: 'bookmark' },
  { key: 'courses', label: '我的课程', icon: 'book' },
  { key: 'search', label: '搜索笔记', icon: 'search' },
  { key: 'settings', label: '设置', icon: 'settings' },
] as const;

function NavigationIcon({ name }: { name: (typeof NAV)[number]['icon'] }) {
  const shared = {
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.7,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true" {...shared}>
      {name === 'calendar' && <><rect x="3.5" y="5" width="17" height="16" rx="2" /><path d="M7.5 3v4M16.5 3v4M3.5 9.5h17" /></>}
      {name === 'microphone' && <><rect x="9" y="2.5" width="6" height="12" rx="3" /><path d="M5.5 11v1a6.5 6.5 0 0 0 13 0v-1M12 18.5V22M9 22h6" /></>}
      {name === 'recordings' && <><rect x="3" y="4.5" width="18" height="15" rx="3" /><path d="M6.5 11v2m3-5v8m3-6v4m3-7v10m3-6v2" /></>}
      {name === 'book' && <><path d="M3.5 5.5A2.5 2.5 0 0 1 6 3h5v17H6a2.5 2.5 0 0 0-2.5 2zM20.5 5.5A2.5 2.5 0 0 0 18 3h-5v17h5a2.5 2.5 0 0 1 2.5 2z" /></>}
      {name === 'bookmark' && <path d="M6 3.5h12v17l-6-4-6 4z" />}
      {name === 'search' && <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 4.5 4.5" /></>}
      {name === 'settings' && <><circle cx="12" cy="12" r="3" /><path d="M19.4 13.5a7.8 7.8 0 0 0 0-3l2-1.5-2-3.4-2.4 1a8 8 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.6A8 8 0 0 0 7 6.6l-2.4-1-2 3.4 2 1.5a7.8 7.8 0 0 0 0 3l-2 1.5 2 3.4 2.4-1a8 8 0 0 0 2.6 1.5l.4 2.6h4l.4-2.6a8 8 0 0 0 2.6-1.5l2.4 1 2-3.4z" /></>}
    </svg>
  );
}

export default function App() {
  const [view, setView] = useState<View>({ name: 'week' });
  const [hasSemester, setHasSemester] = useState<boolean | null>(null);
  const [startupError, setStartupError] = useState('');
  const [startupAttempt, setStartupAttempt] = useState(0);
  const [section, setSection] = useState<Exclude<View['name'], 'session'>>('week');
  const [audioView, setAudioView] = useState<Extract<View, { name: 'audio' }> | null>(null);
  const [recordingActive, setRecordingActive] = useState(false);
  const [recordingNotice, setRecordingNotice] = useState('');
  const [recoverableCount, setRecoverableCount] = useState(0);
  const [recoveryDismissed, setRecoveryDismissed] = useState(false);
  const [taskRevision, setTaskRevision] = useState(0);
  const pageBody = useRef<HTMLDivElement>(null);
  const pageId = view.name === 'session' ? 'session/' + view.id : view.name;
  const previousPage = useRef(pageId);

  useLayoutEffect(() => {
    if (previousPage.current === pageId) return;
    previousPage.current = pageId;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    // Keep the recorder and its stream mounted throughout navigation.
    const fade = pageBody.current?.animate([{ opacity: 0.4 }, { opacity: 1 }], {
      duration: 185, easing: 'cubic-bezier(.2,.65,.3,1)',
    });
    return () => fade?.cancel();
  }, [pageId]);

  useEffect(() => {
    let current = true;
    setStartupError('');
    api.state(true).then(s => { if (current) setHasSemester(s.has_semester); })
      .catch(error => { if (current) setStartupError(error.message || '暂时无法读取本机课堂资料。'); });
    return () => { current = false; };
  }, [startupAttempt]);

  const navigate = (v: View) => {
    if (v.name === 'audio' && (!recordingActive || !audioView)) setAudioView(v);
    if (v.name !== 'session') setSection(v.name);
    setView(v);
  };

  useEffect(() => {
    if (view.name !== 'week' || recordingActive || recoveryDismissed) return;
    let current = true;
    recordingLibrary.list().then(({ drafts }) => {
      if (current) setRecoverableCount(drafts.filter(d => d.chunk_count > 0 && !d.problem).length);
    }).catch(() => { if (current) setRecoverableCount(0); });
    return () => { current = false; };
  }, [view.name, recordingActive, recoveryDismissed]);

  useEffect(() => {
    if (!recordingActive) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [recordingActive]);

  if (hasSemester === null) {
    return <div className="empty">{startupError ? <><p role="alert">{startupError}</p>
      <button type="button" onClick={() => setStartupAttempt(n => n + 1)}>重新读取课堂资料</button></> : '正在加载…'}</div>;
  }
  if (!hasSemester) {
    return <Onboarding onDone={() => { setHasSemester(true); setView({ name: 'week' }); }} />;
  }

  const activeKey = view.name === 'session' ? section : view.name;

  return (
    <div className="app">
      <div className="sidebar selection-group" role="navigation" aria-label="主导航">
        <div className="brand">
          <img src={logoUrl} alt="" aria-hidden="true" />
          <span>课堂助手</span>
        </div>
        <SelectionIndicator value={activeKey} continuous />
        {NAV.filter(n => n.key !== 'settings').map((n) => (
          <button
            key={n.key}
            type="button"
            className={`nav-item ${activeKey === n.key ? 'active' : ''}`}
            aria-current={activeKey === n.key ? 'page' : undefined}
            onClick={() => navigate({ name: n.key } as View)}
          >
            <NavigationIcon name={n.icon} /><span>{n.label}{n.key === 'audio' && recordingActive ? ' · 录音未完成' : ''}</span>
          </button>
        ))}
        <div className="spacer" />
        <button type="button" className={`nav-item ${activeKey === 'settings' ? 'active' : ''}`} aria-current={activeKey === 'settings' ? 'page' : undefined} onClick={() => navigate({ name: 'settings' })}><NavigationIcon name="settings" /><span>设置</span></button>
      </div>
      <div className="content">
        <GlobalJobStatus navigate={navigate} onTerminal={() => setTaskRevision(value => value + 1)} currentSessionId={view.name === 'session' ? view.id : undefined} />
        {recordingNotice && <div className="quality-warning" role="alert">{recordingNotice} <button type="button" onClick={() => setRecordingNotice('')}>关闭提示</button></div>}
        {view.name === 'week' && !recordingActive && !recoveryDismissed && recoverableCount > 0 && <div className="quality-warning" role="status">
          有 {recoverableCount} 段未完成录音，可尝试恢复。 <button type="button" onClick={() => navigate({ name: 'recordings', filter: 'recovery' })}>查看待恢复录音</button> <button type="button" onClick={() => setRecoveryDismissed(true)}>关闭提示</button>
        </div>}
        <div ref={pageBody} className="page-body">
          {view.name === 'week' && <ThisWeek navigate={navigate} refreshToken={taskRevision} />}
          {audioView && <div hidden={view.name !== 'audio'}><AudioWorkspace key={`${audioView.courseId || ''}:${audioView.sessionId || ''}:${audioView.date || ''}:${audioView.source || 'microphone'}`} navigate={navigate}
            courseId={audioView.courseId} sessionId={audioView.sessionId} date={audioView.date} title={audioView.title} source={audioView.source} visible={view.name === 'audio'}
            onRecordingChange={setRecordingActive} onRecordingNotice={setRecordingNotice} onRecordingSaved={() => setTaskRevision(n => n + 1)} /></div>}
          {view.name === 'recordings' && <Recordings key={JSON.stringify(view)} {...view} navigate={navigate} refreshToken={taskRevision} />}
          {view.name === 'courses' && <Courses navigate={navigate} courseId={view.courseId} />}
          {view.name === 'session' && <SessionDetail key={view.id} sid={view.id} navigate={navigate} fromSearch={view.fromSearch} fromImportant={view.fromImportant} source={view.source} />}
          {view.name === 'important' && <ImportantItems key={view.group || 'overview'} navigate={navigate} group={view.group} revision={taskRevision} />}
          {(view.name === 'search' || (view.name === 'session' && view.fromSearch)) && <div hidden={view.name !== 'search'}><Search navigate={navigate} /></div>}
          {view.name === 'settings' && <Settings navigate={navigate} />}
        </div>
      </div>
    </div>
  );
}
