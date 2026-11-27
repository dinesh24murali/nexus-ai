import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { registerViaApi } from "./support/api";

/**
 * Deep research (TASK-043) against the real stack. The API and worker must run with
 * LLM_MODE=fake (docs/02_outputs/06_dev_setup.md): the planner and the report are the scripted
 * models, the web search is the offline stand-in, and the PDF is made by the worker's real renderer.
 */

const TOPIC = "solid-state batteries";
const box = (page: Page) => page.getByRole("textbox", { name: "Message" });
const log = (page: Page) => page.getByRole("log", { name: "Conversation" });
const plan = (page: Page) => page.getByRole("group", { name: "Research plan" });

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
        "the research e2e tests.",
    );
  }
});

async function signIn(page: Page) {
  const account = await registerViaApi(page.request);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /^Hello,/ }),
  ).toBeVisible();
  return account;
}

/** Send from the home page; the first message creates the conversation. */
async function ask(page: Page, text: string) {
  await box(page).fill(text);
  await box(page).press("Enter");
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
}

test("/research shows the plan first, and approving writes a cited report", async ({
  page,
}) => {
  await signIn(page);

  await ask(page, `/research ${TOPIC}`);

  // The plan is a card; nothing has been searched and no answer exists yet.
  await expect(plan(page)).toBeVisible();
  await expect(plan(page).getByRole("listitem")).toHaveCount(4);
  await expect(plan(page).getByText(`Topic: ${TOPIC}`)).toBeVisible();
  await expect(page.getByText("Searched the web")).toHaveCount(0);
  await expect(
    log(page).getByRole("article", { name: "Nexus reply" }),
  ).toHaveCount(0);

  await plan(page).getByRole("button", { name: "Approve and search" }).click();

  const reply = log(page).getByRole("article", { name: "Nexus reply" }).last();
  await expect(reply.getByText("Key findings")).toBeVisible();
  await expect(reply.getByText(/Answered by:\s*Research/)).toBeVisible();
  // The four searches are on the answer, and the sources it cites are chips under it.
  await expect(reply.getByText("Searched the web")).toHaveCount(4);
  await expect(reply.getByRole("list", { name: /sources/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Regenerate" })).toHaveCount(0);
  await expect(plan(page)).toHaveCount(0);

  // After a reload everything is what was stored.
  await page.reload();
  await expect(
    log(page)
      .getByRole("article", { name: "Nexus reply" })
      .last()
      .getByText("Key findings"),
  ).toBeVisible();
});

test("the plan can be edited: only the edited queries are searched", async ({
  page,
}) => {
  await signIn(page);
  await ask(page, `/research ${TOPIC}`);
  await expect(plan(page)).toBeVisible();

  await plan(page).getByRole("button", { name: "Edit" }).click();
  await page
    .getByRole("textbox", { name: "Search query 1" })
    .fill("my own query");
  await page.getByRole("button", { name: "Remove query 4" }).click();
  await page.getByRole("button", { name: "Remove query 3" }).click();
  await page.getByRole("button", { name: "Search with these queries" }).click();

  const reply = log(page).getByRole("article", { name: "Nexus reply" }).last();
  await expect(reply.getByText("Key findings")).toBeVisible();
  await expect(reply.getByText("Searched the web")).toHaveCount(2);
  await expect(reply.getByText("my own query")).toBeVisible();
});

test("rejecting the plan searches nothing", async ({ page }) => {
  await signIn(page);
  await ask(page, `/research ${TOPIC}`);
  await expect(plan(page)).toBeVisible();

  await plan(page).getByRole("button", { name: "Don't search" }).click();

  const reply = log(page).getByRole("article", { name: "Nexus reply" }).last();
  await expect(reply).toContainText("I won't search the web");
  await expect(reply.getByText("Searched the web")).toHaveCount(0);
  await expect(plan(page)).toHaveCount(0);
});

test("the plan survives a reload, and a new message abandons it", async ({
  page,
}) => {
  await signIn(page);
  await ask(page, `/research ${TOPIC}`);
  await expect(plan(page)).toBeVisible();

  await page.reload();
  await expect(plan(page)).toBeVisible();
  await expect(plan(page).getByRole("listitem")).toHaveCount(4);

  await box(page).fill("never mind, hello");
  await box(page).press("Enter");
  await expect(
    log(page).getByText("Fake reply: never mind, hello"),
  ).toBeVisible();
  await expect(plan(page)).toHaveCount(0);
});

test("a research report asked as a PDF becomes a document card and a real PDF", async ({
  page,
}) => {
  await signIn(page);

  await ask(
    page,
    `Research ${TOPIC} as a PDF [[route:deep_research]][[slot:topic=${TOPIC}]][[slot:output=pdf]]`,
  );
  await expect(plan(page)).toBeVisible();
  await plan(page).getByRole("button", { name: "Approve and search" }).click();

  const card = log(page).getByRole("region", { name: /^Document:/ });
  await expect(card).toBeVisible();
  const download = card.getByRole("button", { name: /^Download/ });
  await expect(download).toBeVisible({ timeout: 30_000 });
  await expect(card).toContainText("Research report");

  const [saved] = await Promise.all([
    page.waitForEvent("download"),
    download.click(),
  ]);
  const bytes = await readFile(await saved.path());
  expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
});

test("/help lists /research and a bare /research explains its usage", async ({
  page,
}) => {
  await signIn(page);
  await ask(page, "/help");
  await expect(log(page).getByText(/\/research <question>/)).toBeVisible();

  await box(page).fill("/research");
  await box(page).press("Enter"); // the / menu completes the command first
  await expect(box(page)).toHaveValue("/research ");
  await box(page).press("Enter"); // ... and a second Enter sends it
  await expect(
    log(page).getByText(/Usage: \/research <question>/),
  ).toBeVisible();
  await expect(plan(page)).toHaveCount(0);
});
