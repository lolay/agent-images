import { chromium } from "playwright";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
await page.goto("https://example.com", { waitUntil: "load", timeout: 60000 });
await page.screenshot({ path: process.argv[2], fullPage: true });
console.log("title:", await page.title());
await browser.close();
