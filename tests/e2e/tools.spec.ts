import { expect, test, type Page } from "@playwright/test";
import { registerViaApi } from "./support/api";

/**
 * Web search approval against the real stack (TASK-023): the run stops at an approval card, and
 * approve, edit, reject, a reload mid-approval and abandoning it all work. The API and worker must
 * run with LLM_MODE=fake (docs/02_outputs/06_dev_setup.md): the scripted model asks for
 * `web_search("Go release notes")` for `[[fixture:web_search]]`, and the offline fake searcher
 * answers with results that quote the query, so an edited query is visible in the source chips.
 */

const box = (page: Page) => page.getByRole("textbox", { name: "Message" });
const replies = (page: Page) =>
  page.getByRole("article", { name: "Nexus reply" });
const sources = (page: Page) => page.getByRole("list", { name: "Sources" });
const card = (page: Page) =>
  page.getByRole("group", { name: "Search the web?" });
/** The tool cards under an answer (TASK-024), not the approval card above. */
const toolCard = (page: Page, name: string) =>
  page.getByRole("list", { name: "Tool calls" }).getByRole("group", { name });

const ANSWER =
  "According to the search results, the latest Go release focuses on tooling and runtime improvements [1].";

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
        "the tools e2e tests.",
    );
  }
});

/** Sign in, open a chat and ask something that makes the model want to search the web. */
async function askForASearch(page: Page) {
  await registerViaApi(page.request);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /^Hello,/ }),
  ).toBeVisible();
  await box(page).fill("[[fixture:web_search]] What is new in Go?");
  await box(page).press("Enter");
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
  await expect(card(page)).toBeVisible();
}

test("the run stops at an approval card that shows the exact query, and nothing is searched yet", async ({
  page,
}) => {
  await askForASearch(page);

  await expect(card(page)).toContainText("Go release notes");
  await expect(card(page).getByRole("button", { name: "Approve" })).toBeEnabled();
  await expect(card(page).getByRole("button", { name: "Edit" })).toBeEnabled();
  await expect(card(page).getByRole("button", { name: "Reject" })).toBeEnabled();
  await expect(replies(page)).toHaveCount(0); // no answer yet
  await expect(sources(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0); // not running
});

test("approve: the search runs, the answer cites the web and the card is gone", async ({
  page,
}) => {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(String(e)));
  await askForASearch(page);

  await card(page).getByRole("button", { name: "Approve" }).click();

  await expect(replies(page)).toContainText(ANSWER);
  await expect(card(page)).toHaveCount(0);
  const chip = sources(page).getByRole("button", {
    name: /\[1\] Results for: Go release notes/,
  });
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(page.getByRole("dialog", { name: /Source 1/ })).toContainText(
    "Fake web result about Go release notes",
  );
  await page.keyboard.press("Escape");

  // The search shows as a finished tool card with its query and result count.
  await expect(toolCard(page, "Searched the web")).toContainText("Go release notes");
  await expect(toolCard(page, "Searched the web")).toContainText("2 results found");

  // What was saved is what comes back, and the approval does not come back.
  await page.reload();
  await expect(replies(page)).toContainText(ANSWER);
  await expect(card(page)).toHaveCount(0);
  await expect(toolCard(page, "Searched the web")).toContainText("Go release notes");
  await expect(
    sources(page).getByRole("button", { name: /\[1\] Results for: Go release notes/ }),
  ).toBeVisible();
  expect(problems).toEqual([]);
});

test("edit: the edited query is the one that is searched", async ({ page }) => {
  await askForASearch(page);

  await card(page).getByRole("button", { name: "Edit" }).click();
  const query = card(page).getByRole("textbox", { name: "Search query" });
  await expect(query).toHaveValue("Go release notes");
  await query.fill("Go 1.24 generics");
  await card(page).getByRole("button", { name: "Search with this query" }).click();

  await expect(replies(page)).toContainText(ANSWER);
  await expect(
    sources(page).getByRole("button", { name: /\[1\] Results for: Go 1\.24 generics/ }),
  ).toBeVisible();
  await expect(card(page)).toHaveCount(0);
});

test("reject: no search is made and the model still answers", async ({ page }) => {
  await askForASearch(page);

  await card(page).getByRole("button", { name: "Reject" }).click();

  await expect(replies(page)).toBeVisible();
  await expect(card(page)).toHaveCount(0);
  await expect(sources(page)).toHaveCount(0); // nothing was searched, so nothing is cited
  await expect(toolCard(page, "Web search declined")).toBeVisible();
  // Reload only once the run has ended: a stream is not replayed, so the answer is read back
  // from what was saved.
  await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);
  await page.reload();
  await expect(toolCard(page, "Web search declined")).toBeVisible(); // and it is kept
});

test("reload mid-approval: the card comes back and can still be answered", async ({
  page,
}) => {
  await askForASearch(page);

  await page.reload();

  await expect(card(page)).toBeVisible();
  await expect(card(page)).toContainText("Go release notes");
  await expect(replies(page)).toHaveCount(0);
  await card(page).getByRole("button", { name: "Approve" }).click();
  await expect(replies(page)).toContainText(ANSWER);
  await expect(card(page)).toHaveCount(0);
});

test("sending a new message instead abandons the approval", async ({ page }) => {
  await askForASearch(page);

  await box(page).fill("never mind, just say hello");
  await box(page).press("Enter");

  await expect(replies(page)).toContainText("Fake reply: never mind, just say hello");
  await expect(card(page)).toHaveCount(0);
  await page.reload();
  await expect(card(page)).toHaveCount(0); // and it stays gone
});

test("a calculation needs no approval", async ({ page }) => {
  await registerViaApi(page.request);
  await page.goto("/");
  await box(page).fill("[[fixture:calc]] What is 17.5% of 2348901?");
  await box(page).press("Enter");

  await expect(replies(page)).toContainText(
    "17.5% of 2,348,901 is 411057.675.",
  );
  await expect(card(page)).toHaveCount(0);
  await expect(toolCard(page, "Calculated")).toContainText("0.175 * 2348901");
});
