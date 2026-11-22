/** Read the media timeline of acknowledged chunks without replaying or retaining a whole lecture. */
export function savedAudioClock(mimeType: string, report: (seconds: number) => void) {
  if (typeof MediaSource === 'undefined' || !MediaSource.isTypeSupported(mimeType)) return null;

  const media = new MediaSource();
  const reader = document.createElement('audio');
  const url = URL.createObjectURL(media);
  const pending: Blob[] = [];
  let buffer: SourceBuffer | undefined;
  let pendingBytes = 0;
  let reading = false;
  let closed = false;
  let lastTrim = 0;

  function close() {
    if (closed) return;
    closed = true;
    pending.length = 0;
    buffer = undefined;
    reader.removeAttribute('src');
    reader.load();
    URL.revokeObjectURL(url);
  }
  function next() {
    if (!buffer || buffer.updating || reading || !pending.length || closed) return;
    const chunk = pending.shift()!;
    reading = true;
    void chunk.arrayBuffer().then(bytes => {
      if (!buffer || closed) return;
      pendingBytes -= chunk.size;
      reading = false;
      buffer.appendBuffer(bytes);
    }).catch(close);
  }
  media.addEventListener('sourceopen', () => {
    if (closed || buffer) return;
    try {
      buffer = media.addSourceBuffer(mimeType);
      buffer.addEventListener('error', close);
      buffer.addEventListener('updateend', () => {
        if (!buffer || closed) return;
        try {
          const ranges = buffer.buffered;
          if (ranges.length) {
            const end = ranges.end(ranges.length - 1);
            report(end);
            if (end - lastTrim >= 30) {
              lastTrim = end;
              buffer.remove(0, end - 5);
              return;
            }
          }
          next();
        } catch { close(); }
      });
      next();
    } catch { close(); }
  });
  reader.addEventListener('error', close);
  reader.muted = true;
  reader.preload = 'auto';
  reader.src = url;
  return {
    append(chunk: Blob) {
      if (closed) return;
      // Timing is optional. Disk writes keep moving even if the media reader stalls.
      pendingBytes += chunk.size;
      if (pendingBytes > 2 * 1024 * 1024) { close(); return; }
      pending.push(chunk);
      next();
    },
    close,
  };
}
