import { expect, test, type Page } from "@playwright/test";
import { E2E_USER } from "../playwright.config";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(E2E_USER.username);
  await page.getByLabel("Password").fill(E2E_USER.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  // "Live" appears only after the WebSocket auth handshake succeeds (token sent as a message).
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
}

test("wrong password is rejected", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Username").fill(E2E_USER.username);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("invalid username or password")).toBeVisible();
});

test("analyst reviews an alert's explanation and triages it", async ({ page }) => {
  await signIn(page);
  await expect(page.getByRole("link", { name: /10\.0\.0\.66/ })).toBeVisible(); // riskiest host

  await page.getByRole("link", { name: "Alerts", exact: true }).click();
  await page.getByRole("link", { name: "10.0.0.66" }).first().click(); // newest: the alert
  await expect(page.getByRole("heading", { name: "Why BotGraph flagged this host" })).toBeVisible();
  await expect(page.getByText("Regular, beacon-like timing")).toBeVisible();
  await expect(page.getByText("IRC share of connections (classic C2)")).toBeVisible();

  await page.getByLabel("Status").selectOption("investigating");
  await page.getByLabel("Note").fill("Beacons to 203.0.113.9 every minute");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();

  await page.reload();
  await expect(page.locator("header").getByText("Investigating")).toBeVisible();
  await expect(page.getByLabel("Note")).toHaveValue("Beacons to 203.0.113.9 every minute");
});

test("host timeline and live map render", async ({ page }) => {
  await signIn(page);
  await page.goto("/hosts/demo-lab/10.0.0.66");
  await expect(page.getByText("60 one-minute windows")).toBeVisible();
  await page.goto("/map");
  await expect(page.getByText("showing 10 of 10 hosts")).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();
});

test("sensors show drift and admins see the audit log", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Sensors", exact: true }).click();
  // The seeded beaconing pushed the network past the significant PSI level since calibration.
  const since = page.getByLabel(/^Drift since calibration: PSI 0\.32/);
  await expect(since.getByText("Significant")).toBeVisible();
  await expect(since.getByText("Regular, beacon-like timing")).toBeVisible();
  await expect(page.getByLabel(/^Drift vs training data/).getByText("Stable")).toBeVisible();

  await page.getByRole("link", { name: "Audit log" }).click();
  await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Signed in" }).first()).toBeVisible();
});

test("signing out revokes the token on the server", async ({ page }) => {
  await signIn(page);
  const token = await page.evaluate(
    () => JSON.parse(sessionStorage.getItem("botgraph.session") ?? "{}").token as string,
  );
  const me = () => page.request.get("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
  expect((await me()).status()).toBe(200);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect.poll(async () => (await me()).status()).toBe(401);
});
