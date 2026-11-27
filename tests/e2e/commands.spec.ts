import { expect, test, type Page } from "@playwright/test";
import { registerViaApi, type Account } from "./support/api";

/**
 * Slash commands, the mode chip and Regenerate (TASK-040) against the real stack. The API and
 * worker must run with LLM_MODE=fake (docs/02_outputs/06_dev_setup.md): `/pdf` then runs the
 * document pipeline with the scripted model and the real renderer.
 *
 * Here a `/pdf` answer shows its chip and has no Regenerate; the document CARD (status polling,
 * download) and the /documents page are covered by documents.spec.ts (TASK-042).
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
        "the command e2e tests.",
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

/** Send from the home page and wait until the first answer has been stored. */
async function startChat(page: Page, text: string) {
  await box(page).fill(text);
  await box(page).press("Enter");
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
  await expect(log(page).getByText(`Fake reply: ${text}`)).toBeVisible();
}

async function say(page: Page, text: string) {
  // The composer ignores Enter while the previous run is still finishing (Stop is showing).
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await box(page).fill(text);
  await box(page).press("Enter");
}

test("/pdf answers with a Document chip and no Regenerate", async ({
  page,
}) => {
  await signIn(page);
  await startChat(page, "What did we decide about the launch?");
  await expect(page.getByRole("button", { name: "Regenerate" })).toBeVisible();

  await say(page, "/pdf");

  const reply = log(page).getByRole("article", { name: "Nexus reply" }).last();
  await expect(reply.getByText(/Answered by:\s*Document/)).toBeVisible();
  await expect(reply).toContainText("into a PDF");
  await expect(page.getByRole("button", { name: "Regenerate" })).toHaveCount(0);

  // The chip is the stored one after a reload, too.
  await page.reload();
  const again = log(page).getByRole("article", { name: "Nexus reply" }).last();
  await expect(again.getByText(/Answered by:\s*Document/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Regenerate" })).toHaveCount(0);
});

test("the worker renders the PDF: the message reports the document ready", async ({
  page,
}) => {
  const account = await signIn(page);
  await startChat(page, "Notes on the launch plan");
  await say(page, "/pdf");
  await expect(
    log(page).getByRole("article", { name: "Nexus reply" }).last(),
  ).toContainText("into a PDF");

  const conversation = page.url().split("/c/")[1];
  type Doc = {
    status: string;
    page_count: number | null;
    error: string | null;
  };
  await expect
    .poll(
      async () => {
        const res = await page.request.get(
          `/api/v1/conversations/${conversation}/messages`,
          { headers: { authorization: `Bearer ${account.token}` } },
        );
        const { items } = (await res.json()) as {
          items: { documents: Doc[] }[];
        };
        return items.flatMap((m) => m.documents).map((d) => d.status);
      },
      { timeout: 30_000, message: "the render job should finish" },
    )
    .toEqual(["ready"]);
});

test("a plain chat answer has no chip and keeps Regenerate", async ({
  page,
}) => {
  await signIn(page);
  await startChat(page, "hello there");

  await expect(page.getByText("Answered by")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Regenerate" })).toBeVisible();
});

test("/memory shows a Memory chip and /help shows none, and neither can be regenerated", async ({
  page,
}) => {
  await signIn(page);
  await startChat(page, "hi");

  await say(page, "/memory");
  const memory = log(page).getByRole("article", { name: "Nexus reply" }).last();
  await expect(memory.getByText(/Answered by:\s*Memory/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Regenerate" })).toHaveCount(0);

  await say(page, "/help");
  const help = log(page).getByRole("article", { name: "Nexus reply" }).last();
  await expect(help).toContainText("Commands");
  await expect(help.getByText("Answered by")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Regenerate" })).toHaveCount(0);
});

test("the / menu offers commands and inserts one without sending", async ({
  page,
}) => {
  await signIn(page);

  await box(page).fill("/");
  const menu = page.getByRole("listbox", { name: "Commands" });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("option")).toHaveCount(8);

  await box(page).fill("/me");
  await expect(menu.getByRole("option")).toHaveCount(3);
  await box(page).press("ArrowDown");
  await box(page).press("Enter"); // completes the highlighted command, does not send
  await expect(box(page)).toHaveValue("/memory remember ");
  await expect(menu).toHaveCount(0);
  await expect(page).toHaveURL(/\/$/); // still on the home page: nothing was sent

  await box(page).press("Escape");
  await box(page).fill("/etc/hosts");
  await expect(menu).toHaveCount(0); // a path is not a command
});

test("the / menu works in the chat composer too", async ({ page }) => {
  await signIn(page);
  await startChat(page, "hello");

  await box(page).fill("/pd");
  await page.getByRole("option", { name: /\/pdf last/ }).click();
  await expect(box(page)).toHaveValue("/pdf last");
});
