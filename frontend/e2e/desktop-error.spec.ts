import { test, expect } from "@playwright/test";

test("desktop backend failure offers reconnect instead of creating a new semester", async ({
  page,
}) => {
  await page.route("**/api/state", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "测试：本地服务暂不可用" }),
    }),
  );
  await page.goto("http://127.0.0.1:4174");
  await expect(page.getByRole("alert")).toContainText("本地服务暂不可用");
  await expect(
    page.getByRole("button", { name: "重新连接", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "完成，进入本周课表", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("本地服务暂不可用");
});
