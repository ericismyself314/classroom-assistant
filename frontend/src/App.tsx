import { useEffect, useState } from 'react';
import { api } from './api';
import { Onboarding } from './pages/Onboarding';
import { ThisWeek } from './pages/ThisWeek';
import { Courses } from './pages/Courses';
import { SessionDetail } from './pages/SessionDetail';
import { ImportantItems } from './pages/ImportantItems';
import { Search } from './pages/Search';
import { Settings } from './pages/Settings';
import { AudioWorkspace } from './pages/AudioWorkspace';
const logoUrl = `${import.meta.env.BASE_URL}classroom-assistant-icon.png`;

export type View =
  | { name: 'week' }
  | { name: 'courses'; courseId?: string }
  | { name: 'session'; id: string }
  | { name: 'audio'; courseId?: string; sessionId?: string; date?: string; title?: string; source?: 'microphone' | 'upload' }
  | { name: 'important' }
  | { name: 'search' }
  | { name: 'settings' };

const NAV = [
  { key: 'week', label: '课程总览', icon: 'calendar' },
  { key: 'audio', label: '录音与转写', icon: 'microphone' },
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

  useEffect(() => {
    api.state(true).then((s) => setHasSemester(s.has_semester)).catch(() => setHasSemester(false));
  }, []);

  const navigate = (v: View) => setView(v);

  if (hasSemester === null) {
    return <div className="empty">正在加载…</div>;
  }
  if (!hasSemester) {
    return <Onboarding onDone={() => { setHasSemester(true); setView({ name: 'week' }); }} />;
  }

  const activeKey = view.name === 'session' ? 'audio' : view.name;

  return (
    <div className="app">
      <div className="sidebar">
        <div className="brand">
          <img src={logoUrl} alt="" aria-hidden="true" />
          <span>课堂助手</span>
        </div>
        {NAV.filter(n => n.key !== 'settings').map((n) => (
          <button
            key={n.key}
            type="button"
            className={`nav-item ${activeKey === n.key ? 'active' : ''}`}
            aria-current={activeKey === n.key ? 'page' : undefined}
            onClick={() => navigate({ name: n.key } as View)}
          >
            <NavigationIcon name={n.icon} /><span>{n.label}</span>
          </button>
        ))}
        <div className="spacer" />
        <button type="button" className={`nav-item ${activeKey === 'settings' ? 'active' : ''}`} aria-current={activeKey === 'settings' ? 'page' : undefined} onClick={() => navigate({ name: 'settings' })}><NavigationIcon name="settings" /><span>设置</span></button>
      </div>
      <div className="content">
        {view.name === 'week' && <ThisWeek navigate={navigate} />}
        {view.name === 'audio' && <AudioWorkspace key={`${view.courseId || ''}:${view.sessionId || ''}:${view.source || 'microphone'}`} navigate={navigate}
          courseId={view.courseId} sessionId={view.sessionId} date={view.date} title={view.title} source={view.source} />}
        {view.name === 'courses' && <Courses navigate={navigate} courseId={view.courseId} />}
        {view.name === 'session' && <SessionDetail sid={view.id} navigate={navigate} />}
        {view.name === 'important' && <ImportantItems navigate={navigate} />}
        {view.name === 'search' && <Search navigate={navigate} />}
        {view.name === 'settings' && <Settings navigate={navigate} />}
      </div>
    </div>
  );
}
