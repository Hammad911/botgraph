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
