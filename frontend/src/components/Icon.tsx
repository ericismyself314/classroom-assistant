import { SVGProps } from 'react';

export type IconName = 'calendar' | 'mic' | 'book' | 'check' | 'search' | 'settings' | 'chevron' | 'plus' | 'clock' | 'pin' | 'play' | 'pause' | 'stop' | 'volume' | 'upload' | 'download' | 'arrow' | 'file';

const paths: Record<IconName, JSX.Element> = {
  calendar: <><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M7 3v4m10-4v4M3 10h18" /></>,
  mic: <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-3 0h6" /></>,
  book: <><path d="M12 5C9 3 5 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-2-1-6-1-9 1Zm0 0v15" /></>,
  check: <><rect x="3" y="3" width="18" height="18" rx="4" /><path d="m7 12 3 3 7-7" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="m9 3-.6 2.2-2 .9L4 5.5 2 9l1.8 1.5v3L2 15l2 3.5 2.4-.6 2 .9L9 21h6l.6-2.2 2-.9 2.4.6 2-3.5-1.8-1.5v-3L22 9l-2-3.5-2.4.6-2-.9L15 3Z" /></>,
  chevron: <path d="m9 5 7 7-7 7" />,
  plus: <path d="M12 4v16M4 12h16" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  pin: <><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z" /><circle cx="12" cy="10" r="2" /></>,
  play: <path d="m9 5 11 7-11 7Z" fill="currentColor" stroke="none" />,
  pause: <path d="M8 5v14m8-14v14" strokeWidth="3.5" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />,
  volume: <><path d="M11 4 6 8H3v8h3l5 4Zm4 4a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></>,
  upload: <path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" />,
  download: <path d="M12 3v13m-5-5 5 5 5-5M4 16v4h16v-4" />,
  arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  file: <path d="M14 3H5v18h14V8Zm0 0v5h5M8 12h8m-8 4h6" />,
};

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
