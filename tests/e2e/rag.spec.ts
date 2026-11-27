import { expect, test, type Page } from "@playwright/test";
import { registerViaApi } from "./support/api";

/**
 * Files and cited answers against the real stack (TASK-021): upload, processing, a cited answer,
 * deleting. The API and worker must run with LLM_MODE=fake (docs/02_outputs/06_dev_setup.md): the
 * scripted model answers `[[fixture:rag]]` by calling `search_my_files` and citing `[1]`, and the
 * fake embeddings are a hashed bag of words, so a file that contains the word "churn" is found.
 */

// Short on purpose: the fake embeddings are a hashed bag of words, so the cosine of a one-word
// query ("churn") against a chunk is about 1/sqrt(words) and must stay above the 0.25 score cut-off.
const NOTES = "Churn decreased to 3.1 percent.";

const box = (page: Page) => page.getByRole("textbox", { name: "Message" });
const replies = (page: Page) =>
  page.getByRole("article", { name: "Nexus reply" });
const uploads = (page: Page) => page.getByRole("list", { name: "Uploads" });
const picker = (page: Page) => page.getByLabel("Choose files to upload");
const sources = (page: Page) => page.getByRole("list", { name: "Sources" });

const notes = (name = "q3-notes.txt", text = NOTES) => ({
  name,
  mimeType: "text/plain",
  buffer: Buffer.from(text),
});

/** The fake model must be active, or no answer here would be scripted. */
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
    .map((line) => JSON.parse(line.slice(6)) as { type: string; delta?: string })
    .map((event) => (event.type === "token" ? event.delta : ""))
    .join("");
  if (answer !== "Fake reply: probe") {
    throw new Error(
      "The stack is not running the fake LLM. Start it with LLM_MODE=fake before running " +
        "the rag e2e tests.",
    );
  }
});

async function ask(page: Page) {
  await box(page).fill("[[fixture:rag]] What happened to churn?");
  await box(page).press("Enter");
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
}

test("upload with the paperclip, wait until it is ready, get a cited answer with its excerpt", async ({
  page,
}) => {
  await registerViaApi(page.request);
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(String(e)));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /^Hello,/ }),
  ).toBeVisible();

  await picker(page).setInputFiles(notes());

  // The worker parses, chunks and embeds it; the chip follows the server's own status.
  await expect(uploads(page)).toContainText("q3-notes.txt");
  await expect(uploads(page)).toContainText("Ready", { timeout: 30_000 });

  await ask(page);
  await expect(replies(page)).toContainText(
    "According to your files, monthly churn decreased to 3.1% [1].",
  );

  // The source is a chip under the answer; it opens the passage the answer rests on.
  const chip = sources(page).getByRole("button", { name: /\[1\] q3-notes\.txt/ });
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(page.getByRole("dialog", { name: /Source 1/ })).toContainText(
    "Churn decreased to 3.1 percent.",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // What was saved is what comes back: the chip survives a reload.
  await page.reload();
  await expect(
    sources(page).getByRole("button", { name: /\[1\] q3-notes\.txt/ }),
  ).toBeVisible();
  expect(problems).toEqual([]);
});

test("the files page lists a file, and a deleted file is no longer found", async ({
  page,
}) => {
  await registerViaApi(page.request);
  await page.goto("/");
  await page.getByRole("link", { name: "Files" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Files" }),
  ).toBeVisible();
  await expect(page.getByText("No files yet.")).toBeVisible();
  await expect(page.getByLabel("Files used")).toHaveText("0/50 files");

  await picker(page).setInputFiles(notes());

  const row = page.getByRole("row", { name: /q3-notes\.txt/ });
  await expect(row).toContainText("Ready", { timeout: 30_000 });
  await expect(row).toContainText("1"); // chunks
  await expect(page.getByLabel("Files used")).toHaveText("1/50 files");

  // Found while it exists...
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await ask(page);
  await expect(sources(page)).toBeVisible();

  // ...gone after deleting: the confirmation comes first, cancelling keeps it.
  await page.getByRole("link", { name: "Files" }).click();
  await page.getByRole("button", { name: "Delete q3-notes.txt" }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(row).toBeVisible();
  await page.getByRole("button", { name: "Delete q3-notes.txt" }).click();
  await page.getByRole("button", { name: "Confirm delete q3-notes.txt" }).click();
  await expect(page.getByText("No files yet.")).toBeVisible();
  await expect(page.getByLabel("Files used")).toHaveText("0/50 files");

  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await ask(page);
  await expect(replies(page)).toContainText("According to your files");
  await expect(sources(page)).toHaveCount(0); // the search found nothing to cite
});

test("refusals are explained on the file they concern", async ({ page }) => {
  await registerViaApi(page.request);
  await page.goto("/");
  await page.getByRole("link", { name: "Files" }).click();
  // Wait for the page itself: the home page has a paperclip input too, and a file chosen on it
  // while the route changes would go to a page that is about to disappear.
  await expect(
    page.getByRole("heading", { level: 1, name: "Files" }),
  ).toBeVisible();

  await picker(page).setInputFiles(notes("virus.exe", "MZ"));
  await expect(uploads(page)).toContainText(
    "Only PDF, DOCX, TXT and MD files are supported.",
  );

  await picker(page).setInputFiles(notes());
  await expect(page.getByRole("row", { name: /q3-notes\.txt/ })).toBeVisible();
  await picker(page).setInputFiles(notes()); // the same bytes again
  await expect(uploads(page)).toContainText(
    "You have already uploaded this file.",
  );
});
