import AxeBuilder from "@axe-core/playwright";
import { expect, qaTest as test, registerUser, settleVisuals, uniqueUsername } from "./fixtures";

test("light knowledge pages keep real actions readable at every viewport", async ({ appUrl, page }, testInfo) => {
  test.slow();
  async function inspect(name: string) {
    await settleVisuals(page);
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
    const width = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
    expect(width.scroll, `${name} horizontal overflow`).toBeLessThanOrEqual(width.client + 1);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(result.violations.filter(item => item.impact === "serious" || item.impact === "critical")).toEqual([]);
  }
  await page.goto(`${appUrl}/login`);
  if (testInfo.project.name !== "mobile") {
    await expect(page.locator(".auth-intro__art")).toBeVisible();
    await expect.poll(() => page.locator(".auth-intro__art").evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  }
  await inspect("login");
  await page.goto(`${appUrl}/register`);
  await inspect("register");
  await registerUser(page, appUrl, uniqueUsername("light_reader"));
  await inspect("overview-empty");
  await page.goto(`${appUrl}/documents`);
  await page.locator(".document-toolbar").getByRole("button", { name: "导入文档" }).click();
  const filename = "从阅读到理解：RAG 与长期知识积累的研究笔记.md";
  await page.getByLabel("选择文档").setInputFiles({ name: filename, mimeType: "text/markdown", buffer: Buffer.from("# Knowledge\nRetrieval connects documents to verifiable answers.") });
  await page.getByRole("button", { name: "开始导入" }).click();
  const row = page.locator("li.document-row").filter({ hasText: filename });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await inspect("documents");
  await row.getByRole("button", { name: "开始问答" }).click();
  await page.getByRole("dialog", { name: "新建对话" }).getByRole("button", { name: "创建对话" }).click();
  await page.getByLabel("向这些文档提问").fill("如何把一次阅读转化为可追溯的长期知识？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("这是确定性的可信回答，可通过右侧引用核对来源。")).toBeVisible({ timeout: 15_000 });
  await inspect("qa");
  if (testInfo.project.name === "desktop") {
    await page.setViewportSize({ width: 1199, height: 900 });
  }
  await page.getByRole("button", { name: "引用 1", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "引用证据" })).toBeVisible();
  await inspect("qa-evidence");
  await page.getByRole("button", { name: "关闭引用证据" }).press("Escape");
  await expect(page.getByRole("button", { name: "引用 1", exact: true })).toBeFocused();
  if (testInfo.project.name === "desktop") await page.setViewportSize({ width: 1440, height: 1024 });
  await page.goto(`${appUrl}/overview`);
  await expect(page.getByRole("img", { name: /学习活动趋势/ })).toBeVisible();
  await inspect("overview");
  if (testInfo.project.name === "mobile") {
    await page.setViewportSize({ width: 320, height: 844 });
    await inspect("overview-320");
    await page.goto(`${appUrl}/documents`);
    await inspect("documents-320");
  }
});
