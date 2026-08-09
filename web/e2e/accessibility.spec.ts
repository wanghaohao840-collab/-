import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

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

test("login, register, AppShell, and More drawer have no serious axe violations", async ({
  appUrl,
  page,
}, testInfo) => {
  await page.goto(`${appUrl}/login`);
  await expect(page.getByRole("heading", { level: 1, name: "登录" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "知研介绍" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "保持登录状态" })).toBeChecked();
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
