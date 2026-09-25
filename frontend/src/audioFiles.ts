export const MAX_AUDIO_FILES = 64;
export const MAX_AUDIO_BYTES = 2 * 1024 * 1024 * 1024;

const AUDIO_EXTENSIONS = new Set(['mp3', 'm4a', 'wav', 'aac', 'flac', 'ogg', 'webm', 'mp4', 'mov']);

function fileKey(file: File): string {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
}

/** Merge a new picker selection into the current list without losing earlier files. */
export function mergeAudioFiles(current: File[], incoming: File[]): {
  files: File[];
  duplicateCount: number;
  truncated: boolean;
  unsupportedCount: number;
  oversizedCount: number;
} {
  const files = [...current];
  const seen = new Set(files.map(fileKey));
  let duplicateCount = 0;
  let truncated = false;
  let unsupportedCount = 0;
  let oversizedCount = 0;
  for (const file of incoming) {
    const extension = file.name.split('.').pop()?.toLowerCase() || '';
    if (!AUDIO_EXTENSIONS.has(extension)) {
      unsupportedCount += 1;
      continue;
    }
    if (file.size > MAX_AUDIO_BYTES) {
      oversizedCount += 1;
      continue;
    }
    const key = fileKey(file);
    if (seen.has(key)) {
      duplicateCount += 1;
      continue;
    }
    if (files.length >= MAX_AUDIO_FILES) {
      truncated = true;
      break;
    }
    seen.add(key);
    files.push(file);
  }
  return { files, duplicateCount, truncated, unsupportedCount, oversizedCount };
}

export function totalAudioBytes(files: File[]): number {
  return files.reduce((sum, file) => sum + file.size, 0);
}
