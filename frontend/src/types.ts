export interface Semester {
  id: string; name: string; start_date: string; end_date: string;
  timezone: string; is_active: number;
}
export interface Course {
  id: string; semester_id: string; name: string; code: string | null;
  teacher_name: string | null; location: string | null; language_mode: string;
  color: string; is_archived: number; vocabulary?: string;
}
export interface Slot {
  id?: string; weekday: number; start_time: string; end_time: string;
  location?: string | null; recurrence_start?: string | null; recurrence_end?: string | null;
}
export interface Session {
  id: string; course_id: string; session_date: string; start_time: string | null;
  end_time: string | null; week_number: number | null; title: string | null;
  location: string | null; status: string; source_type: string | null;
  audio_path: string | null; audio_hash: string | null; audio_duration_seconds: number | null;
  transcript_language: string | null; transcription_model: string | null;
  analysis_model: string | null; processing_error: string | null;
}
export interface Segment {
  id: string; session_id: string; sequence_number: number;
  start_seconds: number | null; end_seconds: number | null; text: string;
  display_text: string | null;
  original_text: string | null; language: string | null; confidence: number | null;
  is_uncertain: number;
}
export interface Topic {
  id: string; session_id: string; sequence_number: number; title: string;
  summary: string | null; start_seconds: number | null; end_seconds: number | null;
  evidence_segment_ids: string | null;
}
export interface Item {
  id: string; session_id: string; course_id: string; category: string;
  title: string; details: string | null; raw_date_text: string | null;
  normalized_date: string | null; action_required: number; confidence: string;
  evidence_data: string | null; is_confirmed: number; is_completed: number;
  session_date: string | null;
  verification_status?: string;
}
export interface SlotOcc {
  course_id: string; course_name: string; color: string; language_mode: string;
  weekday: number; start_time: string; end_time: string; location: string | null;
  session: Session | null; week_number: number;
}
export interface WeekDay {
  date: string; weekday: number; weekday_cn: string; is_today: boolean; slots: SlotOcc[];
}
export interface Job {
  id: string; session_id: string; job_type: string; status: string;
  stage: string | null; progress_data: string | null; attempt_count: number;
  error_message: string | null;
}
export const CATEGORY_LABELS: Record<string, string> = {
  exam: '考试', assignment: '作业', study_scope: '学习范围',
  schedule_change: '安排变化', activity: '活动', other: '其它',
};
export const CONFIDENCE_LABELS: Record<string, string> = {
  explicit: '明确通知', probable: '较明确', uncertain: '待确认',
};
