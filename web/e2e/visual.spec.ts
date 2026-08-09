import { expect, openMore, registerUser, settleVisuals, test } from "./fixtures";

const errorEnvelope = (
  status: number,
  code: string,
  message: string,
  fieldErrors: Record<string, string> = {},
) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify({
    error: { code, message, retryable: status >= 500, field_errors: fieldErrors },
  }),
});

async function fillLogin(page: Parameters<typeof settleVisuals>[0]) {
  await page.getByLabel("用户名").fill("visual_reader");
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
  await page.route(`${appUrl}/api/v1/auth/login`, async (route) => {
    await route.fulfill(
      errorEnvelope(422, "validation_error", "请修正表单错误", {
        username: "用户名格式无效",
      }),
    );
  });
  await page.goto(`${appUrl}/login`);
  await fillLogin(page);
  await expect(page.getByText("用户名格式无效")).toBeVisible();
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("validation-error.png");
});

test("server error baseline", async ({ appUrl, page }) => {
  await page.route(`${appUrl}/api/v1/auth/login`, async (route) => {
    await route.fulfill(
      errorEnvelope(500, "internal_error", "服务暂时不可用，请稍后重试"),
    );
  });
  await page.goto(`${appUrl}/login`);
  await fillLogin(page);
  await expect(page.getByRole("alert")).toContainText("服务暂时不可用");
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("server-error.png");
});

test("session-expired baseline", async ({ appUrl, page }) => {
  await page.goto(`${appUrl}/login`);
  await expect(page.getByRole("heading", { level: 1, name: "登录" })).toBeVisible();
  await page.evaluate(() => {
    const current = history.state as Record<string, unknown> | null;
    history.replaceState(
      { ...current, usr: { from: "/overview", sessionExpired: true } },
      "",
      "/login",
    );
  });
  await page.reload();
  await expect(page.getByRole("dialog", { name: "会话已过期" })).toBeVisible();
  await settleVisuals(page);
  await expect(page).toHaveScreenshot("session-expired.png");
});
