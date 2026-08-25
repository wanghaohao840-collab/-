import type { APIResponse } from "@playwright/test";

import { expect, registerUser, test, uniqueUsername } from "./fixtures";

type SubmittedBatch = {
  batch_id: string;
  tasks: Array<{ document_id: string; task_id: string }>;
};

async function expectPublicNotFound(
  response: APIResponse,
  code: "document_not_found" | "import_batch_not_found" | "import_task_not_found",
  message: string,
) {
  expect(response.status()).toBe(404);
  expect(await response.json()).toEqual({
    error: {
      code,
      message,
      retryable: false,
      field_errors: {},
    },
  });
}

test("imports, lists, and deletes a real document", async ({ appUrl, page }, testInfo) => {
  const filename = `e2e-notes-${testInfo.project.name}.md`;

  await registerUser(
    page,
    appUrl,
    uniqueUsername(`documents_${testInfo.project.name}`),
  );
  await page.goto(`${appUrl}/documents`);

  const heading = page.getByRole("heading", { level: 1, name: "文档库" });
  await expect(heading).toHaveCount(1);
  await expect(heading).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "还没有文档" })).toBeVisible();

  await page
    .locator(".document-toolbar")
    .getByRole("button", { name: "导入文档" })
    .click();
  await page.getByLabel("选择文档").setInputFiles({
    name: filename,
    mimeType: "text/markdown",
    buffer: Buffer.from("# E2E\nA real imported document."),
  });
  const submitResponse = page.waitForResponse(
    (response) =>
      response.url() === `${appUrl}/api/v1/imports` &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始导入" }).click();
  expect((await submitResponse).status()).toBe(202);

  const documentRow = page.locator("li.document-row").filter({ hasText: filename });
  await expect(documentRow).toBeVisible({ timeout: 30_000 });

  await documentRow.getByRole("button", { name: `删除 ${filename}` }).click();
  const deleteDialog = page.getByRole("dialog", { name: `删除 ${filename}` });
  await expect(deleteDialog).toContainText(`确认删除“${filename}”`);
  const deleteResponse = page.waitForResponse(
    (response) =>
      response.url().startsWith(`${appUrl}/api/v1/documents/`) &&
      response.request().method() === "DELETE",
  );
  await deleteDialog.getByRole("button", { name: "确认删除" }).click();
  expect((await deleteResponse).status()).toBe(204);
  await expect(documentRow).toHaveCount(0);

  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "文档库" })).toHaveCount(1);
  await expect(page.locator("li.document-row").filter({ hasText: filename })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 2, name: "还没有文档" })).toBeVisible();
});

test("keeps import and document IDs isolated between authenticated users", async ({
  appUrl,
  browser,
  page,
}, testInfo) => {
  const filename = `private-${testInfo.project.name}.txt`;
  await registerUser(page, appUrl, uniqueUsername(`owner_${testInfo.project.name}`));
  await page.goto(`${appUrl}/documents`);
  await page
    .locator(".document-toolbar")
    .getByRole("button", { name: "导入文档" })
    .click();
  await page.getByLabel("选择文档").setInputFiles({
    name: filename,
    mimeType: "text/plain",
    buffer: Buffer.from("Private document owned by user A."),
  });
  const submission = page.waitForResponse(
    (response) =>
      response.url() === `${appUrl}/api/v1/imports` &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始导入" }).click();
  const submissionResponse = await submission;
  expect(submissionResponse.status()).toBe(202);
  const batch = (await submissionResponse.json()) as SubmittedBatch;
  const [task] = batch.tasks;
  expect(task).toBeDefined();
  await expect(page.locator("li.document-row").filter({ hasText: filename })).toBeVisible({
    timeout: 30_000,
  });

  const otherContext = await browser.newContext();
  try {
    const otherPage = await otherContext.newPage();
    const registration = otherPage.waitForResponse(
      (response) => response.url() === `${appUrl}/api/v1/auth/register`,
    );
    await registerUser(
      otherPage,
      appUrl,
      uniqueUsername(`other_${testInfo.project.name}`),
    );
    const registrationResponse = await registration;
    expect(registrationResponse.status()).toBe(200);
    const { csrf_token: csrfToken } = (await registrationResponse.json()) as {
      csrf_token: string;
    };
    expect(csrfToken).toBeTruthy();
    const sessionCookie = (await otherContext.cookies(appUrl)).find(
      (cookie) => cookie.name === "zhiyan_session",
    );
    expect(sessionCookie?.httpOnly).toBe(true);

    const otherDocuments = await otherContext.request.get(`${appUrl}/api/v1/documents`);
    expect(otherDocuments.status()).toBe(200);
    expect(await otherDocuments.json()).toEqual({ items: [] });

    await expectPublicNotFound(
      await otherContext.request.get(
      `${appUrl}/api/v1/imports/${batch.batch_id}`,
      ),
      "import_batch_not_found",
      "导入批次不存在",
    );
    await expectPublicNotFound(
      await otherContext.request.post(
        `${appUrl}/api/v1/imports/${batch.batch_id}/tasks/${task.task_id}/retry`,
        { headers: { "X-CSRF-Token": csrfToken } },
      ),
      "import_task_not_found",
      "导入任务不存在",
    );
    await expectPublicNotFound(
      await otherContext.request.post(
        `${appUrl}/api/v1/imports/${batch.batch_id}/tasks/${task.task_id}/cancel`,
        { headers: { "X-CSRF-Token": csrfToken } },
      ),
      "import_task_not_found",
      "导入任务不存在",
    );
    await expectPublicNotFound(
      await otherContext.request.post(
        `${appUrl}/api/v1/imports/${batch.batch_id}/retry-failed`,
        { headers: { "X-CSRF-Token": csrfToken } },
      ),
      "import_batch_not_found",
      "导入批次不存在",
    );
    await expectPublicNotFound(
      await otherContext.request.delete(
        `${appUrl}/api/v1/documents/${task.document_id}`,
        { headers: { "X-CSRF-Token": csrfToken } },
      ),
      "document_not_found",
      "文档不存在",
    );

    const ownerBatch = await page.context().request.get(
      `${appUrl}/api/v1/imports/${batch.batch_id}`,
    );
    expect(ownerBatch.status()).toBe(200);
    const ownerDocuments = await page.context().request.get(`${appUrl}/api/v1/documents`);
    expect(ownerDocuments.status()).toBe(200);
    expect(await ownerDocuments.json()).toMatchObject({
      items: [{ document_id: task.document_id, name: filename }],
    });
  } finally {
    await otherContext.close();
  }
});
