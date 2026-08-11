import { expect, openMore, registerUser, settleVisuals, test } from "./fixtures";

async function fillLogin(
  page: Parameters<typeof settleVisuals>[0],
  username = "visual_reader",
) {
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill("e2e-only-passphrase");
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

test("login baseline", async ({ appUrl, page }) => {
  await page.goto(`${appUrl}/login`);
  await expect(page.getByRole("heading", { level: 1, name: "登录" })).toBeVisible();
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("login.png");
});

test("authenticated shell baseline", async ({ appUrl, page }, testInfo) => {
  await registerUser(page, appUrl, `visual_shell_${testInfo.project.name}`);
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("shell.png");
});

test("mobile More drawer baseline", async ({ appUrl, page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "More baseline is the mobile bottom sheet");
  await registerUser(page, appUrl, "visual_more_mobile");
  await openMore(page, testInfo.project.name);
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("more-drawer.png");
});

test("validation error baseline", async ({ appUrl, page }) => {
  await page.goto(`${appUrl}/login`);
  const responsePromise = page.waitForResponse(`${appUrl}/api/v1/auth/login`);
  await fillLogin(page, "visual_reader\u200b");
  expect((await responsePromise).status()).toBe(422);
  await expect(page.getByText("用户名格式无效")).toBeVisible();
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("validation-error.png");
});

test("server error baseline", async ({ appServer, appUrl, page }) => {
  // Stopping the real unified runtime can exceed the default budget on a cold
  // Windows filesystem while the browser resolves the failed request.
  test.slow();
  await page.goto(`${appUrl}/login`);
  await appServer.stop();
  try {
    await fillLogin(page);
    await expect(page.getByRole("alert")).toContainText("服务暂时不可用");
    await settleVisuals(page);
    await expect(page).toHaveScreenshot("server-error.png");
  } finally {
    await appServer.start();
  }
});

test("session-expired baseline", async ({ appServer, appUrl, page }, testInfo) => {
  // This assertion intentionally restarts the full FastAPI + Gradio runtime.
  test.slow();
  await registerUser(page, appUrl, `visual_expired_${testInfo.project.name}`);
  await appServer.restart();
  if (testInfo.project.name === "mobile") {
    await openMore(page, testInfo.project.name);
  }
  const logoutScope =
    testInfo.project.name === "mobile"
      ? page.getByRole("dialog", { name: "更多" })
      : page.getByRole("complementary", { name: "应用侧栏" });
  const logoutButton = logoutScope.getByRole("button", { name: "退出登录" });
  const responsePromise = page.waitForResponse(`${appUrl}/api/v1/auth/logout`);
  await logoutButton.focus();
  await logoutButton.press("Enter");
  expect((await responsePromise).status()).toBe(401);
  await expect(page.getByRole("dialog", { name: "会话已过期" })).toBeVisible();
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("session-expired.png");
});
