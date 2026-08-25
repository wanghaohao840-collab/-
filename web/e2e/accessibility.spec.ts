import AxeBuilder from "@axe-core/playwright";
import type { Locator, Page } from "@playwright/test";

import { expect, openMore, registerUser, test, uniqueUsername } from "./fixtures";

async function expectNoSeriousOrCritical(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const violations = results.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical",
  );
  expect(violations, JSON.stringify(violations, null, 2)).toEqual([]);
}

async function expectVisibleFocus(locator: Locator) {
  await expect(locator).toBeFocused();
  const focusStyle = await locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth };
  });
  expect(focusStyle.outlineStyle).not.toBe("none");
  expect(Number.parseFloat(focusStyle.outlineWidth)).toBeGreaterThanOrEqual(2);
}

async function expectMinimumTarget(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box, "interactive target must have a rendered bounding box").not.toBeNull();
  expect(box?.width).toBeGreaterThanOrEqual(44);
  expect(box?.height).toBeGreaterThanOrEqual(44);
}

test("login, register, AppShell, and More drawer have no serious axe violations", async ({
  appUrl,
  page,
}, testInfo) => {
  await page.goto(`${appUrl}/login`);
  await expect(page.getByRole("heading", { level: 1, name: "登录" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "知研介绍" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "保持登录状态" })).toHaveCount(0);
  const password = page.getByLabel("密码", { exact: true });
  const visibilityToggle = page.getByRole("button", { name: "显示密码" });
  const toggleBox = await visibilityToggle.boundingBox();
  expect(toggleBox?.width).toBeGreaterThanOrEqual(44);
  expect(toggleBox?.height).toBeGreaterThanOrEqual(44);
  await visibilityToggle.click();
  await expect(password).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "隐藏密码" }).click();
  await expect(password).toHaveAttribute("type", "password");
  await expectNoSeriousOrCritical(page);

  await page.goto(`${appUrl}/register`);
  await expect(page.getByRole("heading", { level: 1, name: "注册" })).toBeVisible();
  await expectNoSeriousOrCritical(page);

  await registerUser(page, appUrl, uniqueUsername(`axe_${testInfo.project.name}`));
  await expectNoSeriousOrCritical(page);

  await openMore(page, testInfo.project.name);
  await expect(
    page.getByRole("dialog", { name: "更多" }).getByRole("link", { name: "学习笔记" }),
  ).toBeFocused();
  await expectNoSeriousOrCritical(page);
});

test("keyboard focus is visible and More returns focus to its trigger", async ({
  appUrl,
  page,
}, testInfo) => {
  await page.goto(`${appUrl}/login`);
  await expect(page.getByRole("heading", { level: 1, name: "登录" })).toBeVisible();
  await page.keyboard.press("Tab");
  const username = page.getByLabel("用户名");
  await expect(username).toBeFocused();
  const focusStyle = await username.evaluate((element) => {
    const style = getComputedStyle(element);
    return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth };
  });
  expect(focusStyle.outlineStyle).not.toBe("none");
  expect(Number.parseFloat(focusStyle.outlineWidth)).toBeGreaterThanOrEqual(2);

  await registerUser(page, appUrl, uniqueUsername(`focus_${testInfo.project.name}`));
  const trigger = await openMore(page, testInfo.project.name);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "更多" })).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("document empty state and import overlay meet axe and keyboard contracts", async ({
  appUrl,
  page,
}, testInfo) => {
  await registerUser(page, appUrl, uniqueUsername(`documents_axe_${testInfo.project.name}`));
  await page.goto(`${appUrl}/documents`);

  const heading = page.getByRole("heading", { level: 1, name: "文档库" });
  const toolbarImport = page
    .locator(".document-toolbar")
    .getByRole("button", { name: "导入文档" });
  const emptyImport = page
    .locator(".documents-empty")
    .getByRole("button", { name: "导入文档" });
  await expect(heading).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 2, name: "还没有文档" })).toBeVisible();
  await toolbarImport.focus();
  await expectVisibleFocus(toolbarImport);
  await expectNoSeriousOrCritical(page);

  if (testInfo.project.name === "mobile") {
    await expectMinimumTarget(toolbarImport);
    await expectMinimumTarget(emptyImport);
  }

  await page.evaluate(() => {
    document.body.style.overflow = "clip";
  });
  await toolbarImport.click();
  const dialog = page.getByRole("dialog", { name: "导入文档" });
  const close = dialog.getByRole("button", { name: "关闭导入文档" });
  const cancel = dialog.getByRole("button", { name: "取消", exact: true });
  await expect(dialog).toBeVisible();
  await expect(heading).toHaveCount(1);
  await expectVisibleFocus(close);
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("hidden");
  await expectNoSeriousOrCritical(page);

  if (testInfo.project.name === "mobile") {
    await expectMinimumTarget(close);
    await expectMinimumTarget(dialog.locator(".file-picker__browse"));
    await expectMinimumTarget(dialog.getByRole("button", { name: "开始导入" }));
    await expectMinimumTarget(cancel);
  }

  await close.press("Shift+Tab");
  await expect(cancel).toBeFocused();
  await cancel.press("Tab");
  await expect(close).toBeFocused();
  await close.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(toolbarImport).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("clip");
});

test("populated documents and delete dialog meet axe and keyboard contracts", async ({
  appUrl,
  page,
}, testInfo) => {
  test.slow();
  const filename = `accessible-${testInfo.project.name}.txt`;
  await registerUser(page, appUrl, uniqueUsername(`documents_list_${testInfo.project.name}`));
  await page.goto(`${appUrl}/documents`);
  await page
    .locator(".document-toolbar")
    .getByRole("button", { name: "导入文档" })
    .click();
  await page.getByLabel("选择文档").setInputFiles({
    name: filename,
    mimeType: "text/plain",
    buffer: Buffer.from("Accessible real document."),
  });
  const submission = page.waitForResponse(
    (response) =>
      response.url() === `${appUrl}/api/v1/imports` &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始导入" }).click();
  expect((await submission).status()).toBe(202);

  const documentList = page.getByRole("list", { name: "文档列表" });
  const documentRow = documentList.getByRole("listitem", { name: filename });
  const deleteTrigger = documentRow.getByRole("button", { name: `删除 ${filename}` });
  await expect(documentRow).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", { level: 1, name: "文档库" })).toHaveCount(1);
  await expectNoSeriousOrCritical(page);
  if (testInfo.project.name === "mobile") {
    await expectMinimumTarget(page.getByLabel("按名称筛选"));
    await expectMinimumTarget(deleteTrigger);
  }

  await page.evaluate(() => {
    document.body.style.overflow = "auto";
  });
  await page.getByLabel("按名称筛选").focus();
  await page.getByLabel("按名称筛选").press("Tab");
  await expectVisibleFocus(deleteTrigger);
  await deleteTrigger.press("Enter");
  const dialog = page.getByRole("dialog", { name: `删除 ${filename}` });
  const cancel = dialog.getByRole("button", { name: "取消", exact: true });
  const confirm = dialog.getByRole("button", { name: "确认删除" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "文档库" })).toHaveCount(1);
  await expectVisibleFocus(cancel);
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("hidden");
  await expectNoSeriousOrCritical(page);
  if (testInfo.project.name === "mobile") {
    await expectMinimumTarget(cancel);
    await expectMinimumTarget(confirm);
  }

  await cancel.press("Shift+Tab");
  await expect(confirm).toBeFocused();
  await confirm.press("Tab");
  await expect(cancel).toBeFocused();
  await cancel.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(deleteTrigger).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe("auto");
});
