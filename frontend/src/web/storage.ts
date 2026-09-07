export interface Lesson {
  id: string;
  course: string;
  title: string;
  date: string;
  transcript: string;
  notes: string;
  audio?: Blob;
  audioName?: string;
  updatedAt: string;
}

// Scope data to the project path: multiple GitHub Pages projects share an origin.
const DB_NAME = `classroom-assistant:${new URL(".", location.href).pathname}`;
function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () =>
      request.result.createObjectStore("lessons", { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () =>
      reject(new Error("请关闭其他课堂助手标签页后重试。"));
  });
}
async function transaction<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("lessons", mode);
    const request = action(tx.objectStore("lessons"));
    // A successful request is not a durable write until the transaction commits.
    tx.oncomplete = () => {
      db.close();
      resolve(request.result);
    };
    tx.onabort = tx.onerror = () => {
      db.close();
      reject(
        tx.error || new Error("浏览器存储失败，请检查可用空间或隐私设置。"),
      );
    };
  });
}
export const listLessons = () =>
  transaction("readonly", (store) => store.getAll() as IDBRequest<Lesson[]>);
export const saveLesson = (lesson: Lesson) =>
  transaction("readwrite", (store) => store.put(lesson));
export const deleteLesson = (id: string) =>
  transaction("readwrite", (store) => store.delete(id));

export function localDate() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
export function newLesson(): Lesson {
  return {
    id: crypto.randomUUID(),
    course: "",
    title: "",
    date: localDate(),
    transcript: "",
    notes: "",
    updatedAt: "",
  };
}
export function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// Backup only text metadata; audio has its own lossless download control.
export function exportLessons(lessons: Lesson[]) {
  const records = lessons.map(({ audio, ...lesson }) => lesson);
  download(
    new Blob(
      [
        JSON.stringify(
          { format: "classroom-assistant-web", version: 1, lessons: records },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    ),
    "classroom-notes.json",
  );
}
export async function importLessons(file: File): Promise<number> {
  if (file.size > 20 * 1024 * 1024) throw new Error("文字备份不能超过 20 MB。");
  const backup = JSON.parse(await file.text());
  if (
    backup?.format !== "classroom-assistant-web" ||
    backup.version !== 1 ||
    !Array.isArray(backup.lessons)
  )
    throw new Error("不是网页版课堂助手 v1 文字备份。");
  const records: Lesson[] = backup.lessons.map(
    (row: Record<string, unknown>) => {
      if (
        !row ||
        ["course", "title", "date", "transcript", "notes"].some(
          (key) => typeof row[key] !== "string",
        )
      )
        throw new Error("备份内容不完整，未导入任何记录。");
      // Restore as new records, never overwrite existing work or attached audio.
      return {
        ...newLesson(),
        course: row.course as string,
        title: row.title as string,
        date: row.date as string,
        transcript: row.transcript as string,
        notes: row.notes as string,
        updatedAt: new Date().toISOString(),
      };
    },
  );
  if (!records.length) return 0;
  const db = await open();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction("lessons", "readwrite");
    for (const record of records) tx.objectStore("lessons").add(record);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onabort = tx.onerror = () => {
      db.close();
      reject(
        tx.error ||
          new Error(
            "文字备份导入失败，未导入任何记录。请检查浏览器可用空间后重试。",
          ),
      );
    };
  });
  return records.length;
}
