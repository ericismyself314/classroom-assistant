import {
  Semester, Course, Slot, Session, Job, WeekDay, Item,
} from './types';

const BASE = '';

const REQUEST_TIMEOUT_MS = 30_000;
const UPLOAD_TIMEOUT_MS = 30 * 60 * 1000;
const TIMEOUT_MARKER = '__CLASSROOM_ASSISTANT_TIMEOUT__';

function requestLabel(path: string): string {
  if (path.includes('/process')) return '重新分析';
  if (path.includes('/audio')) return '上传录音';
  if (path.includes('/transcript')) return '导入转写';
  return '请求本地服务';
}

function isNetworkFailure(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return error.name === 'TypeError' || /failed to fetch|networkerror|网络错误|连接被中断/i.test(error.message);
}

function isTimeout(error: unknown): boolean {
  return error instanceof Error && error.message === TIMEOUT_MARKER;
}

async function backendAlive(): Promise<boolean> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 2500);
  try {
    const r = await fetch(`${BASE}/api/health?probe=${Date.now()}`, {
      cache: 'no-store',
      signal: controller.signal,
    });
    return r.ok;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timer);
  }
}

async function connectionError(path: string, timedOut = false): Promise<Error> {
  const label = requestLabel(path);
  const alive = await backendAlive();
  if (!alive) {
    return new Error(`无法连接课堂助手本地服务，应用可能正在重启。${label}没有确认完成，请等待 2 秒后再试。`);
  }
  if (timedOut) {
    return new Error(`${label}等待本地服务响应超时，但服务仍在线。请先刷新课堂详情查看状态，不要连续重复提交。`);
  }
  return new Error(`${label}与本地服务的连接被中断，但服务仍在线。请点击重试；已有转写和课堂数据不会丢失。`);
}

async function fetchWithTimeout(path: string, opts: RequestInit | undefined, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(BASE + path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(TIMEOUT_MARKER);
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

async function req(path: string, opts?: RequestInit) {
  const method = String(opts?.method || 'GET').toUpperCase();
  // GET/HEAD are safe to retry once when the WebView2 socket was interrupted.
  // POST/PUT/DELETE are intentionally not retried to avoid duplicate writes or jobs.
  const attempts = method === 'GET' || method === 'HEAD' ? 2 : 1;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const r = await fetchWithTimeout(path, opts, REQUEST_TIMEOUT_MS);
      if (!r.ok) {
        const t = await r.text();
        let message = t;
        try {
          const parsed = JSON.parse(t);
          message = parsed.error || parsed.message || t;
        } catch { /* response is plain text */ }
        throw new Error(message || r.statusText);
      }
      return r.json();
    } catch (error) {
      if ((isNetworkFailure(error) || isTimeout(error)) && attempt + 1 < attempts) {
        await new Promise((resolve) => window.setTimeout(resolve, 350));
        continue;
      }
      if (isNetworkFailure(error) || isTimeout(error)) {
        throw await connectionError(path, isTimeout(error));
      }
      throw error;
    }
  }
  throw new Error('本地服务请求失败');
}

async function rawUpload(path: string, file: File) {
  return rawUploadWithHeaders(path, file, {});
}

function wireFilename(name: string): string {
  // Fetch headers are ByteString in WebView2; a Chinese filename would make
  // the request fail before any bytes are uploaded. The server only needs the
  // extension to select the decoder, so send an ASCII placeholder filename.
  const match = /\.([A-Za-z0-9]+)$/.exec(name);
  return `upload.${match ? match[1].toLowerCase() : 'm4a'}`;
}

async function rawUploadWithHeaders(path: string, file: File, extra: Record<string, string>) {
  const buf = await file.arrayBuffer();
  try {
    const r = await fetchWithTimeout(path, {
      method: 'POST',
      headers: { 'X-Filename': wireFilename(file.name), 'X-Original-Filename': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream', ...extra },
      body: buf,
    }, UPLOAD_TIMEOUT_MS);
    if (!r.ok) {
      const t = await r.text();
      let message = t;
      try {
        const parsed = JSON.parse(t);
        message = parsed.error || parsed.message || t;
      } catch { /* response is plain text */ }
      throw new Error(message || r.statusText);
    }
    return r.json();
  } catch (error) {
    if (isNetworkFailure(error) || isTimeout(error)) {
      throw await connectionError(path, isTimeout(error));
    }
    throw error;
  }
}

async function rawUploadWithProgress(
  path: string, file: File, extra: Record<string, string>,
  onProgress?: (loaded: number, total: number) => void,
): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const rejectConnection = async (timedOut = false) => {
      if (settled) return;
      settled = true;
      reject(await connectionError(path, timedOut));
    };
    xhr.open('POST', BASE + path, true);
    xhr.timeout = UPLOAD_TIMEOUT_MS;
    xhr.responseType = 'text';
    xhr.setRequestHeader('X-Filename', wireFilename(file.name));
    xhr.setRequestHeader('X-Original-Filename', encodeURIComponent(file.name));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    for (const [k, v] of Object.entries(extra)) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      const body = xhr.responseText || '';
      let message = body;
      try {
        const parsed = JSON.parse(body);
        message = parsed.error || parsed.message || body;
      } catch { /* plain text */ }
      if (settled) return;
      if (xhr.status >= 200 && xhr.status < 300) {
        settled = true;
        try { resolve(JSON.parse(body)); } catch { resolve({ ok: true }); }
      } else {
        settled = true;
        reject(new Error(message || `HTTP ${xhr.status}`));
      }
    };
    xhr.onerror = () => { void rejectConnection(); };
    xhr.ontimeout = () => { void rejectConnection(true); };
    xhr.onabort = () => {
      if (settled) return;
      settled = true;
      reject(new Error('已取消'));
    };
    xhr.send(file);
  });
}

export const api = {
  state: (brief = false) => req('/api/state' + (brief ? '?brief=1' : '')),
  readiness: () => req('/api/readiness'),
  semesters: () => req('/api/semesters'),
  createSemester: (b: any) => req('/api/semesters', { method: 'POST', body: JSON.stringify(b) }),
  activateSemester: (id: string) => req(`/api/semesters/${id}/activate`, { method: 'POST' }),

  courses: (sem?: string) => req('/api/courses' + (sem ? `?semester_id=${sem}` : '')),
  createCourse: (b: any) => req('/api/courses', { method: 'POST', body: JSON.stringify(b) }),
  getCourse: (id: string) => req(`/api/courses/${id}`),
  updateCourse: (id: string, b: any) => req(`/api/courses/${id}`, { method: 'PUT', body: JSON.stringify(b) }),
  deleteCourse: (id: string) => req(`/api/courses/${id}`, { method: 'DELETE' }),
  setSlots: (id: string, slots: Slot[]) => req(`/api/courses/${id}/slots`, { method: 'PUT', body: JSON.stringify({ slots }) }),

  week: (center?: string) => req('/api/week' + (center ? `?center=${center}` : '')),
  createSession: (b: any) => req('/api/sessions', { method: 'POST', body: JSON.stringify(b) }),
  sessions: (cid: string) => req(`/api/courses/${cid}/sessions`),
  sessionDetail: (id: string) => req(`/api/sessions/${id}`),
  updateSession: (id: string, b: any) => req(`/api/sessions/${id}`, { method: 'PUT', body: JSON.stringify(b) }),
  sessionJob: (id: string) => req(`/api/sessions/${id}/job`),
  deleteSession: (id: string) => req(`/api/sessions/${id}`, { method: 'DELETE' }),

  uploadAudio: (id: string, file: File, onProgress?: (loaded: number, total: number) => void,
    extra?: Record<string, string>) =>
    rawUploadWithProgress(`/api/sessions/${id}/audio`, file, extra || {}, onProgress),
  deleteSessionAudio: (id: string) => req(`/api/sessions/${id}/audio`, { method: 'DELETE' }),
  deleteStagedAudioFile: (id: string, index: number) => req(`/api/sessions/${id}/audio/${index}`, { method: 'DELETE' }),
  importTranscript: (id: string, b: any) => req(`/api/sessions/${id}/transcript`, { method: 'POST', body: JSON.stringify(b) }),
  process: (id: string, b: any) => req(`/api/sessions/${id}/process`, { method: 'POST', body: JSON.stringify(b) }),

  job: (id: string) => req(`/api/jobs/${id}`),
  cancelJob: (id: string) => req(`/api/jobs/${id}/cancel`, { method: 'POST' }),

  important: (q: Record<string, string>) => req('/api/important?' + new URLSearchParams(q)),
  search: (q: Record<string, string>) => req('/api/search?' + new URLSearchParams(q)),

  settings: () => req('/api/settings'),
  saveSettings: (b: any) => req('/api/settings', { method: 'PUT', body: JSON.stringify(b) }),
  testWhisper: () => req('/api/settings/test-whisper', { method: 'POST' }),
  testAnalysis: () => req('/api/settings/test-analysis', { method: 'POST' }),
  modelStatus: () => req('/api/models/status'),
  downloadWhisper: (size: string) => req('/api/models/whisper/download', { method: 'POST', body: JSON.stringify({ size }) }),
  validateWhisper: (sizes?: string[]) => req('/api/models/whisper/validate', { method: 'POST', body: JSON.stringify({ sizes: sizes || [] }) }),
  cleanupWhisper: (size: string) => req('/api/models/whisper/cleanup', { method: 'POST', body: JSON.stringify({ size }) }),
  downloadLlm: () => req('/api/models/llm/download', { method: 'POST' }),
  deleteLlm: () => req('/api/models/llm/delete', { method: 'POST' }),
  importModel: (file: File, type: string, modelId: string) =>
    rawUploadWithHeaders('/api/models/import', file, { 'X-Model-Type': type, 'X-Model-Id': modelId }),
  backup: () => req('/api/backup'),
  restore: (file: File) => rawUpload('/api/restore', file),
  updateItem: (id: string, b: any) => req(`/api/items/${id}`, { method: 'PUT', body: JSON.stringify(b) }),
  deleteItem: (id: string) => req(`/api/items/${id}`, { method: 'DELETE' }),
  updateSegment: (id: string, text: string, expected_text: string) => req(`/api/segments/${id}`, { method: 'PUT', body: JSON.stringify({ text, expected_text }) }),
  updateChapter: (id: string, index: number, body: any) => req(`/api/sessions/${id}/notes/chapters/${index}`, { method: 'PUT', body: JSON.stringify(body) }),
  openDataFolder: () => req('/api/system/open-data-folder', { method: 'POST' }),
};

export type { Semester, Course, Slot, Session, Job, WeekDay, Item };
