// Drives the phone page in headless Chrome over the DevTools protocol (no extra package):
// pairing screen, projects, the chat with a change, Open the change, Accept, Go. Prints one
// JSON line of what it saw; screenshots go to OUT. Run by tests/remote_page/browse.py.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [chrome, base, token, out, scheme] = process.argv.slice(2);
const debugPort = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), "joinery-chrome-"));
const browser = spawn(chrome, [
  "--headless=new", `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars",
  // Ubuntu 24.04 forbids the user namespaces Chrome's sandbox needs; this throwaway
  // browser only ever opens the local test server.
  ...(process.platform === "linux" ? ["--no-sandbox"] : []),
  "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const seen = { checks: [] };
const check = (ok, what) => seen.checks.push({ ok: !!ok, what });

async function target() {
  for (let i = 0; i < 300; i += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
      const page = list.find((entry) => entry.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch (error) { /* not up yet */ }
    await sleep(100);
  }
  throw new Error("Chrome didn't start");
}

let socket;
let next = 0;
const waiting = new Map();
function send(method, params = {}) {
  const id = ++next;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((done, fail) => waiting.set(id, { done, fail }));
}

async function evaluate(expression) {
  const answer = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (answer.exceptionDetails) throw new Error(JSON.stringify(answer.exceptionDetails));
  return answer.result.value;
}

async function until(expression, ms = 8000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await evaluate(expression)) return true;
    await sleep(100);
  }
  return false;
}

async function shot(name) {
  await sleep(350);
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(out, `remote-${name}-${scheme}.png`), Buffer.from(data, "base64"));
}

async function main() {
  socket = new WebSocket(await target());
  await new Promise((done) => socket.addEventListener("open", done));
  socket.addEventListener("message", (message) => {
    const data = JSON.parse(message.data);
    const call = waiting.get(data.id);
    if (!call) return;
    waiting.delete(data.id);
    if (data.error) call.fail(new Error(data.error.message));
    else call.done(data.result);
  });
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });

  // Not paired yet: the pairing screen, with the code from the link.
  await send("Page.navigate", { url: `${base}/#pair=ABCDEFGHJK` });
  check(await until(`document.querySelector("#pair-code")?.value === "ABCDEFGHJK"`), "The pairing screen takes the code from the link");
  await shot("pair");

  await send("Network.enable");
  await send("Network.setCookie", { name: "joinery_remote", value: token, url: base, httpOnly: true, secure: true, sameSite: "Strict" });
  await send("Page.navigate", { url: `${base}/` });
  check(await until(`document.querySelectorAll(".row").length === 1`), "The open project is listed");
  check(await evaluate(`document.querySelector(".row-sub").textContent.includes("change waits")`), "It says a change waits");
  await shot("projects");
  await evaluate(`[...document.querySelectorAll("button")].find((b) => b.textContent === "Settings").click()`);
  check(await until(`!!document.querySelector("dialog[open] #push")`), "Settings offers notifications");
  await shot("settings");
  await evaluate(`document.querySelector("dialog[open]").close()`);

  await evaluate(`document.querySelector(".row").click()`);
  check(await until(`!!document.querySelector(".change")`), "The change card shows");
  check(await evaluate(`[...document.querySelectorAll(".you p")].some((p) => p.textContent === "Make the hedge taller")`), "The request is in the transcript");
  check(await evaluate(`!!document.querySelector(".compare img.after")`), "Before and after compare");
  check(await until(`document.querySelector(".compare img.after").complete && document.querySelector(".compare img.after").naturalWidth > 0`), "The after picture loads by change and index");
  check(await evaluate(`[...document.querySelectorAll(".change button")].some((b) => b.textContent === "Open the change") && ![...document.querySelectorAll(".change button")].some((b) => b.textContent === "Accept")`), "Accept waits until the change with notes is opened");
  check(await evaluate(`!!document.querySelector(".todo") && [...document.querySelectorAll(".todo button")].some((b) => b.textContent === "Go")`), "The to-do offers Go");
  // Typing is never interrupted by a redraw.
  await evaluate(`(() => { const box = document.querySelector("#message"); box.focus(); box.value = "draft"; box.dispatchEvent(new Event("input")); })()`);
  await sleep(1500);
  check(await evaluate(`document.activeElement?.id === "message" && document.querySelector("#message").value === "draft"`), "A redraw keeps the message box and its text");
  await evaluate(`(() => { const box = document.querySelector("#message"); box.value = ""; box.dispatchEvent(new Event("input")); box.blur(); })()`);
  await shot("chat");

  await evaluate(`[...document.querySelectorAll(".change button")].find((b) => b.textContent === "Open the change").click()`);
  check(await until(`[...document.querySelectorAll(".change button")].some((b) => b.textContent === "Accept")`), "Opened, Accept is offered");
  check(await evaluate(`!!document.querySelector(".diff .add")`), "The changed lines show");
  await shot("change");
  await evaluate(`[...document.querySelectorAll(".change button")].find((b) => b.textContent === "Accept").click()`);
  check(await until(`!document.querySelector(".change")`), "Accept settles the card");
  await evaluate(`[...document.querySelectorAll(".todo button")].find((b) => b.textContent === "Go").click()`);
  check(await until(`!document.querySelector(".todo")`), "Go starts the to-do");
  await shot("after");
  seen.errors = await evaluate(`window.__errors || []`);
}

main()
  .catch((error) => { seen.error = String(error.stack || error); })
  .finally(() => {
    process.stdout.write(JSON.stringify(seen) + "\n");
    browser.kill();
    setTimeout(() => { rmSync(profile, { recursive: true, force: true }); process.exit(0); }, 300);
  });
