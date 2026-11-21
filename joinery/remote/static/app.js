// Joinery Remote: the smallest page that drives the API (ADR 0093). Text only goes in
// through textContent, never as HTML.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { project: null, seq: 0, started: 0, source: null, change: null, todo: null };

async function api(path, body) {
  const options = body === undefined
    ? { credentials: "same-origin" }
    : {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Joinery-Remote": "1" },
        body: JSON.stringify(body),
      };
  const answer = await fetch(path, options);
  const data = await answer.json().catch(() => ({}));
  return { status: answer.status, data };
}

function show(id) {
  for (const section of ["pair", "projects", "chat"]) $(section).hidden = section !== id;
}

function say(id, text) { $(id).textContent = text || ""; }

// --- Pairing ---------------------------------------------------------------------------

function startPairing() {
  const code = new URLSearchParams(location.hash.slice(1)).get("pair") || "";
  $("pair-code").value = code;
  $("pair-name").value = guessName();
  show("pair");
}

function guessName() {
  const agent = navigator.userAgent;
  if (/iPhone/.test(agent)) return "iPhone";
  if (/iPad/.test(agent)) return "iPad";
  if (/Android/.test(agent)) return "Android phone";
  return "Browser";
}

$("pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  say("pair-status", "Asking the machine…");
  const { status, data } = await api("/api/pair", {
    code: $("pair-code").value,
    name: $("pair-name").value,
    platform: navigator.platform || "",
  });
  if (status !== 200) return say("pair-status", data.error || "Couldn't pair.");
  say("pair-status", "Confirm on your Mac: Settings → Remote → Pair.");
  for (;;) {
    await new Promise((done) => setTimeout(done, 2000));
    const poll = await api("/api/pair/status", { request_id: data.request_id, secret: data.secret });
    const answer = poll.data.state;
    if (answer === "allowed") { history.replaceState(null, "", "/"); return loadProjects(); }
    if (answer === "denied") return say("pair-status", "The machine said no.");
    if (answer === "expired" || answer === "unknown") return say("pair-status", "The request expired. Make a new code.");
  }
});

// --- Projects --------------------------------------------------------------------------

async function loadProjects() {
  const { status, data } = await api("/api/projects");
  if (status === 401) return startPairing();
  const list = $("project-list");
  list.replaceChildren();
  for (const project of data.projects || []) {
    const row = document.createElement("li");
    const parts = [project.name, project.working ? "working" : "", project.waiting ? "change waiting" : ""];
    row.textContent = parts.filter(Boolean).join(" · ");
    row.tabIndex = 0;
    row.addEventListener("click", () => openChat(project));
    list.append(row);
  }
  $("projects-empty").hidden = (data.projects || []).length > 0;
  show("projects");
}

$("back").addEventListener("click", () => { closeStream(); loadProjects(); });

// --- Chat ------------------------------------------------------------------------------

function openChat(project) {
  closeStream();
  state.project = project;
  state.seq = 0;
  state.started = project.started;
  $("log").replaceChildren();
  say("chat-title", project.name);
  setChange(null);
  setTodo(null);
  show("chat");
  connect();
}

function closeStream() {
  if (state.source) state.source.close();
  state.source = null;
}

function connect() {
  const path = `/api/projects/${state.project.id}/events?since=${state.seq}&started=${state.started}`;
  const source = new EventSource(path);
  state.source = source;
  say("link-state", "Connecting…");
  source.addEventListener("hello", (message) => {
    const hello = JSON.parse(message.data);
    state.started = hello.started;
    say("link-state", hello.editor ? "Editor open" : "No editor");
  });
  source.addEventListener("event", (message) => {
    state.seq = Number(message.lastEventId) || state.seq;
    take(JSON.parse(message.data));
  });
  source.addEventListener("notice", (message) => say("chat-status", JSON.parse(message.data).text));
  source.addEventListener("gone", () => { say("link-state", "Disconnected"); closeStream(); });
  source.onerror = () => say("link-state", "Asleep or offline. Reconnecting…");
}

function line(text, kind) {
  if (!text) return;
  const item = document.createElement("li");
  item.textContent = text;
  if (kind) item.className = kind;
  $("log").append(item);
  item.scrollIntoView({ block: "end" });
}

function take(event) {
  switch (event.kind) {
    case "chat_started": case "user_message": line(event.text, "you"); break;
    case "message": line(event.text, event.level === "warn" ? "warn" : ""); break;
    case "progress": line(event.text, "step"); break;
    case "file_preview": setChange(event); break;
    case "file_decision": if (state.change && state.change.transaction_id === event.transaction_id) setChange(null);
      line(event.accepted ? "Change accepted" : "Discarded"); break;
    case "todo": setTodo(event.state === "waiting" ? event : null); break;
    case "art_gate": line("Waiting for the art direction to be approved at the editor (/art → Approve)."); break;
    default: break;
  }
}

function setChange(preview) {
  state.change = preview;
  $("change").hidden = !preview;
  if (!preview) return;
  say("change-summary", preview.summary);
  const media = $("change-media");
  media.replaceChildren();
  for (const item of preview.media || []) {
    for (const side of ["before", "after"]) {
      if (!item[side] || item.media !== "image") continue;
      const picture = document.createElement("img");
      picture.alt = `${item.path} ${side}`;
      picture.src = `/api/projects/${state.project.id}/${item[side]}`;
      media.append(picture);
    }
  }
}

$("change-open").addEventListener("click", async () => {
  const { data } = await api(`/api/projects/${state.project.id}/changes/${state.change.transaction_id}`);
  line((data.review_notes || []).map((note) => `${note.severity}: ${note.text}`).join("\n") || "No notes.");
});

async function act(action) {
  const { data } = await api(`/api/projects/${state.project.id}/actions`, action);
  say("chat-status", data.ok ? data.text || "" : data.text || data.error || "That didn't work.");
  return data;
}

$("change-accept").addEventListener("click", () => act({ kind: "decision", transaction_id: state.change.transaction_id, accept: true }));
$("change-discard").addEventListener("click", () => act({ kind: "decision", transaction_id: state.change.transaction_id, accept: false }));

function setTodo(todo) {
  state.todo = todo;
  $("todo").hidden = !todo;
  if (todo) say("todo-text", (todo.steps || []).map((step) => step.title || step).join("\n"));
}

$("todo-go").addEventListener("click", () => act({ kind: "todo_go", todo_id: state.todo.todo_id }));
$("todo-cancel").addEventListener("click", () => act({ kind: "todo_cancel", todo_id: state.todo.todo_id }));
$("stop").addEventListener("click", () => act({ kind: "cancel" }));

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("message").value.trim();
  if (!text) return;
  const answer = await act({ kind: "prompt", text });
  if (answer.ok) { $("message").value = ""; line(text, "you"); }
});

if (location.hash.includes("pair=")) startPairing();
else loadProjects();
