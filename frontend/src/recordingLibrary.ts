import { recordingRequest } from './recordingDrafts';

export interface RecordingFile {
  index: number;
  name: string;
  duration: number | null;
  bytes: number | null;
  availability: 'available' | 'missing' | 'external' | 'damaged' | 'unavailable';
  file_key: string;
  recorded_at?: string;
  interrupted?: boolean;
  recovery_draft_id?: string;
}

export interface RecordingSession {
  session_id: string;
  course_id: string;
  course_name: string;
  course_code: string | null;
  semester_name: string;
  title: string | null;
  session_date: string;
  status: string;
  has_transcript: number;
  files: RecordingFile[];
}

export interface RecoveryDraft {
  id: string;
  course_id: string;
  course_name: string;
  course_code: string | null;
  session_id: string | null;
  session_date: string;
  title: string;
  created_at: string;
  chunk_count: number;
  bytes: number;
  partial_write: boolean;
  linked_file: boolean;
  problem: string | null;
}

export type RecordingCollection = { sessions: RecordingSession[]; drafts: RecoveryDraft[] };
export type SavedRecording = { session_id: string; file: RecordingFile; interrupted: boolean };

export const recordingLibrary = {
  list: (): Promise<RecordingCollection> => recordingRequest('/api/recordings', { cache: 'no-store' }),
  deleteStagedFile: (sessionId: string, file: RecordingFile): Promise<{ file_count: number }> =>
    recordingRequest(recordingLibrary.audioUrl(sessionId, file.index, file.file_key), { method: 'DELETE' }),
  revealDraft: (id: string): Promise<{ ok: boolean }> => recordingRequest(`/api/recording-drafts/${encodeURIComponent(id)}/reveal`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }),
  reveal: (sessionId: string, index: number, fileKey?: string): Promise<{ ok: boolean }> =>
    recordingRequest(`/api/sessions/${encodeURIComponent(sessionId)}/audio/${index}/reveal`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_key: fileKey }),
    }),
  audioUrl: (sessionId: string, index: number, fileKey: string) =>
    `/api/recordings/${encodeURIComponent(sessionId)}/files/${index}?key=${encodeURIComponent(fileKey)}`,
};

export function recordingDuration(seconds: number | null) {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return '时长未知';
  const total = Math.max(1, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const tail = String(total % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${tail}` : `${minutes}:${tail}`;
}

export function recordingSize(bytes: number | null) {
  if (bytes === null) return '';
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;
}

export function recordingLocalTime(timestamp?: string) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
