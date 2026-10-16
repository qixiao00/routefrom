import assert from "node:assert/strict";
import path from "node:path";
import { expect } from "@playwright/test";

export async function verifyCloudViewsBrowser({ browser, page, context, login, account, mintPixels }) {
  const sync = page.getByRole("button", { name: "地图视图与同步" });
  const track = page.getByRole("button", { name: /^观测轨迹/ });
  const openViews = async target => { await target.getByRole("button", { name: "地图视图与同步", exact: true }).click(); };
  const checkpoint = label => { if (process.env.ROUTEFROM_HTTP_VERBOSE === "true") console.log(`Cloud browser checkpoint: ${label}`); };
  // A transient database/network failure deliberately freezes automatic writes.
  // Exercise the user's explicit recovery, never bypass CAS or reset revisions.
  await expect.poll(async () => /视图已保存到账号|视图未同步|视图版本冲突/.test(await sync.innerText()), { timeout: 30_000 }).toBe(true);
  if ((await sync.innerText()).includes("视图未同步")) {
    await openViews(page);
    await page.getByRole("button", { name: "读取账号视图", exact: true }).click();
    await page.getByRole("button", { name: "保存当前本地视图", exact: true }).click();
    await page.keyboard.press("Escape");
    checkpoint("initial transient failure recovered through explicit account reread and local-view choice");
  }
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  checkpoint("initial account view ready");
  await page.getByRole("button", { name: "隐藏检查器", exact: true }).click();
  await page.getByRole("button", { name: "收起侧栏", exact: true }).click();
  await expect(page.locator(".layer-panel")).toBeHidden();
  await expect(page.locator(".inspector")).toBeHidden();
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  await page.reload();
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  await expect(page.locator(".layer-panel")).toBeHidden();
  await expect(page.locator(".inspector")).toBeHidden();
  await page.getByRole("button", { name: "展开侧栏", exact: true }).click();
  await expect(page.locator(".layer-panel")).toBeVisible();
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  await openViews(page);
  checkpoint("panel state survived reload");
  await page.getByLabel("为当前视图命名").fill("合成数据 · 跨设备通勤");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("button", { name: /^合成数据 · 跨设备通勤/ })).toBeVisible();
  checkpoint("named view saved");
  await page.screenshot({ path: path.resolve("../artifacts/cloud-map-views-qa.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await track.click();
  await expect(track).toHaveAttribute("aria-pressed", "false");
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  await page.reload();
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  await expect(track).toHaveAttribute("aria-pressed", "false");

  // New browser profile: no shared localStorage, same real account login.
  const secondContext = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
  const second = await secondContext.newPage();
  const errors = [];
  second.on("pageerror", error => errors.push(error.message));
  try {
    await login(account, second);
    await expect(second.getByRole("button", { name: "地图视图与同步" })).toContainText("视图已保存到账号", { timeout: 30_000 });
    await expect(second.getByRole("button", { name: /^观测轨迹/ })).toHaveAttribute("aria-pressed", "false");
    await expect(second.locator(".inspector")).toBeHidden();
    const state = await second.evaluate(() => JSON.parse(localStorage.getItem("routefrom-workspace-view-v1")).state);
    assert.ok(state.mapView.zoom > 12, `cloud restores camera in a new device profile (actual zoom ${state.mapView.zoom})`);
    await openViews(second);
    await second.getByRole("button", { name: /^合成数据 · 跨设备通勤/ }).click();
    await expect(second.getByRole("button", { name: /^观测轨迹/ })).toHaveAttribute("aria-pressed", "true");
    await expect(second.getByRole("button", { name: "地图视图与同步" })).toContainText("视图已保存到账号", { timeout: 30_000 });
    await expect.poll(async () => mintPixels(await second.locator(".maplibregl-canvas").screenshot()), { timeout: 20_000 }).toBeGreaterThan(20);
    checkpoint("new browser restored camera, layer, panels and named view");
    assert.deepEqual(errors, []);
  } finally { await secondContext.close(); }

  // First device's revision is now stale: it must not silently overwrite.
  await track.click();
  await expect(sync).toContainText("视图版本冲突", { timeout: 30_000 });
  await openViews(page);
  await page.getByRole("button", { name: "读取账号视图", exact: true }).click();
  await page.getByRole("button", { name: "恢复账号视图", exact: true }).click();
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  await expect(track).toHaveAttribute("aria-pressed", "true");
  checkpoint("concurrent device conflict resolved explicitly");
  await page.keyboard.press("Escape");

  // Failed save survives reload. Reconnecting offers a choice, not data loss.
  await context.setOffline(true);
  await track.click();
  await expect(sync).toContainText("视图未同步", { timeout: 30_000 });
  const offline = await page.evaluate(() => JSON.parse(localStorage.getItem("routefrom-workspace-view-v1")).state);
  assert.equal(offline.cloudViewPending, true);
  await context.setOffline(false);
  await page.reload();
  await expect(sync).toContainText("视图版本冲突", { timeout: 30_000 });
  await expect(track).toHaveAttribute("aria-pressed", "false");
  await openViews(page);
  await page.getByRole("button", { name: "读取账号视图", exact: true }).click();
  await page.getByRole("button", { name: "保存当前本地视图", exact: true }).click();
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  checkpoint("offline edits survived reload and were recovered");
  await page.getByRole("button", { name: /^合成数据 · 跨设备通勤/ }).click();
  await expect(track).toHaveAttribute("aria-pressed", "true");
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  await page.getByRole("button", { name: "查看全球足迹", exact: true }).click();
  await expect.poll(async () => (await page.evaluate(() => JSON.parse(localStorage.getItem("routefrom-workspace-view-v1")).state)).mapView.zoom).toBeLessThan(3);
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  await openViews(page);
  await page.getByRole("button", { name: /^合成数据 · 跨设备通勤/ }).click();
  await expect.poll(async () => (await page.evaluate(() => JSON.parse(localStorage.getItem("routefrom-workspace-view-v1")).state)).mapView.zoom).toBeGreaterThan(12);
  await expect.poll(async () => mintPixels(await page.locator(".maplibregl-canvas").screenshot()), { timeout: 20_000 }).toBeGreaterThan(20);
  await expect(sync).toContainText("视图已保存到账号", { timeout: 30_000 });
  checkpoint("named view moved the actual globe camera back from global zoom");
}
