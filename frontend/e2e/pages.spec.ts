import { test, expect } from "@playwright/test";

test("save, reload, search, timeline, backup and restore without any backend", async ({
  page,
}) => {
  const errors: string[] = [];
  const apiRequests: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.url().includes("/api/")) apiRequests.push(request.url());
  });
  await page.goto("./");
  await expect(page).toHaveTitle("课堂助手");
  await page.getByLabel("课程", { exact: true }).fill("计算机导论");
  await page.getByLabel("课堂标题", { exact: true }).fill("数据结构");
  await page
    .getByLabel("文字稿（支持")
    .fill("[00:00] 今天讨论数组。\n[05:20] 下周五提交第一章作业。");
  await page
    .getByRole("button", { name: "提取原文与事项", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "笔记", exact: true }),
  ).toHaveValue(/下周五提交第一章作业/);
  await page.getByRole("button", { name: "保存课堂", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("已保存到此浏览器。");
  await page.reload();
  await page.getByRole("button", { name: /计算机导论.*数据结构/ }).click();
  await expect(page.getByLabel("文字稿（支持")).toHaveValue(/今天讨论数组/);
  await page.getByLabel("搜索课程、文字与笔记").fill("没有这个词");
  await expect(page.getByText("没有匹配的课堂。")).toBeVisible();
  await page.getByLabel("搜索课程、文字与笔记").fill("第一章");
  await expect(
    page.getByRole("button", { name: /计算机导论.*数据结构/ }),
  ).toBeVisible();
  await page.getByText("展开原文时间轴", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "05:20", exact: true }),
  ).toBeDisabled();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出文字备份", exact: true }).click();
  const backup = await downloaded;
  await page
    .getByLabel("导入网页版文字备份")
    .setInputFiles((await backup.path())!);
  await expect(page.getByRole("status")).toHaveText(
    "已导入 1 条新记录，现有记录未覆盖。",
  );
  await expect(
    page.getByRole("button", { name: /计算机导论.*数据结构/ }),
  ).toHaveCount(2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
  expect(apiRequests).toEqual([]);
});

test("invalid restore is atomic; unsaved edits are protected", async ({
  page,
}) => {
  await page.goto("./");
  await page.getByLabel("课程", { exact: true }).fill("未保存课程");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "＋ 新建", exact: true }).click();
  await expect(page.getByLabel("课程", { exact: true })).toHaveValue(
    "未保存课程",
  );
  await page
    .getByLabel("导入网页版文字备份")
    .setInputFiles({
      name: "invalid.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({
          format: "classroom-assistant-web",
          version: 1,
          lessons: [
            {
              course: "有效",
              title: "一",
              date: "2026-09-07",
              transcript: "",
              notes: "",
            },
            {},
          ],
        }),
      ),
    });
  await expect(page.getByRole("alert")).toContainText("未导入任何记录");
  await expect(page.locator(".web-record")).toHaveCount(0);
});

test("audio persists, timestamp seeks, cancellation preserves text, and deletion removes record", async ({
  page,
}) => {
  await page.goto("./");
  await page.getByLabel("课程", { exact: true }).fill("音频课");
  await page.getByLabel("课堂标题", { exact: true }).fill("回听");
  // Two seconds of PCM silence, generated without an external fixture or network.
  const wav = Buffer.alloc(44 + 32000 * 2);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24);
  wav.writeUInt32LE(32000, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(wav.length - 44, 40);
  await page
    .getByLabel("选择录音")
    .setInputFiles({ name: "课堂.wav", mimeType: "audio/wav", buffer: wav });
  await page.getByLabel("文字稿（支持").fill("[00:01] 需要回听的内容");
  await page.getByRole("button", { name: "保存课堂", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("已保存到此浏览器。");
  await page.reload();
  await page.getByRole("button", { name: /音频课.*回听/ }).click();
  await page.getByText("展开原文时间轴", { exact: true }).click();
  await page.getByRole("button", { name: "00:01", exact: true }).click();
  await expect
    .poll(() =>
      page
        .locator("audio")
        .evaluate((audio: HTMLAudioElement) => audio.currentTime),
    )
    .toBeGreaterThanOrEqual(1);
  await page.getByText("在本机自动转写（实验性）", { exact: true }).click();
  // No model downloads in normal CI. Exercise cancellation during decode/load.
  await page.route(/huggingface\.co/, (route) => route.abort());
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .getByRole("button", { name: "下载模型并在本机转写", exact: true })
    .click();
  await page.getByRole("button", { name: "取消识别", exact: true }).click();
  await expect(page.getByLabel("文字稿（支持")).toHaveValue(
    "[00:01] 需要回听的内容",
  );
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除课堂", exact: true }).click();
  await expect(page.locator(".web-record")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".web-record")).toHaveCount(0);
});
