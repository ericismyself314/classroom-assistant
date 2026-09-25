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
import logoUrl from './assets/classroom-assistant-icon.svg';

export type View =
  | { name: 'week' }
  | { name: 'courses'; courseId?: string }
  | { name: 'session'; id: string }
  | { name: 'audio'; courseId?: string; sessionId?: string; date?: string; title?: string; source?: 'microphone' | 'upload' }
  | { name: 'important' }
  | { name: 'search' }
  | { name: 'settings' };

const NAV = [
  { key: 'week', label: '本周', icon: '📅' },
  { key: 'audio', label: '录音与导入', icon: '🎙️' },
  { key: 'courses', label: '课程', icon: '📚' },
  { key: 'important', label: '重要事项', icon: '🔔' },
  { key: 'search', label: '搜索', icon: '🔍' },
  { key: 'settings', label: '设置', icon: '⚙️' },
] as const;

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

  const activeKey = view.name === 'courses' || view.name === 'session' ? 'courses' : view.name;

  return (
    <div className="app">
      <div className="sidebar">
        <div className="brand">
          <img src={logoUrl} alt="" aria-hidden="true" />
          <span>课堂助手</span>
        </div>
        {NAV.map((n) => (
          <div
            key={n.key}
            className={`nav-item ${activeKey === n.key ? 'active' : ''}`}
            onClick={() => navigate({ name: n.key } as View)}
          >
            <span>{n.icon}</span><span>{n.label}</span>
          </div>
        ))}
        <div className="spacer" />
        <div className="foot">本地运行 · 数据留在你的电脑</div>
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
