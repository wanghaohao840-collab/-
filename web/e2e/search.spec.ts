import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, notesTest as test, registerUser, uniqueUsername } from "./fixtures";

async function accessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = results.violations.filter((item) => item.impact === "serious" || item.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
  const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(sizes.scroll).toBeLessThanOrEqual(sizes.client + 1);
}

test("scoped search, source details, editable note and QA handoff use real services", async ({ appUrl, page, context }, testInfo) => {
  test.slow();
  const filename = `知识检索-${testInfo.project.name}.md`;
  await registerUser(page, appUrl, uniqueUsername(`search_${testInfo.project.name}`));
  await page.goto(`${appUrl}/search`);
  await expect(page.getByRole("heading", { name: "文献检索", level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: "检索证据" })).toBeDisabled();
  await accessible(page);
  if (testInfo.project.name === "desktop") {
    for (const [width, height] of [[1920, 1080], [1440, 900], [1280, 800]]) {
      await page.setViewportSize({ width, height });
      await accessible(page);
      await page.screenshot({ path: testInfo.outputPath(`research-idle-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await page.goto(`${appUrl}/documents`);
  await page.locator(".document-toolbar").getByRole("button", { name: "导入文档" }).click();
  await page.getByLabel("选择文档").setInputFiles({ name: filename, mimeType: "text/markdown", buffer: Buffer.from("# 知研学习方法\n\n检索增强学习通过寻找证据、记录理解和持续复习，帮助形成可靠知识。来源核验使每一条笔记可以追溯到原始资料。") });
  await page.getByRole("button", { name: "开始导入" }).click();
  await expect(page.locator("li.document-row").filter({ hasText: filename })).toBeVisible({ timeout: 30000 });
  await page.goto(`${appUrl}/search`);
  await page.getByRole("checkbox", { name: filename }).check();
  await page.getByLabel("你想从这些资料中找到什么？").fill("检索增强学习");
  await page.getByRole("button", { name: "检索证据" }).click();
  const trigger = page.getByRole("button", { name: `查看来源 1：${filename}` });
  await expect(trigger).toBeVisible();
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "收起", exact: true }).click();
    await expect(page.getByRole("checkbox", { name: filename })).not.toBeVisible();
  }
  await expect(page).toHaveURL(`${appUrl}/search`);
  await accessible(page);
  await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: testInfo.outputPath("search-results.png"), fullPage: true });
  if (testInfo.project.name === "desktop") {
    for (const [width, height] of [[1920, 1080], [1440, 900], [1280, 800]]) {
      await page.setViewportSize({ width, height });
      await accessible(page);
      expect(await page.locator(".search-page").evaluate((element) => element.getBoundingClientRect().width)).toBeLessThanOrEqual(1560);
      await page.screenshot({ path: testInfo.outputPath(`research-results-${width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await trigger.click();
  const detail = page.getByRole("dialog", { name: "来源详情" });
  await expect(detail).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭来源详情" })).toBeFocused();
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "复制引用" }).click();
  await expect(page.getByText("引用已复制")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(filename);
  await accessible(page);
  await page.keyboard.press("Escape");
  await expect(detail).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await page.getByRole("button", { name: `加入笔记 1：${filename}` }).click();
  await page.getByLabel("笔记正文（保存前可编辑）").fill("我的检索笔记：检索证据后应核验原始来源。");
  await accessible(page);
  await page.getByRole("button", { name: "保存并打开笔记" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("search-editable-note.png"), fullPage: false });
  const savedResponse = page.waitForResponse((response) => response.url() === `${appUrl}/api/v1/notes` && response.request().method() === "POST");
  await page.getByRole("button", { name: "保存并打开笔记" }).click();
  const saved = await savedResponse;
  expect(saved.status()).toBe(201);
  const note = await saved.json();
  expect(note.sources[0].kind).toBe("document_chunk");
  expect(note.sources[0].excerpt_snapshot).toContain("检索增强学习");
  await expect(page).toHaveURL(`${appUrl}/notes?note=${note.id}`);
  await expect(page.getByLabel("笔记正文")).toHaveValue("我的检索笔记：检索证据后应核验原始来源。");
  await page.goto(`${appUrl}/search`);
  await expect(page.getByLabel("你想从这些资料中找到什么？")).toHaveValue("");
  await expect(page.getByRole("checkbox", { name: filename })).not.toBeChecked();
  await page.getByRole("checkbox", { name: filename }).check();
  await page.getByLabel("你想从这些资料中找到什么？").fill("检索增强学习");
  await page.getByRole("button", { name: "检索证据" }).click();
  await trigger.click();
  await page.getByRole("button", { name: "基于此文档问答" }).click();
  await expect(page.getByRole("dialog", { name: "新建对话" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: filename })).toBeChecked();
  await page.goto(`${appUrl}/documents`);
  const documentRow = page.locator("li.document-row").filter({ hasText: filename });
  await documentRow.getByRole("button", { name: `删除 ${filename}` }).click();
  await page.getByRole("dialog", { name: `删除 ${filename}` }).getByRole("button", { name: "确认删除" }).click();
  await expect(documentRow).toHaveCount(0);
  await expect.poll(async () => {
    const response = await page.request.get(`${appUrl}/api/v1/notes/${note.id}`);
    const body = await response.json();
    return { status: response.status(), deleted: body.sources?.[0]?.deleted, error: body.error?.code };
  }).toEqual({ status: 200, deleted: true, error: undefined });
  const afterDeletion = await (await page.request.get(`${appUrl}/api/v1/notes/${note.id}`)).json();
  expect(afterDeletion.body_markdown).toContain("我的检索笔记");
  expect(afterDeletion.sources[0].excerpt_snapshot).toBeNull();
  expect(afterDeletion.sources[0].locator).toBeNull();
});
