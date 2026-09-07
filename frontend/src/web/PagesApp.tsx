import { useEffect, useRef, useState } from "react";
import {
  deleteLesson,
  download,
  exportLessons,
  importLessons,
  Lesson,
  listLessons,
  newLesson,
  saveLesson,
} from "./storage";
import { extractNotes, timeline, timestamp } from "./transcript";
import "./web.css";

export default function PagesApp() {
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [lesson, setLesson] = useState<Lesson>(newLesson);
  const [query, setQuery] = useState("");
  const [dirty, setDirty] = useState(false);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [recognizing, setRecognizing] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [audioURL, setAudioURL] = useState("");
  const [language, setLanguage] = useState("chinese");
  const player = useRef<HTMLAudioElement>(null);
  const worker = useRef<Worker | null>(null);
  const generation = useRef(0);
  const refresh = async () =>
    setLessons(
      (await listLessons()).sort((a, b) => b.date.localeCompare(a.date)),
    );
  useEffect(() => {
    refresh()
      .then(() => setReady(true))
      .catch((e) => setError(`无法打开浏览器存储：${e.message}`));
  }, []);
  useEffect(() => {
    if (!lesson.audio) {
      setAudioURL("");
      return;
    }
    const url = URL.createObjectURL(lesson.audio);
    setAudioURL(url);
    return () => URL.revokeObjectURL(url);
  }, [lesson.audio]);
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (dirty || recognizing) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, recognizing]);
  useEffect(
    () => () => {
      worker.current?.terminate();
      generation.current++;
    },
    [],
  );
  function edit(patch: Partial<Lesson>) {
    setLesson((current) => ({ ...current, ...patch }));
    setDirty(true);
    setStatus("");
  }
  function select(next: Lesson) {
    if (dirty && !confirm("尚有未保存的修改，放弃这些修改？")) return;
    setLesson(next);
    setDirty(false);
    setError("");
    setStatus("");
  }
  async function action(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await task();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!lesson.course.trim() || !lesson.title.trim() || !lesson.date)
      throw new Error("请填写课程、课堂标题和日期。");
    const saved = {
      ...lesson,
      course: lesson.course.trim(),
      title: lesson.title.trim(),
      updatedAt: new Date().toISOString(),
    };
    await saveLesson(saved);
    setLesson(saved);
    setDirty(false);
    await refresh();
    setStatus("已保存到此浏览器。");
  }
  function cancel() {
    generation.current++;
    worker.current?.terminate();
    worker.current = null;
    setRecognizing(false);
    setStatus("已取消识别，原录音和文字稿保留。");
  }
  async function transcribe() {
    if (!lesson.audio) return;
    if (
      lesson.transcript.trim() &&
      !confirm("识别完成后将替换当前文字稿，继续？")
    )
      return;
    const token = ++generation.current;
    setRecognizing(true);
    setError("");
    setStatus("正在解码录音…");
    try {
      const context = new AudioContext();
      let decoded: AudioBuffer;
      try {
        decoded = await context.decodeAudioData(
          await lesson.audio.arrayBuffer(),
        );
      } finally {
        await context.close();
      }
      if (token !== generation.current) return;
      if (decoded.duration > 2 * 60 * 60)
        throw new Error("浏览器模式每段最多 2 小时，请先拆分录音。");
      const offline = new OfflineAudioContext(
        1,
        Math.ceil(decoded.duration * 16000),
        16000,
      );
      const source = offline.createBufferSource();
      source.buffer = decoded;
      source.connect(offline.destination);
      source.start();
      const audio = (await offline.startRendering()).getChannelData(0);
      if (token !== generation.current) return;
      const current = new Worker(
        new URL("./transcribe.worker.ts", import.meta.url),
        { type: "module" },
      );
      worker.current = current;
      const fail = (message: string) => {
        current.terminate();
        worker.current = null;
        setRecognizing(false);
        setStatus("");
        setError(
          `识别未完成：${message}。可重试，或导入已有文字稿；原数据保留。`,
        );
      };
      current.onerror = (event) => fail(event.message || "模型运行失败");
      current.onmessage = ({ data }) => {
        if (token !== generation.current) return;
        if (data.type === "progress")
          setStatus(
            `${data.message}${typeof data.progress?.progress === "number" ? ` · ${Math.round(data.progress.progress)}%` : ""}`,
          );
        if (data.type === "error") fail(data.message);
        if (data.type === "complete") {
          const result = Array.isArray(data.result)
            ? data.result[0]
            : data.result;
          const text =
            result.chunks
              ?.map(
                (chunk: {
                  timestamp: [number | null, number | null];
                  text: string;
                }) =>
                  `${chunk.timestamp[0] === null ? "" : `[${timestamp(chunk.timestamp[0])}] `}${chunk.text.trim()}`,
              )
              .join("\n") || result.text;
          if (!text?.trim()) {
            fail("未识别出文字");
            return;
          }
          edit({ transcript: text });
          setStatus("识别完成，请核对文字并保存课堂。");
          current.terminate();
          worker.current = null;
          setRecognizing(false);
        }
      };
      current.postMessage({ audio, language }, [audio.buffer]);
    } catch (e) {
      if (token !== generation.current) return;
      setRecognizing(false);
      setStatus("");
      setError(
        `无法识别录音：${e instanceof Error ? e.message : String(e)}。请尝试 WAV / MP3，或导入文字稿。`,
      );
    }
  }
  const locked = busy || recognizing || !ready;
  const filtered = lessons.filter((row) =>
    `${row.course} ${row.title} ${row.transcript} ${row.notes}`
      .toLocaleLowerCase()
      .includes(query.toLocaleLowerCase()),
  );
  return (
    <div className="web-app">
      <header className="web-header">
        <div>
          <span className="web-eyebrow">CLASSROOM ASSISTANT / 浏览器版</span>
          <h1>把课堂留下来。</h1>
          <p>录音、文字稿与课堂笔记，在自己的浏览器中整理。</p>
        </div>
        <a
          href="https://github.com/ericismyself314/classroom-assistant"
          target="_blank"
          rel="noreferrer"
        >
          开源项目 ↗
        </a>
      </header>
      <div className="web-notice">
        无需账户或服务器。记录存于此设备的浏览器，清除网站数据会删除记录，请定期备份文字与录音。浏览器版与
        Windows 版数据独立。
      </div>
      {error && (
        <div className="web-error" role="alert">
          {error}
          {!ready && (
            <button onClick={() => location.reload()}>重新加载</button>
          )}
        </div>
      )}
      <div className="web-layout">
        <aside className="web-library">
          <div className="web-section-title">
            <h2>
              我的课堂 <small>{lessons.length}</small>
            </h2>
            <button disabled={locked} onClick={() => select(newLesson())}>
              ＋ 新建
            </button>
          </div>
          <label>
            搜索课程、文字与笔记
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入关键词…"
            />
          </label>
          <div className="web-records">
            {!ready ? (
              <p>正在读取记录…</p>
            ) : !filtered.length ? (
              <p className="web-muted">
                {query
                  ? "没有匹配的课堂。"
                  : "从新建课堂开始，添加一段录音或文字稿。"}
              </p>
            ) : (
              filtered.map((row) => (
                <button
                  key={row.id}
                  className={`web-record ${row.id === lesson.id ? "selected" : ""}`}
                  disabled={locked}
                  onClick={() => select(row)}
                >
                  <span>
                    {row.course} · {row.date}
                  </span>
                  <strong>{row.title}</strong>
                  <small>
                    {row.audio ? "有录音 · " : ""}
                    {row.transcript ? "有文字稿" : "待整理"}
                  </small>
                </button>
              ))
            )}
          </div>
          <div className="web-backup">
            <h3>数据备份</h3>
            <p>
              文字备份包含已保存的课堂和笔记，不含录音。录音请在课堂中单独下载。
            </p>
            <button
              disabled={locked || !lessons.length}
              onClick={() => exportLessons(lessons)}
            >
              导出文字备份
            </button>
            <label>
              导入网页版文字备份
              <input
                type="file"
                accept=".json,application/json"
                disabled={locked}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file)
                    void action(async () => {
                      const count = await importLessons(file);
                      await refresh();
                      setStatus(`已导入 ${count} 条新记录，现有记录未覆盖。`);
                    });
                }}
              />
            </label>
          </div>
        </aside>
        <main className="web-editor">
          <div className="web-section-title">
            <h2>课堂记录</h2>
            <span className="web-muted">
              {dirty
                ? "有未保存的修改"
                : lesson.updatedAt
                  ? "已保存"
                  : "新课堂"}
            </span>
          </div>
          <fieldset disabled={locked}>
            <div className="web-fields">
              <label>
                课程
                <input
                  value={lesson.course}
                  onChange={(e) => edit({ course: e.target.value })}
                  placeholder="例如：计算机导论"
                />
              </label>
              <label>
                课堂日期
                <input
                  type="date"
                  value={lesson.date}
                  onChange={(e) => edit({ date: e.target.value })}
                />
              </label>
            </div>
            <label>
              课堂标题
              <input
                value={lesson.title}
                onChange={(e) => edit({ title: e.target.value })}
                placeholder="例如：第 3 讲 · 数据结构"
              />
            </label>
          </fieldset>
          <section>
            <h3>01 / 课堂录音</h3>
            <label>
              选择录音（最多 200 MB / 2 小时）
              <input
                type="file"
                accept="audio/*,.m4a,.mp3,.wav,.mp4"
                disabled={locked}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (!file) return;
                  if (file.size > 200 * 1024 * 1024) {
                    setError("录音超过 200 MB，请先拆分。");
                    return;
                  }
                  edit({ audio: file, audioName: file.name });
                  setError("");
                }}
              />
            </label>
            {audioURL && (
              <>
                <p className="web-muted">{lesson.audioName}</p>
                <audio
                  ref={player}
                  controls
                  src={audioURL}
                  onError={() =>
                    setError("浏览器无法播放此音频格式，请转换为 WAV 或 MP3。")
                  }
                />
                <button
                  onClick={() =>
                    download(lesson.audio!, lesson.audioName || "recording.wav")
                  }
                >
                  下载原录音
                </button>
              </>
            )}
            <details>
              <summary>在本机自动转写（实验性）</summary>
              <p>
                首次使用会从 Hugging Face 下载 Whisper tiny
                多语言模型及运行文件（约数百
                MB），录音不会上传。模型较小，长课录音可能很慢，结果需核对；如下载或设备内存不足，可导入文字稿。此功能需要联网加载模型，网页刷新离线启动未支持。
              </p>
              <label>
                授课语言
                <select
                  value={language}
                  disabled={locked}
                  onChange={(e) => setLanguage(e.target.value)}
                >
                  <option value="chinese">中文</option>
                  <option value="english">英文</option>
                  <option value="auto">自动识别 / 其他语言</option>
                </select>
              </label>
              <button
                disabled={locked || !lesson.audio}
                onClick={() => void transcribe()}
              >
                下载模型并在本机转写
              </button>
            </details>
            {recognizing && <button onClick={cancel}>取消识别</button>}
          </section>
          <section>
            <h3>02 / 文字稿与回听</h3>
            <label>
              导入 TXT 文件
              <input
                type="file"
                accept=".txt,text/plain"
                disabled={locked}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (!file) return;
                  if (
                    lesson.transcript &&
                    !confirm("用文件内容替换当前文字稿？")
                  )
                    return;
                  void action(async () => {
                    if (file.size > 5 * 1024 * 1024)
                      throw new Error("文字稿最多 5 MB。");
                    edit({ transcript: await file.text() });
                  });
                }}
              />
            </label>
            <label>
              文字稿（支持 [mm:ss] 或 [hh:mm:ss]）
              <textarea
                rows={9}
                value={lesson.transcript}
                disabled={locked}
                onChange={(e) => edit({ transcript: e.target.value })}
                placeholder="[00:00] 今天讨论…&#10;[05:20] 下周五提交作业…"
              />
            </label>
            {lesson.transcript && (
              <details>
                <summary>展开原文时间轴</summary>
                <div className="web-timeline">
                  {timeline(lesson.transcript).map((line, i) => (
                    <p key={i}>
                      {line.time !== null && (
                        <button
                          disabled={!audioURL}
                          onClick={() => {
                            if (player.current) {
                              player.current.currentTime = line.time!;
                              void player.current
                                .play()
                                .catch(() =>
                                  setError("请先点击音频播放器开始播放。"),
                                );
                            }
                          }}
                        >
                          {line.label}
                        </button>
                      )}
                      <span>{line.text}</span>
                    </p>
                  ))}
                </div>
              </details>
            )}
          </section>
          <section>
            <div className="web-section-title">
              <h3>03 / 课堂笔记</h3>
              <button
                disabled={locked || !lesson.transcript.trim()}
                onClick={() => {
                  if (
                    !lesson.notes ||
                    confirm("重新提取将替换现有笔记，继续？")
                  )
                    edit({ notes: extractNotes(lesson.transcript) });
                }}
              >
                提取原文与事项
              </button>
            </div>
            <p className="web-muted">
              按关键词摘录原话，保留时间戳；不会自动生成 AI
              概览或推断作业日期。可在下方自行整理。
            </p>
            <label>
              笔记
              <textarea
                rows={9}
                value={lesson.notes}
                disabled={locked}
                onChange={(e) => edit({ notes: e.target.value })}
                placeholder="记录这一课的重点、作业和待核对内容…"
              />
            </label>
          </section>
          <div className="web-actions">
            <button
              className="primary"
              disabled={locked}
              onClick={() => void action(save)}
            >
              保存课堂
            </button>
            <button
              disabled={!lesson.notes && !lesson.transcript}
              onClick={() =>
                download(
                  new Blob(
                    [
                      `# ${lesson.title}\n${lesson.course} · ${lesson.date}\n\n${lesson.notes}\n\n## 完整文字稿\n${lesson.transcript}`,
                    ],
                    { type: "text/markdown;charset=utf-8" },
                  ),
                  "classroom-notes.md",
                )
              }
            >
              导出 Markdown
            </button>
            <button
              className="danger"
              disabled={locked || !lessons.some((row) => row.id === lesson.id)}
              onClick={() => {
                if (confirm("删除这节课堂及其录音？此操作无法撤销。"))
                  void action(async () => {
                    await deleteLesson(lesson.id);
                    setLesson(newLesson());
                    setDirty(false);
                    await refresh();
                    setStatus("课堂已删除。");
                  });
              }}
            >
              删除课堂
            </button>
          </div>
          <p className="web-status" role="status" aria-live="polite">
            {status}
          </p>
        </main>
      </div>
      <footer className="web-footer">
        课堂助手 · 基于原开源项目的轻量浏览器模式。完整离线 AI
        分析与课表功能请使用 Windows 版。
      </footer>
    </div>
  );
}
