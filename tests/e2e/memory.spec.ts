import { expect, test, type Page } from "@playwright/test";
import { registerViaApi, type Account } from "./support/api";

/**
 * Long-term memory against the real stack (TASK-027): the toast after a turn that taught Nexus
 * something, and Settings → Memory (switch, add, edit, delete, clear all). The API and worker must
 * run with LLM_MODE=fake (docs/02_outputs/06_dev_setup.md): the fake extraction turns
 * "Remember that I prefer Go for backend work." into the memory "User prefers Go for backend work".
 */

const box = (page: Page) => page.getByRole("textbox", { name: "Message" });
const memories = (page: Page) => page.getByRole("list", { name: "Memories" });
const toggle = (page: Page) =>
  page.getByRole("switch", { name: "Use long-term memory" });

const GO = "User prefers Go for backend work";

/** The fake model must be active, or no memory here would be scripted. */
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
        "the memory e2e tests.",
    );
  }
});

/** A fresh account, signed in in the browser (the refresh cookie is shared with the page). */
async function signIn(page: Page): Promise<Account> {
  const account = await registerViaApi(page.request);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /^Hello,/ }),
  ).toBeVisible();
  return account;
}

async function teach(
  page: Page,
  text = "Remember that I prefer Go for backend work.",
) {
  await box(page).fill(text);
  await box(page).press("Enter");
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
}

/** Add a memory through the API, so a test can start from a known list. */
async function seed(page: Page, account: Account, ...texts: string[]) {
  for (const content of texts) {
    const res = await page.request.post("/api/v1/memories", {
      headers: { authorization: `Bearer ${account.token}` },
      data: { content },
    });
    expect(res.status()).toBe(201);
  }
}

test("a toast tells you what Nexus learned, and Manage opens Settings → Memory", async ({
  page,
}) => {
  await signIn(page);

  await teach(page);

  const toast = page.getByRole("region", { name: "Notifications" });
  await expect(toast).toContainText("Nexus remembered 1 thing.", {
    timeout: 15_000,
  });
  await toast.getByRole("link", { name: "Manage" }).click();

  await expect(page).toHaveURL(/\/settings\/memory$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Memory" }),
  ).toBeVisible();
  await expect(memories(page).getByText(GO)).toBeVisible();
  await expect(page.getByText("Learned from a chat")).toBeVisible();
});

test("the sidebar opens Settings → Memory, empty at first", async ({
  page,
}) => {
  await signIn(page);

  await page.getByRole("link", { name: "Memory" }).click();

  await expect(page).toHaveURL(/\/settings\/memory$/);
  await expect(page.getByText(/Nothing yet/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Clear all" })).toBeDisabled();
  await expect(toggle(page)).toBeChecked();
});

test("add, edit and delete a memory", async ({ page }) => {
  await signIn(page);
  await page.goto("/settings/memory");

  await page.getByLabel("Add a memory").fill("Lives in Pune.");
  await page.getByRole("button", { name: "Add" }).click();
  await expect(memories(page).getByText("Lives in Pune.")).toBeVisible();
  await expect(page.getByText("Added by you")).toBeVisible();

  // The same fact again is refused, and says so.
  await page.getByLabel("Add a memory").fill("Lives in Pune.");
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "You already have a memory like this.",
  );
  await page.getByLabel("Add a memory").fill("");

  // Edit in place; it is still there after a reload.
  await page.getByRole("button", { name: "Edit: Lives in Pune." }).click();
  await page.getByLabel("Memory text").fill("Lives in Berlin.");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(memories(page).getByText("Lives in Berlin.")).toBeVisible();
  await page.reload();
  await expect(memories(page).getByText("Lives in Berlin.")).toBeVisible();
  await expect(page.getByText("Lives in Pune.")).toHaveCount(0);

  // Delete asks first.
  await page.getByRole("button", { name: /^Delete: Lives in Berlin/ }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(memories(page).getByText("Lives in Berlin.")).toBeVisible();
  await page.getByRole("button", { name: /^Delete: Lives in Berlin/ }).click();
  await page.getByRole("button", { name: /^Confirm delete/ }).click();
  await expect(page.getByText(/Nothing yet/)).toBeVisible();
});

test("text Nexus may not remember is refused with a reason that does not repeat it", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/settings/memory");

  await page.getByLabel("Add a memory").fill("I have type 2 diabetes.");
  await page.getByRole("button", { name: "Add" }).click();

  const alert = page.getByRole("alert");
  await expect(alert).toContainText(
    "does not remember this kind of personal information",
  );
  await expect(alert).not.toContainText("diabetes");
  await expect(page.getByText(/Nothing yet/)).toBeVisible();
});

test("clear all asks first and then empties the list", async ({ page }) => {
  const account = await signIn(page);
  await seed(page, account, "Likes tea.", "Lives in Pune.");
  await page.goto("/settings/memory");
  await expect(memories(page).getByRole("listitem")).toHaveCount(2);

  await page.getByRole("button", { name: "Clear all" }).click();
  await expect(page.getByText(/Delete all 2 memories\?/)).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(memories(page).getByRole("listitem")).toHaveCount(2);

  await page.getByRole("button", { name: "Clear all" }).click();
  await page.getByRole("button", { name: "Delete all" }).click();
  await expect(page.getByText(/Nothing yet/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Nothing yet/)).toBeVisible();
});

test("with memory off nothing is learned, no toast appears, and the list can still be managed", async ({
  page,
}) => {
  const account = await signIn(page);
  await seed(page, account, "Likes tea.");
  await page.goto("/settings/memory");

  await toggle(page).click();
  await expect(toggle(page)).not.toBeChecked();
  await page.reload();
  await expect(toggle(page)).not.toBeChecked(); // the account setting, not a page state
  await expect(memories(page).getByText("Likes tea.")).toBeVisible();

  await page.goto("/");
  await teach(page);
  await expect(
    page.getByRole("article", { name: "Nexus reply" }),
  ).toBeVisible();
  await page.waitForTimeout(5_000); // longer than the first two checks would have waited
  await expect(page.getByText(/Nexus remembered/)).toHaveCount(0);
  await page.goto("/settings/memory");
  await expect(memories(page).getByRole("listitem")).toHaveCount(1); // still just the tea

  await toggle(page).click();
  await expect(toggle(page)).toBeChecked();
});

test("one account never sees another's memories", async ({ page, browser }) => {
  const first = await signIn(page);
  await seed(page, first, "Secret of the first account.");
  await page.goto("/settings/memory");
  await expect(
    memories(page).getByText("Secret of the first account."),
  ).toBeVisible();

  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await registerViaApi(otherPage.request, "Other");
  await otherPage.goto("/settings/memory");

  await expect(otherPage.getByText(/Nothing yet/)).toBeVisible();
  await expect(otherPage.getByText("Secret of the first account.")).toHaveCount(
    0,
  );
  await other.close();
});

/** What the API says the account remembers (the source of truth for what is really deleted). */
async function stored(page: Page, account: Account): Promise<string[]> {
  const res = await page.request.get("/api/v1/memories?limit=100", {
    headers: { authorization: `Bearer ${account.token}` },
  });
  const body = (await res.json()) as { items: { content: string }[] };
  return body.items.map((item) => item.content).sort();
}

async function say(page: Page, text: string) {
  await box(page).fill(text);
  await box(page).press("Enter");
}

test("memory by chat: remember, list and /help answer without the chat model", async ({
  page,
}) => {
  const account = await signIn(page);

  await say(page, "/memory remember I prefer Go for backend work.");
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
  await expect(page.getByText(`Saved: ${GO}.`)).toBeVisible();
  expect(await stored(page, account)).toEqual([`${GO}.`]);

  await say(page, "/memory");
  await expect(page.getByText("I remember 1 thing about you:")).toBeVisible();

  await say(page, "/help");
  await expect(page.getByText(/I'm Nexus\. I can chat/)).toBeVisible();
  await expect(page.getByText("/memory forget <text>")).toBeVisible();

  // A command that is not available yet says so instead of falling through to the model.
  await say(page, "/research why is the sky blue");
  await expect(page.getByText(/is not available yet/)).toBeVisible();
});

test("forgetting a memory asks first: keep it, then forget it", async ({
  page,
}) => {
  const account = await signIn(page);
  await seed(page, account, "User works at Acme.", "User prefers dark mode.");

  await say(page, "/memory forget works at Acme");
  const card = page.getByRole("group", { name: "Forget a memory?" });
  await expect(card).toBeVisible();
  await expect(card.getByText("User works at Acme.")).toBeVisible();
  // The card is only a question: nothing is deleted while it waits.
  expect(await stored(page, account)).toHaveLength(2);

  // Keep it: nothing is deleted.
  await card.getByRole("button", { name: "Keep it" }).click();
  await expect(page.getByText("OK, I didn't delete anything.")).toBeVisible();
  await expect(card).toHaveCount(0);
  expect(await stored(page, account)).toHaveLength(2);

  // Ask again and confirm: exactly that memory goes.
  await say(page, "/memory forget works at Acme");
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Forget it" }).click();
  await expect(page.getByText("Forgot: User works at Acme.")).toBeVisible();
  await expect(card).toHaveCount(0);
  expect(await stored(page, account)).toEqual(["User prefers dark mode."]);

  // A reload keeps the answer, and no card comes back.
  await page.reload();
  await expect(page.getByText("Forgot: User works at Acme.")).toBeVisible();
  await expect(card).toHaveCount(0);
});

test("a forget card survives a reload and a new message abandons it", async ({
  page,
}) => {
  const account = await signIn(page);
  await seed(page, account, "User works at Acme.");

  await say(page, "/memory forget works at Acme");
  const card = page.getByRole("group", { name: "Forget a memory?" });
  await expect(card).toBeVisible();

  await page.reload();
  await expect(card).toBeVisible(); // restored from the conversation, not from run state

  await say(page, "never mind, hello");
  await expect(card).toHaveCount(0);
  expect(await stored(page, account)).toEqual(["User works at Acme."]);
});
