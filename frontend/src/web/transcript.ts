export function timestamp(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  const secondsPart = String(value % 60).padStart(2, "0");
  if (value < 3600)
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${secondsPart}`;
  return `${String(Math.floor(value / 3600)).padStart(2, "0")}:${String(Math.floor(value / 60) % 60).padStart(2, "0")}:${secondsPart}`;
}
export function timeline(
  text: string,
): { time: number | null; text: string; label: string }[] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => {
      const match = /^\s*\[(?:(\d+):)?(\d+):([0-5]\d)\]\s*(.*)$/.exec(line);
      if (!match) return { time: null, text: line, label: "" };
      const seconds =
        Number(match[1] || 0) * 3600 + Number(match[2]) * 60 + Number(match[3]);
      return { time: seconds, label: timestamp(seconds), text: match[4] };
    });
}
export function extractNotes(text: string): string {
  const lines = timeline(text);
  const important = lines.filter((line) =>
    /作业|作業|考试|考試|截止|提交|assignment|homework|submit|quiz|exam|deadline|due\b/i.test(
      line.text,
    ),
  );
  return `课堂原文摘录（规则提取，非 AI 总结）\n\n${lines
    .slice(0, 8)
    .map((line) => `• ${line.label ? `[${line.label}] ` : ""}${line.text}`)
    .join(
      "\n",
    )}\n\n待核对事项（保留原话，不推断日期）\n${important.length ? important.map((line) => `• ${line.label ? `[${line.label}] ` : ""}${line.text}`).join("\n") : "未匹配到作业或考试关键词，请阅读完整文字稿。"}`;
}
