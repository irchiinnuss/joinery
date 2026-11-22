// Joinery Remote's phone page (ADR 0093): pair, pick a project, follow its chat and direct it.
// Everything the phone may do is an action the server and the chat host check again.
import { Offline, Stream, get, post } from "./api.js";
import {
  apply, checksLine, clock, diffLines, emptyProject, facts, fromHistory, needsOpening,
  notesWorstFirst,
} from "./state.js";
import { button, compare, h, single, sounds, toast } from "./view.js";

const app = document.getElementById("app");
const ui = {
  me: null,
  project: null, // {id, name, agent, editor, ...}
  state: emptyProject(),
  stream: null,
  link: "connecting", // live | connecting | asleep | gone
  lastSeen: 0,
  editor: false,
  // A saved chat shown instead of the open one: {id, title, items}, or null.
  other: null,
  details: false,
  unseen: 0,
  draft: "",
  sending: false,
};
let frame = 0;

function render() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    if (ui.project) chatScreen();
  });
}

function screen(...children) {
  app.replaceChildren(...children);
}

// --- Pairing ---------------------------------------------------------------------------

function guessName() {
  const agent = navigator.userAgent;
  if (/iPhone/.test(agent)) return "iPhone";
  if (/iPad/.test(agent)) return "iPad";
  if (/Android/.test(agent)) return "Android phone";
  return "Browser";
}

function pairScreen(message = "") {
  const code = new URLSearchParams(location.hash.slice(1)).get("pair") || "";
  const status = h("p", { class: "status", role: "status", text: message });
  const codeInput = h("input", {
    id: "pair-code", name: "code", value: code, autocomplete: "one-time-code",
    autocapitalize: "characters", spellcheck: "false", required: true, maxlength: "11",
    class: "code", inputmode: "text",
  });
  const nameInput = h("input", { id: "pair-name", name: "name", value: guessName(), maxlength: "60", required: true });
  const pair = h("button", { type: "submit", class: "button primary wide" }, "Pair");
  const form = h("form", { class: "card stack", onsubmit: (event) => submitPair(event, codeInput, nameInput, pair, status) },
    h("label", { for: "pair-code", text: "Code" }), codeInput,
    h("label", { for: "pair-name", text: "This phone's name" }), nameInput,
    pair);
  screen(h("main", { class: "page narrow" },
    h("h1", { text: "Pair this phone" }),
    h("p", { class: "muted", text: "Use the code from Settings → Remote on your Mac. The Mac asks you before anything is paired." }),
    form, status));
}

async function submitPair(event, codeInput, nameInput, pair, status) {
  event.preventDefault();
  pair.disabled = true;
  status.textContent = "Asking the Mac…";
  let asked;
  try {
    asked = await post("/api/pair", {
      code: codeInput.value, name: nameInput.value, platform: platformName(),
    });
  } catch (error) {
    status.textContent = "The Mac can't be reached. Is Tailscale on?";
    pair.disabled = false;
    return;
  }
  if (!asked.ok) {
    status.textContent = asked.data.error || "Couldn't pair.";
    pair.disabled = false;
    return;
  }
  status.textContent = "Answer on your Mac: Settings → Remote → Pair.";
  const until = Date.now() + 5 * 60 * 1000;
  while (Date.now() < until) {
    await new Promise((done) => setTimeout(done, 1500));
    let poll;
    try {
      poll = await post("/api/pair/status", { request_id: asked.data.request_id, secret: asked.data.secret });
    } catch (error) {
      continue;
    }
    const answer = poll.data.state;
    if (answer === "allowed") {
      history.replaceState(null, "", "/");
      toast("Paired.", "success");
      return projectsScreen();
    }
    if (answer === "denied") {
      status.textContent = "The Mac said no.";
      pair.disabled = false;
      return;
    }
    if (answer === "expired" || answer === "unknown") break;
  }
  status.textContent = "That request expired. Make a new code on the Mac.";
  pair.disabled = false;
}

function platformName() {
  const agent = navigator.userAgent;
  if (/iPhone|iPad/.test(agent)) return "iOS";
  if (/Android/.test(agent)) return "Android";
  if (/Mac OS X/.test(agent)) return "macOS";
  return navigator.platform || "";
}

// --- Projects --------------------------------------------------------------------------

async function projectsScreen() {
  closeProject();
  screen(h("main", { class: "page" }, h("h1", { text: "Projects" }), skeleton(3)));
  let answer;
  try {
    [answer, ui.me] = await Promise.all([get("/api/projects"), get("/api/me").then((me) => me.data)]);
  } catch (error) {
    return asleepScreen();
  }
  if (answer.status === 401) return pairScreen();
  const projects = answer.data.projects || [];
  const rows = projects.map((project, index) => h("li", { style: `--i:${index}` },
    h("button", {
      type: "button", class: `row ${project.open ? "" : "closed"}`,
      onclick: () => (project.open ? openProject(project) : wakeProject(project)),
      disabled: !project.open && !project.can_open,
    },
      h("span", { class: `dot ${project.waiting ? "waiting" : project.working ? "working" : "idle"}`, "aria-hidden": "true" }),
      h("span", { class: "row-main" },
        h("span", { class: "row-title", text: project.name }),
        h("span", { class: "row-sub", text: projectLine(project) })),
      h("span", { class: "chevron", "aria-hidden": "true", text: "›" }))));
  screen(h("main", { class: "page" },
    h("div", { class: "title-row" }, h("h1", { text: "Projects" }), button("Settings", settingsSheet, "quiet", { "aria-haspopup": "dialog" })),
    projects.length
      ? h("ul", { class: "list" }, rows)
      : h("div", { class: "empty" },
        h("p", { text: "No Joinery project is open on your Mac." }),
        h("p", { class: "muted", text: "Open one in Joinery, then come back." }),
        button("Look again", projectsScreen))));
}

function projectLine(project) {
  if (!project.open) return project.can_open ? "Closed · tap to open it without the editor" : "Closed · open it in Joinery on your Mac";
  const parts = [];
  if (project.waiting) parts.push(project.waiting === 1 ? "A change waits" : `${project.waiting} changes wait`);
  else if (project.working) parts.push("Working");
  if (project.todo) parts.push("To-do waits for Go");
  if (!parts.length) parts.push(project.editor ? "Open in the editor" : "Running without the editor");
  return parts.join(" · ");
}

// A project no editor has open: the always-on helper starts its chat.
async function wakeProject(project) {
  toast(`Opening ${project.name}…`);
  let answer;
  try {
    answer = await post(`/api/projects/${project.id}/open`, {});
  } catch (error) {
    return asleepScreen();
  }
  if (!answer.ok || !answer.data.ok) {
    toast(answer.data.text || answer.data.error || "It couldn't be opened.", "warn");
    return;
  }
  openProject({ ...project, ...answer.data.project, open: true });
}

// --- Settings: notifications ------------------------------------------------------------

function keyBytes(text) {
  const padded = (text + "===".slice((text.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (ch) => ch.charCodeAt(0));
}

async function settingsSheet() {
  const sheet = h("dialog", { class: "sheet", "aria-label": "Settings" });
  const close = () => { sheet.close(); sheet.remove(); };
  sheet.addEventListener("close", () => sheet.remove());
  const status = h("p", { class: "muted small", role: "status" });
  const toggle = h("input", { type: "checkbox", role: "switch", id: "push", class: "switch" });
  const can = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  sheet.append(
    h("div", { class: "sheet-head" }, h("h2", { text: "Settings" }), button("Done", close, "quiet")),
    h("div", { class: "card stack" },
      h("label", { class: "switch-row", for: "push" },
        h("span", { class: "row-main" }, h("span", { class: "row-title", text: "Notifications" }),
          h("span", { class: "row-sub", text: "When a change waits, Journeyman needs you, or something goes wrong" })),
        toggle),
      status),
    h("div", { class: "card stack" },
      h("p", { class: "row-title", text: (ui.me && ui.me.name) || "This phone" }),
      h("p", { class: "muted small", text: "To unpair it, use Revoke in Settings → Remote on your Mac." })));
  document.body.append(sheet);
  sheet.showModal();
  if (!can) {
    toggle.disabled = true;
    status.textContent = /iPhone|iPad/.test(navigator.userAgent) && !standalone
      ? "Add Joinery to your Home Screen first (Share → Add to Home Screen), then open it from there."
      : "This browser can't show notifications.";
    return;
  }
  const info = await get("/api/push").catch(() => null);
  toggle.checked = !!(info && info.data.subscribed && Notification.permission === "granted");
  toggle.addEventListener("change", async () => {
    toggle.disabled = true;
    try {
      if (toggle.checked) await pushOn(info.data.public_key, status);
      else await pushOff(status);
    } catch (error) {
      status.textContent = String(error.message || error);
      toggle.checked = !toggle.checked;
    }
    toggle.disabled = false;
  });
}

async function pushOn(key, status) {
  if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications are off for Joinery in this phone's settings.");
  const registration = await navigator.serviceWorker.ready;
  const subscription = (await registration.pushManager.getSubscription())
    || (await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) }));
  const answer = await post("/api/push/subscribe", { subscription: subscription.toJSON() });
  if (!answer.ok) throw new Error(answer.data.text || "Your Mac didn't take the subscription.");
  status.textContent = "On. Nothing comes while you're at the editor.";
}

async function pushOff(status) {
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  if (subscription) {
    await post("/api/push/unsubscribe", { endpoint: subscription.endpoint });
    await subscription.unsubscribe();
  }
  status.textContent = "Off.";
}

function asleepScreen() {
  screen(h("main", { class: "page narrow" },
    h("h1", { text: "Your Mac can't be reached" }),
    h("p", { class: "muted", text: "It may be asleep or offline, or Tailscale is off on this phone." }),
    button("Try again", projectsScreen, "primary")));
}

function skeleton(lines) {
  return h("div", { class: "skeleton", "aria-busy": "true", "aria-label": "Loading" },
    ...Array.from({ length: lines }, () => h("div", { class: "bar" })));
}

// --- A project's chat ------------------------------------------------------------------

function openProject(project) {
  closeProject();
  ui.project = project;
  ui.state = emptyProject();
  ui.other = null;
  ui.draft = "";
  ui.details = false;
  ui.link = "connecting";
  ui.editor = project.editor;
  location.hash = `p=${project.id}`;
  ui.stream = new Stream(project.id, {
    hello: (hello) => {
      ui.link = "live";
      ui.editor = hello.editor;
      ui.lastSeen = Date.now() / 1000;
      if (hello.gap) loadOpenChatHistory();
      render();
    },
    event: (event, catchingUp) => {
      const alerts = ui.state.alerts.length;
      apply(ui.state, event);
      ui.lastSeen = Date.now() / 1000;
      if (ui.state.alerts.length > alerts && !catchingUp) notify(ui.state.alerts[ui.state.alerts.length - 1]);
      render();
    },
    notice: () => {},
    gone: (reason) => {
      ui.link = reason === "revoked" ? "revoked" : "gone";
      if (reason === "revoked") return pairScreen("This phone was unpaired at the Mac.");
      render();
    },
    down: () => {
      ui.link = "asleep";
      render();
    },
  });
  ui.stream.open();
  chatScreen();
}

function closeProject() {
  if (ui.stream) ui.stream.close();
  ui.stream = null;
  ui.project = null;
}

async function loadOpenChatHistory() {
  if (!ui.state.chatId) return;
  const answer = await get(`/api/projects/${ui.project.id}/chats/${ui.state.chatId}`).catch(() => null);
  if (answer && answer.ok && !ui.state.items.length) {
    ui.state.items = fromHistory(answer.data.turns);
    render();
  }
}

function notify(alert) {
  if (document.hidden) {
    ui.unseen += 1;
    document.title = `(${ui.unseen}) Joinery Remote`;
  } else {
    toast(alert.text, alert.kind === "change_ready" ? "accent" : "info");
  }
  if (alert.kind !== "finished" && navigator.vibrate) navigator.vibrate(30);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    ui.unseen = 0;
    document.title = "Joinery Remote";
  }
});

function linkLine() {
  if (ui.link === "live") return ui.editor ? "Editor open" : "Running without the editor";
  if (ui.link === "connecting") return "Connecting…";
  if (ui.link === "gone") return "The chat closed on the Mac";
  const seen = ui.lastSeen ? ` · last seen ${clock(ui.lastSeen)}` : "";
  return `Asleep or offline${seen}. Reconnecting…`;
}

// The chat's layout is made once per project; each event redraws the header and the body
// but never the message box, so typing, the cursor and the keyboard stay where they are.
const layout = { project: "", header: null, body: null, composer: null };

function chatScreen() {
  const project = ui.project;
  if (layout.project !== project.id || !app.contains(layout.body)) {
    layout.project = project.id;
    layout.header = h("header", { class: "topbar" });
    layout.body = h("main", { class: "chat", id: "chat" });
    layout.composer = composer();
    app.replaceChildren(layout.header, layout.body, layout.composer.form);
  }
  const state = ui.state;
  const viewing = ui.other;
  const body = layout.body;
  const pinned = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
  layout.header.replaceChildren(
    h("button", { type: "button", class: "icon", "aria-label": "Projects", onclick: projectsScreen, text: "‹" }),
    h("div", { class: "topbar-title" },
      h("h1", { text: viewing ? viewing.title || "Chat" : project.name }),
      h("p", { class: `link ${ui.link}`, role: "status", text: viewing ? `${project.name} · saved chat` : linkLine() })),
    button("Chats", chatsSheet, "quiet", { "aria-haspopup": "dialog" }));
  const parts = [];
  if (state.stop && !viewing) parts.push(stopBanner(state.stop));
  if (state.artGate && !viewing) {
    parts.push(h("div", { class: "notice", role: "note", text: "Waiting for the art direction to be approved at the editor (/art → Approve)." }));
  }
  const items = viewing ? viewing.items : state.items;
  const log = h("ol", { class: "log" });
  items.forEach((item) => log.append(itemView(item)));
  if (!items.length) log.append(h("li", { class: "empty-log", text: viewing ? "Nothing in this chat yet." : "Ask Journeyman something. It works on your Mac, and every change waits for you." }));
  parts.push(log);
  if (!viewing) {
    if (state.working) parts.push(h("div", { class: "working", role: "status" }, h("span", { class: "pulse", "aria-hidden": "true" }), h("span", { text: state.step || "Working…" })));
    for (const task of Object.values(state.tasks)) parts.push(taskCard(task));
    if (state.offer) parts.push(offerCard(state.offer));
    if (state.todo) parts.push(todoCard(state.todo));
    if (state.change) parts.push(changeCard(state.change));
  }
  body.replaceChildren(...parts);
  layout.composer.sync();
  if (pinned) body.scrollTop = body.scrollHeight;
}

function itemView(item) {
  if (item.type === "you") return h("li", { class: "you" }, h("p", { text: item.text }));
  if (item.type === "reply") return h("li", { class: "reply", text: item.text });
  if (item.type === "step") return h("li", { class: "step", text: item.text });
  if (item.type === "decision") return h("li", { class: `decision ${item.accepted ? "kept" : "dropped"}`, text: item.text });
  return h("li", { class: `notice-line ${item.level}`, text: item.text });
}

function stopBanner(text) {
  return h("section", { class: "banner danger", role: "alert" },
    h("h2", { text: "Stopped by project policy" }),
    h("p", { text: text.replace(/^Stopped by project policy:\s*/, "") }),
    h("p", { class: "muted", text: "Agree a tool-based approach before continuing." }),
    button("I've read it", () => { ui.state.stop = ""; render(); }, "secondary"));
}

function taskCard(task) {
  const stop = task.state === "working" || task.state === "waiting"
    ? button("Stop", () => act({ kind: "cancel", task_id: task.task_id }), "quiet", { "aria-label": `Stop ${task.title}` })
    : null;
  return h("section", { class: "card task" },
    h("div", { class: "task-head" }, h("span", { class: `dot ${task.state === "working" ? "working" : "idle"}`, "aria-hidden": "true" }),
      h("span", { class: "task-title", text: task.title || "Task" }), stop),
    task.step ? h("p", { class: "muted small", text: task.step }) : null);
}

function offerCard(offer) {
  return h("section", { class: "card stack" },
    h("p", { text: offer.message || "Journeyman ran out of quota." }),
    h("div", { class: "actions" },
      button("Not now", () => act({ kind: "provider_switch", agent: "" }), "secondary"),
      button(`Continue with ${offer.to_name || offer.to}`, () => act({ kind: "provider_switch", agent: offer.to }), "primary")));
}

function todoCard(todo) {
  const steps = (todo.steps || []).map((step, index) => h("li", { class: `todo-step ${(todo.step_states || [])[index] || ""}`, text: typeof step === "string" ? step : step.title || "" }));
  const checks = (todo.done_when || []).map((item) => h("li", { class: `check ${item.status || ""}`, text: item.text }));
  const waiting = todo.state === "waiting";
  return h("section", { class: "card stack todo", "aria-label": "To-do" },
    h("h2", { text: waiting ? "Journeyman's plan" : "Working through the plan" }),
    h("ol", { class: "steps" }, steps),
    checks.length ? h("div", {}, h("h3", { text: "Done when" }), h("ul", { class: "checks" }, checks)) : null,
    waiting ? h("div", { class: "actions" },
      button("Cancel", () => act({ kind: "todo_cancel", todo_id: todo.todo_id }), "secondary"),
      button("Go", () => act({ kind: "todo_go", todo_id: todo.todo_id }), "primary")) : null);
}

function changeCard(change) {
  const tx = change.transaction_id;
  const mustOpen = needsOpening(change) && !ui.state.opened.has(tx);
  const lines = facts(change);
  const checks = checksLine(change);
  const notes = notesWorstFirst(change);
  const card = h("section", { class: "card stack change", "aria-label": "Change waiting for review" },
    h("p", { class: "eyebrow", text: "Waiting for you" }),
    h("h2", { text: change.summary || "Changes" }),
    lines.length ? h("ul", { class: "facts" }, lines.map((fact) => h("li", { text: fact }))) : null,
    checks ? h("p", { class: `checks-line ${(change.failed_checks || []).length ? "warn" : ""}`, text: checks }) : null,
    notes.length ? h("ul", { class: "notes" }, notes.slice(0, ui.details ? notes.length : 2).map((note) => h("li", { class: note.severity, text: note.text }))) : null,
    mediaView(change));
  if (ui.details) card.append(detailsView(change));
  const accept = mustOpen
    ? button("Open the change", () => openChange(tx), "primary")
    : button("Accept", () => decide(tx, true), "primary");
  card.append(h("div", { class: "actions" },
    button("Discard", () => decide(tx, false), "secondary"),
    !mustOpen && !ui.details ? button("Details", () => openChange(tx), "quiet") : null,
    accept));
  return card;
}

function mediaView(change) {
  if (change.media_pending && !(change.media || []).some((item) => item.after || item.before)) {
    return h("div", { class: "media-pending" }, skeleton(1), h("p", { class: "muted small", text: "Rendering the before and after…" }));
  }
  const base = `/api/projects/${ui.project.id}/`;
  const shown = [];
  for (const item of change.media || []) {
    const label = item.label || item.path || "Change";
    const before = item.before ? base + item.before : "";
    const after = item.after ? base + item.after : "";
    if (!before && !after) continue;
    if (item.media === "audio") shown.push(sounds(before, after, label));
    else if (before && after) shown.push(compare(before, after, label));
    else shown.push(single(after || before, label, after ? "after" : "before"));
  }
  return shown.length ? h("div", { class: "media" }, shown) : null;
}

function detailsView(change) {
  const parts = [];
  for (const result of change.done_when || []) {
    parts.push(h("li", { class: `check ${result.status}`, text: `${result.text}${result.evidence ? ` — ${result.evidence}` : ""}` }));
  }
  const diff = diffLines(change.diff);
  return h("div", { class: "details stack" },
    (change.not_as_asked || []).length ? h("div", {}, h("h3", { text: "Not as asked" }), h("ul", {}, change.not_as_asked.map((line) => h("li", { text: line })))) : null,
    (change.failed_checks || []).length ? h("div", {}, h("h3", { text: "Checks that failed" }), h("ul", {}, change.failed_checks.map((problem) => h("li", { text: problem.text || problem.message || JSON.stringify(problem) })))) : null,
    parts.length ? h("div", {}, h("h3", { text: "Done when" }), h("ul", { class: "checks" }, parts)) : null,
    (change.changes || []).length ? h("div", {}, h("h3", { text: "Files" }), h("ul", { class: "files" }, change.changes.map((file) => h("li", { text: `${file.path}` })))) : null,
    diff.length ? h("pre", { class: "diff", tabindex: "0", "aria-label": "Changed lines" }, diff.map((line) => h("span", { class: line.kind, text: line.text + "\n" }))) : null);
}

async function openChange(tx) {
  // A POST: opening is what lets Accept through, so no GET may do it.
  const answer = await post(`/api/projects/${ui.project.id}/changes/${tx}/open`, {}).catch(() => null);
  if (!answer || !answer.ok) {
    toast((answer && answer.data.error) || "That change isn't waiting any more.", "warn");
    return;
  }
  ui.state.opened.add(tx);
  if (ui.state.change && ui.state.change.transaction_id === tx) ui.state.change = { ...ui.state.change, ...answer.data };
  ui.details = true;
  render();
}

async function decide(tx, accept) {
  const done = await act({ kind: "decision", transaction_id: tx, accept });
  if (done && done.ok) ui.details = false;
}

// --- Composer --------------------------------------------------------------------------

function composer() {
  const input = h("textarea", {
    id: "message", rows: "1", "aria-label": "Message", enterkeyhint: "send",
  });
  // Send takes the accent only once there is something to send: one call to action at a time.
  const send = h("button", { type: "submit", class: "button send", "aria-label": "Send" }, "Send");
  input.addEventListener("input", () => {
    ui.draft = input.value;
    send.classList.toggle("primary", !!ui.draft.trim());
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  });

  const stop = button("Stop", () => act({ kind: "cancel" }), "stop", { "aria-label": "Stop Journeyman" });
  const form = h("form", { class: "composer", onsubmit: (event) => { event.preventDefault(); sendDraft(); } },
    input, stop, send);
  return {
    form,
    sync() {
      if (input.value !== ui.draft) input.value = ui.draft;
      input.placeholder = placeholder();
      stop.hidden = !(ui.state.working && !ui.other);
      send.disabled = ui.sending;
      send.classList.toggle("primary", !!ui.draft.trim());
    },
  };
}

function placeholder() {
  if (ui.link === "asleep") return "Write now; send when your Mac is back";
  if (ui.other) return `Message “${ui.other.title || "this chat"}”`;
  return ui.state.working ? "It queues until Journeyman finishes" : "Ask Journeyman";
}

async function sendDraft() {
  const text = ui.draft.trim();
  if (!text || ui.sending) return;
  const action = ui.other && ui.other.id !== ui.state.chatId
    ? { kind: "chat_task", text, chat_id: ui.other.id }
    : { kind: "prompt", text };
  ui.sending = true;
  render();
  const done = await act(action);
  ui.sending = false;
  // Pending: it is the editor's to send now, and the outcome shows in the chat.
  if (done && (done.ok || done.pending)) {
    ui.draft = "";
    if (ui.other) ui.other.items.push({ type: "you", text });
  }
  render();
}

async function act(action) {
  let answer;
  try {
    answer = await post(`/api/projects/${ui.project.id}/actions`, action);
  } catch (error) {
    if (error instanceof Offline) {
      toast("Not sent: your Mac is asleep or offline. Send it again when it's back.", "warn");
      return null;
    }
    throw error;
  }
  if (answer.status === 401) {
    pairScreen("This phone was unpaired at the Mac.");
    return null;
  }
  const text = answer.data.text || answer.data.error || "";
  if (answer.data.pending) toast(text || "Waiting for your Mac.", "info");
  else if (!answer.ok || !answer.data.ok) toast(text || "That didn't work.", "warn");
  else if (text && text !== "Sent." && text !== "Done.") toast(text, "info");
  return answer.data;
}

// --- Saved chats -----------------------------------------------------------------------

async function chatsSheet() {
  const sheet = h("dialog", { class: "sheet", "aria-label": "Chats" });
  const close = () => { sheet.close(); sheet.remove(); };
  sheet.addEventListener("close", () => sheet.remove());
  const list = h("ul", { class: "list" }, h("li", {}, skeleton(3)));
  sheet.append(h("div", { class: "sheet-head" }, h("h2", { text: "Chats" }), button("Close", close, "quiet")), list);
  document.body.append(sheet);
  sheet.showModal();
  const answer = await get(`/api/projects/${ui.project.id}/chats`).catch(() => null);
  const chats = (answer && answer.ok && answer.data.chats) || [];
  const rows = [h("li", {}, h("button", { type: "button", class: "row", onclick: () => { ui.other = null; close(); render(); } },
    h("span", { class: "row-main" }, h("span", { class: "row-title", text: "The chat open in the editor" }), h("span", { class: "row-sub", text: "Live" }))))];
  chats.forEach((chat, index) => rows.push(h("li", { style: `--i:${index}` }, h("button", { type: "button", class: "row", onclick: () => { close(); openSaved(chat); } },
    h("span", { class: "row-main" }, h("span", { class: "row-title", text: chat.title || "Untitled chat" }),
      h("span", { class: "row-sub tabular", text: `${chat.turns} ${chat.turns === 1 ? "request" : "requests"}` + (chat.id === ui.state.chatId ? " · open in the editor" : "") }))))));
  list.replaceChildren(...rows);
}

async function openSaved(chat) {
  if (chat.id === ui.state.chatId) {
    ui.other = null;
    return render();
  }
  ui.other = { id: chat.id, title: chat.title, items: [] };
  render();
  const answer = await get(`/api/projects/${ui.project.id}/chats/${chat.id}`).catch(() => null);
  if (answer && answer.ok && ui.other && ui.other.id === chat.id) {
    ui.other.items = fromHistory(answer.data.turns);
    render();
  }
}

// --- Start -----------------------------------------------------------------------------

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
if (location.hash.includes("pair=")) pairScreen();
else projectsScreen();
