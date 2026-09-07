import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { timestamp, timeline } from "../src/web/transcript";

test("hour timestamps normalize without changing legacy seek positions", async ({
  page,
}) => {
  expect([3599, 3600, 3723, 7200].map(timestamp)).toEqual([
    "59:59",
    "01:00:00",
    "01:02:03",
    "02:00:00",
  ]);
  expect(
    timeline("[62:03] old\n[01:02:03] new").map((row) => row.time),
  ).toEqual([3723, 3723]);
  await page.goto("./");
  await page.getByLabel("文字稿（支持").fill("[01:02:03] 下周交作业");
  await page.getByText("展开原文时间轴", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "01:02:03", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "提取原文与事项", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "笔记", exact: true }),
  ).toHaveValue(/\[01:02:03\]/);
});

test("abort with null transaction error reports actionable failure and allows retry", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const original = IDBDatabase.prototype.transaction;
    IDBDatabase.prototype.transaction = function (
      ...args: Parameters<typeof original>
    ) {
      const tx = original.apply(this, args);
      if (args[1] === "readwrite" && !(window as any).allowWrites)
        queueMicrotask(() => tx.abort());
      return tx;
    };
  });
  await page.goto("./");
  const backup = {
    name: "backup.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        format: "classroom-assistant-web",
        version: 1,
        lessons: [
          {
            course: "测试",
            title: "课堂",
            date: "2026-09-07",
            transcript: "原文",
            notes: "",
          },
        ],
      }),
    ),
  };
  await page.getByLabel("导入网页版文字备份").setInputFiles(backup);
  await expect(page.getByRole("alert")).toContainText(
    "文字备份导入失败，未导入任何记录",
  );
  await page.reload();
  await expect(page.locator(".web-record")).toHaveCount(0);
  await page.evaluate(() => {
    (window as any).allowWrites = true;
  });
  await page.getByLabel("导入网页版文字备份").setInputFiles(backup);
  await expect(page.getByRole("status")).toContainText("已导入 1 条新记录");
});

// Execute the actual worker source with a controlled inference boundary. CI
// exercises cleanup ordering/failures without downloading or mocking app code.
for (const scenario of [
  "success",
  "load-error",
  "inference-error",
  "cleanup-error",
  "both-error",
]) {
  test(`worker lifecycle: ${scenario}`, async () => {
    const events: string[] = [];
    const messages: any[] = [];
    const recognizer = Object.assign(
      async () => {
        if (scenario === "inference-error" || scenario === "both-error")
          throw new Error("original inference failure");
        return { text: "recognized speech" };
      },
      {
        dispose: async () => {
          events.push("dispose");
          if (scenario === "cleanup-error" || scenario === "both-error")
            throw new Error("cleanup failure");
        },
      },
    );
    const scope: any = {
      postMessage: (message: any) => {
        messages.push(message);
        events.push(message.type);
      },
    };
    const source = readFileSync("src/web/transcribe.worker.ts", "utf8");
    const js = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
      },
    }).outputText;
    runInNewContext(js, {
      exports: {},
      self: scope,
      console: { warn: () => events.push("cleanup-warning") },
      require: (name: string) => {
        if (name !== "@huggingface/transformers")
          throw new Error(`Unexpected module: ${name}`);
        return {
          env: { backends: { onnx: { wasm: {} } } },
          pipeline: async () => {
            if (scenario === "load-error")
              throw new Error("model load failure");
            return recognizer;
          },
        };
      },
    });
    await scope.onmessage({
      data: { audio: new Float32Array(1), language: "english" },
    });
    const terminal = messages.filter((m) => m.type !== "progress");
    expect(terminal).toHaveLength(1);
    expect(events.at(-1)).toBe(terminal[0].type);
    if (scenario === "load-error") {
      expect(events).not.toContain("dispose");
      expect(terminal[0].message).toContain("model load failure");
    } else {
      expect(events.filter((e) => e === "dispose")).toHaveLength(1);
      if (scenario === "inference-error" || scenario === "both-error")
        expect(terminal[0].message).toContain("original inference failure");
      else
        expect(terminal[0]).toEqual({
          type: "complete",
          result: { text: "recognized speech" },
        });
    }
  });
}
