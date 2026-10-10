import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { inflateSync } from "node:zlib";
import { chromium, expect } from "@playwright/test";

function mintPixels(png) {
  // Read an actual canvas screenshot, not React props or a source count.
  const width = png.readUInt32BE(16), height = png.readUInt32BE(20);
  assert.equal(png[24], 8);
  assert.equal(png[28], 0);
  const channels = png[25] === 6 ? 4 : png[25] === 2 ? 3 : 0;
  assert.ok(channels, "browser screenshot must be RGB or RGBA PNG");
  const parts = [];
  for (let offset = 8; offset < png.length;) {
    const length = png.readUInt32BE(offset);
    if (png.toString("ascii", offset + 4, offset + 8) === "IDAT") parts.push(png.subarray(offset + 8, offset + 8 + length));
    offset += length + 12;
  }
  const raw = inflateSync(Buffer.concat(parts)), stride = width * channels;
  let previous = Buffer.alloc(stride), count = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)], row = Buffer.alloc(stride);
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? row[x - channels] : 0, b = previous[x], c = x >= channels ? previous[x - channels] : 0;
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
      const predictor = filter === 0 ? 0 : filter === 1 ? a : filter === 2 ? b : filter === 3 ? Math.floor((a + b) / 2)
        : filter === 4 ? (pa <= pb && pa <= pc ? a : pb <= pc ? b : c) : NaN;
      assert.ok(Number.isFinite(predictor));
      row[x] = (raw[y * (stride + 1) + 1 + x] + predictor) & 255;
    }
    for (let x = 0; x < stride; x += channels) {
      const [r, g, b] = row.subarray(x, x + 3);
      if (g > 60 && g > r * 1.3 && g > b * 1.03) count++;
    }
    previous = row;
  }
  return count;
}

export async function verifyWorkspaceBrowser({ baseURL, accounts, richEntries }) {
  // A separate headless browser profile never touches the user's open tab or
  // cookies. The HTTP fixture owns and cleans all accounts used here.
  const browser = await chromium.launch({ channel: "msedge", headless: true,
    args: ["--enable-unsafe-swiftshader"] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors = [];
  let geometry = null;
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", async response => {
    if (!response.url().endsWith("/api/workspace/viewport") || !response.ok()) return;
    try { geometry = await response.json(); } catch { /* cancelled when the camera changes */ }
  });
  const login = async account => {
    await page.goto(`${baseURL}/login`);
    await page.getByLabel("邮箱", { exact: true }).fill(account.email);
    await page.getByLabel("密码", { exact: true }).fill(account.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page.waitForURL(`${baseURL}/`);
  };
  try {
    await login(accounts[1]);
    await expect(page.locator(".dataset-card")).toContainText(`${richEntries} 个唯一观测`, { timeout: 30_000 });
    await expect.poll(() => geometry?.paths.length ?? 0, { timeout: 45_000 }).toBeGreaterThan(0);
    await expect(page.locator(".maplibregl-canvas")).toBeVisible();
    await expect(page.locator(".workspace-error")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^静止事件/ })).toHaveAttribute("aria-pressed", "false");
    await page.getByRole("button", { name: "聚焦可见轨迹", exact: true }).click();
    await expect.poll(async () => {
      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("routefrom-workspace-view-v1")));
      return stored.state.mapView.zoom;
    }, { timeout: 20_000 }).toBeGreaterThan(12);
    const canvas = page.locator(".maplibregl-canvas");
    await expect.poll(async () => mintPixels(await canvas.screenshot()), { timeout: 20_000 }).toBeGreaterThan(20);
    const visiblePixels = mintPixels(await canvas.screenshot());
    const track = page.getByRole("button", { name: /^观测轨迹/ });
    await track.click();
    await expect(track).toHaveAttribute("aria-pressed", "false");
    await expect.poll(async () => mintPixels(await canvas.screenshot()), { timeout: 10_000 }).toBeLessThan(visiblePixels / 2);
    await track.click();
    await expect(track).toHaveAttribute("aria-pressed", "true");
    await expect.poll(async () => mintPixels(await canvas.screenshot()), { timeout: 10_000 }).toBeGreaterThan(visiblePixels / 2);
    const directory = path.resolve("../artifacts");
    await mkdir(directory, { recursive: true });
    await page.screenshot({ path: path.join(directory, "personal-workspace-qa.png"), fullPage: true });

    const previousOwner = await page.evaluate(() => JSON.parse(localStorage.getItem("routefrom-workspace-view-v1")).state.ownerId);
    await page.getByRole("button", { name: "管理账号与足迹", exact: true }).click();
    await page.getByRole("button", { name: "退出登录", exact: true }).click();
    await page.waitForURL(/\/login/);
    await login(accounts[0]);
    await expect(page.locator(".dataset-card")).toContainText("3 个唯一观测", { timeout: 30_000 });
    const state = await page.evaluate(() => JSON.parse(localStorage.getItem("routefrom-workspace-view-v1")).state);
    assert.notEqual(state.ownerId, previousOwner);
    assert.deepEqual(state.placeNotes, {});
    await expect(page.locator(".workspace-error")).toHaveCount(0);
    assert.deepEqual(errors, [], "no React, MapLibre or worker runtime errors");
  } catch (error) {
    const directory = path.resolve("../artifacts");
    await mkdir(directory, { recursive: true });
    await page.screenshot({ path: path.join(directory, "personal-workspace-qa-failed.png"), fullPage: true });
    console.error("Browser workspace errors:", await page.locator(".workspace-error").allTextContents(), errors);
    throw error;
  } finally { await context.close(); await browser.close(); }
}
