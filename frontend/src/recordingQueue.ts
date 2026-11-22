/** Keeps unacknowledged audio in order; a retry repeats the same sequence number. */
export class RecordingQueue {
  private pending: { index: number; blob: Blob }[] = [];
  private nextIndex = 0;
  private draining: Promise<void> | null = null;
  constructor(private write: (index: number, blob: Blob) => Promise<void>) {}
  add(blob: Blob): void {
    if (blob.size) this.pending.push({ index: this.nextIndex++, blob });
  }
  flush(): Promise<void> {
    if (this.draining) return this.draining;
    const drain = async () => {
      while (this.pending.length) {
        const item = this.pending[0];
        await this.write(item.index, item.blob);
        this.pending.shift();
      }
    };
    this.draining = drain().finally(() => { this.draining = null; });
    return this.draining;
  }
}
