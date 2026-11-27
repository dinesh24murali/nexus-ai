import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { registerViaApi, type Account } from "./support/api";

/**
 * Documents (TASK-042) against the real stack: the card under a `/pdf` answer, the download, the
 * /documents page and its delete. The API and worker must run with LLM_MODE=fake
 * (docs/02_outputs/06_dev_setup.md): `/pdf` then runs the document pipeline with the scripted model
 * and the worker's real renderer, so the PDF in these tests is a real one.
 */

const box = (page: Page) => page.getByRole("textbox", { name: "Message" });
const log = (page: Page) => page.getByRole("log", { name: "Conversation" });

test.beforeAll(async ({ request, baseURL }) => {
  const account = await registerViaApi(request, "Probe");
  const auth = { authorization: `Bearer ${account.token}` };
  const created = await request.post(`${baseURL}/api/v1/conversations`, {
    headers: auth,
    data: {},
  });
  const { id } = (await created.json()) as { id: string };
  const sent = await request.post(
    `${baseURL}/api/v1/conversations/${id}/messages`,
    { headers: auth, data: { content: "probe" } },
  );
  const answer = (await sent.text())
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map(
      (line) => JSON.parse(line.slice(6)) as { type: string; delta?: string },
    )
    .map((event) => (event.type === "token" ? event.delta : ""))
    .join("");
  if (answer !== "Fake reply: probe") {
    throw new Error(
      "The stack is not running the fake LLM. Start it with LLM_MODE=fake before running " +
        "the documents e2e tests.",
    );
  }
});

async function signIn(page: Page): Promise<Account> {
  const account = await registerViaApi(page.request);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /^Hello,/ }),
  ).toBeVisible();
  return account;
}

async function say(page: Page, text: string) {
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await box(page).fill(text);
  await box(page).press("Enter");
}

/** A chat with one answer, then `/pdf`: returns once the document card is on screen. */
async function makeDocument(page: Page, topic: string) {
  await box(page).fill(topic);
  await box(page).press("Enter");
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
  await expect(log(page).getByText(`Fake reply: ${topic}`)).toBeVisible();
  await say(page, "/pdf");
  const card = log(page).getByRole("region", { name: /^Document:/ });
  await expect(card).toBeVisible();
  return card;
}

test("a /pdf answer shows a card that turns into a download of a real PDF", async ({
  page,
}) => {
  await signIn(page);
  const card = await makeDocument(page, "Notes on the launch plan");

  // The worker renders it after the answer is written: the card follows.
  const download = card.getByRole("button", { name: /^Download/ });
  await expect(download).toBeVisible({ timeout: 30_000 });
  await expect(card).toContainText(/\d+ pages? · /);

  const [saved] = await Promise.all([
    page.waitForEvent("download"),
    download.click(),
  ]);
  expect(saved.suggestedFilename()).toMatch(/^[A-Za-z0-9 ._-]+\.pdf$/);
  const path = await saved.path();
  const bytes = await readFile(path);
  expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");

  // The card is still there, and still works, after a reload (it comes from the stored message).
  await page.reload();
  await expect(
    log(page)
      .getByRole("region", { name: /^Document:/ })
      .getByRole("button", { name: /^Download/ }),
  ).toBeVisible();
});

test("the /documents page lists the PDF, downloads it and deletes it", async ({
  page,
}) => {
  const account = await signIn(page);
  const card = await makeDocument(page, "Quarterly review notes");
  await expect(card.getByRole("button", { name: /^Download/ })).toBeVisible({
    timeout: 30_000,
  });

  await page.getByRole("link", { name: "Documents" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Documents" }),
  ).toBeVisible();
  const rows = page
    .getByRole("table")
    .getByRole("row")
    .filter({
      has: page.getByRole("button", { name: /^Download/ }),
    });
  await expect(rows).toHaveCount(1);
  await expect(rows.getByText("Ready")).toBeVisible();

  const [saved] = await Promise.all([
    page.waitForEvent("download"),
    rows.getByRole("button", { name: /^Download/ }).click(),
  ]);
  expect((await readFile(await saved.path())).subarray(0, 5).toString()).toBe(
    "%PDF-",
  );

  // Open the chat it came from.
  await rows.getByRole("link", { name: /^Open the chat/ }).click();
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
  await page.goBack();

  // Cancel keeps it; confirming deletes it.
  await rows.getByRole("button", { name: /^Delete/ }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(rows).toHaveCount(1);
  await rows.getByRole("button", { name: /^Delete/ }).click();
  await page.getByRole("button", { name: /^Confirm delete/ }).click();
  await expect(page.getByText("No documents yet.")).toBeVisible();

  // Gone for good: the API says 404, and so does the card in the chat.
  const listed = await page.request.get("/api/v1/documents", {
    headers: { authorization: `Bearer ${account.token}` },
  });
  expect(((await listed.json()) as { items: unknown[] }).items).toEqual([]);
  await page.goBack();
  await expect(page.getByText("This document was deleted.")).toBeVisible();
});

test("another user cannot read, download or delete a document", async ({
  page,
  request,
}) => {
  const alice = await signIn(page);
  const card = await makeDocument(page, "Alice private notes");
  await expect(card.getByRole("button", { name: /^Download/ })).toBeVisible({
    timeout: 30_000,
  });
  const aliceAuth = { authorization: `Bearer ${alice.token}` };
  const { items } = (await (
    await page.request.get("/api/v1/documents", { headers: aliceAuth })
  ).json()) as { items: { id: string }[] };
  expect(items).toHaveLength(1);
  const id = items[0]!.id;

  const bob = await registerViaApi(request, "Bob");
  const bobAuth = { authorization: `Bearer ${bob.token}` };
  for (const path of [
    `/api/v1/documents/${id}`,
    `/api/v1/documents/${id}/download`,
  ]) {
    expect((await request.get(path, { headers: bobAuth })).status()).toBe(404);
  }
  expect(
    (
      await request.delete(`/api/v1/documents/${id}`, { headers: bobAuth })
    ).status(),
  ).toBe(404);
  expect(
    (
      (await (
        await request.get("/api/v1/documents", { headers: bobAuth })
      ).json()) as {
        items: unknown[];
      }
    ).items,
  ).toEqual([]);

  // Alice's PDF is untouched.
  const mine = await request.get(`/api/v1/documents/${id}/download`, {
    headers: aliceAuth,
  });
  expect(mine.status()).toBe(200);
  expect(mine.headers()["content-type"]).toBe("application/pdf");
  expect(mine.headers()["content-disposition"]).toMatch(/^attachment;/);
});
