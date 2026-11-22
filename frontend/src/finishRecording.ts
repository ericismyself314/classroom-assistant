/** Once finalization succeeds, a UI refresh failure must not undo the saved result. */
export async function finishRecording(
  flush: () => Promise<void>,
  finalize: () => Promise<{ session_id: string }>,
  refresh: (sessionId: string) => Promise<void>,
): Promise<{ sessionId: string; refreshFailed: boolean }> {
  await flush();
  const saved = await finalize();
  try {
    await refresh(saved.session_id);
    return { sessionId: saved.session_id, refreshFailed: false };
  } catch {
    return { sessionId: saved.session_id, refreshFailed: true };
  }
}
