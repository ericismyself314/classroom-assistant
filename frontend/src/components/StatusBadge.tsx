import { Session } from '../types';

const LABELS: Record<string, [string, string]> = {
  waiting_upload: ['等待上传', 'gray'],
  file_check: ['文件检查', 'blue'],
  waiting_model: ['等待模型', 'blue'],
  transcribing: ['正在转写', 'blue'],
  analyzing: ['正在分析', 'blue'],
  indexing: ['建立索引', 'blue'],
  completed: ['已完成', 'green'],
  needs_analysis: ['文字已修正 · 待分析', 'blue'],
  failed: ['处理失败', 'red'],
  cancelled: ['已取消', 'gray'],
};

export function StatusBadge({ status }: { status: string }) {
  const [label, cls] = LABELS[status] || [status, 'gray'];
  return <span className={`badge ${cls}`}>{label}</span>;
}

export function jobStage(job: Session | any): string {
  return '';
}
