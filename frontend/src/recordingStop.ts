/** Track why capture stopped separately from whether saving later succeeds. */
export class RecordingStop {
  private userStopped = false;
  private reason = '';
  stopByUser(): void { this.userStopped = true; }
  isUserStopped(): boolean { return this.userStopped; }
  interrupt(reason: string): string {
    if (!this.reason) this.reason = reason;
    return this.reason;
  }
  stopped(): string {
    if (!this.userStopped && !this.reason) this.reason = '录音设备意外停止';
    return this.reason;
  }
}
