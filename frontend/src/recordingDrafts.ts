import type { SavedRecording } from './recordingLibrary';

export type RecordingDraft = {
  id: string; course_id: string; session_id: string | null; session_date: string;
  title: string; mime_type: string; status: string; active: boolean; chunk_count: number; bytes: number; created_at: string;
};
export function recoverableDrafts(drafts: RecordingDraft[]): RecordingDraft[] {
  return drafts.filter((draft) => draft.status !== 'saved' && draft.chunk_count > 0 && !draft.active);
}
async function request(path: string, init?: RequestInit, timeoutMs = 15_000): Promise<any> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { ...init, signal: controller.signal });
    if (!response.ok) {
      let message = `本地服务错误 (${response.status})`;
      try { message = (await response.json()).error || message; } catch { /* keep status */ }
      throw new Error(message);
    }
    return await response.json();
  } catch (error) {
    if (controller.signal.aborted) throw new Error('本地服务响应超时，请重试');
    throw error;
  } finally { window.clearTimeout(timer); }
}
export { request as recordingRequest };
export const recordingDrafts = {
  list: (): Promise<{ drafts: RecordingDraft[] }> => request('/api/recording-drafts'),
  create: (data: { course_id: string; session_id: string | null; session_date: string; title: string; mime_type: string }): Promise<RecordingDraft> =>
    request('/api/recording-drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  write: (id: string, index: number, blob: Blob): Promise<unknown> =>
    request(`/api/recording-drafts/${encodeURIComponent(id)}/chunks/${index}`, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: blob }),
  heartbeat: (id: string): Promise<unknown> => request(`/api/recording-drafts/${encodeURIComponent(id)}/heartbeat`, { method: 'POST' }),
  finalize: (id: string, interrupted: boolean, captureInterrupted = false): Promise<SavedRecording> =>
    request(`/api/recording-drafts/${encodeURIComponent(id)}/finalize`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ interrupted, capture_interrupted: captureInterrupted }) }, 30 * 60_000),
};
