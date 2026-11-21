// What the phone knows of one project, built from the host's events (ADR 0093). Pure: no
// DOM and no network, so it is tested on its own (tests/remote_page/state.test.mjs).

export const POLICY_STOP = "Stopped by project policy";
const DONE_STATES = new Set(["dropped", "cancelled", "done", "passed", "failed"]);
const TASK_ENDS = new Set(["accepted", "discarded", "stopped", "failed", "done"]);

export function emptyProject() {
  return {
    // The chat open at the editor ("" until the host names one).
    chatId: "",
    // The open chat's transcript: {type: you|reply|step|notice|decision, text, level?}.
    items: [],
    working: false,
    // The current step while working ("Opening door.blend in Blender").
    step: "",
    // The change waiting for review (its file_preview, kept up to date), or null.
    change: null,
    // Changes opened on this phone (Accept is offered once a change with notes was opened).
    opened: new Set(),
    todo: null,
    // Tasks and other chats' turns beside the open chat: {task_id: {...}}.
    tasks: {},
    // A Rule Zero stop that must be read before it goes.
    stop: "",
    artGate: false,
    // "Continue with <agent>" after a provider limit: {to, to_name, message} or null.
    offer: null,
    // The last notices worth a badge, newest last.
    alerts: [],
  };
}

// A change carries something to read before it is kept (joinery.remote.actions).
export function needsOpening(preview) {
  if (!preview) return false;
  const notes = preview.review_notes || [];
  if (notes.some((note) => note && (note.severity === "warn" || note.severity === "block"))) {
    return true;
  }
  if ((preview.failed_checks || []).length || (preview.keep_avoid || []).length) return true;
  if (preview.art_gate || (preview.not_as_asked || []).length) return true;
  return (preview.done_when || []).some((result) => result && result.status !== "pass");
}

// Reviewer notes, worst first.
export function notesWorstFirst(preview) {
  const rank = { block: 0, warn: 1, info: 2 };
  return [...((preview && preview.review_notes) || [])].sort(
    (a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3),
  );
}

// The change's facts, at most three (ADR 0040's glanceable review).
export function facts(preview) {
  const found = [...((preview && preview.joinery_facts) || []), ...((preview && preview.facts) || [])];
  return found.map((fact) => (typeof fact === "string" ? fact : fact.text || "")).filter(Boolean).slice(0, 3);
}

// One line for the checks: "Checks passed", "2 checks failed", or "".
export function checksLine(preview) {
  if (!preview) return "";
  const failed = (preview.failed_checks || []).length;
  if (failed) return failed === 1 ? "1 check failed" : `${failed} checks failed`;
  if (preview.done_when_summary) return String(preview.done_when_summary);
  return preview.checks ? "Checks passed" : "";
}

function push(state, item) {
  state.items.push(item);
  if (state.items.length > 500) state.items.splice(0, state.items.length - 500);
}

function alert(state, kind, text) {
  state.alerts.push({ kind, text });
  if (state.alerts.length > 20) state.alerts.shift();
}

function ofOpenChat(state, event) {
  return !event.chat_id || !state.chatId || event.chat_id === state.chatId;
}

function task(state, event) {
  const id = String(event.task_id);
  const known = state.tasks[id] || { task_id: id, title: "", state: "", step: "", chat_id: "" };
  if (event.kind === "task") {
    for (const key of ["title", "state", "step", "message", "transaction_id", "summary", "chat_id", "origin"]) {
      if (event[key] !== undefined && event[key] !== "") known[key] = event[key];
    }
  } else if (event.kind === "progress" || event.kind === "trail") {
    known.step = String(event.text || known.step);
  }
  state.tasks[id] = known;
  if (TASK_ENDS.has(known.state) && event.kind === "task" && !known.transaction_id) {
    // Kept as a line of its own for a moment; the next snapshot drops it.
    known.ended = true;
  }
}

// Take one host event into [state]; returns state.
export function apply(state, event) {
  if (!event || typeof event !== "object") return state;
  const kind = String(event.kind || "");
  if (event.task_id || kind === "chat_wait") {
    if (event.task_id) task(state, event);
    return state;
  }
  switch (kind) {
    case "chat":
      if (event.chat_id && event.chat_id !== state.chatId) {
        state.chatId = String(event.chat_id);
        if (event.mode === "fresh") state.items = [];
      }
      break;
    case "request":
      if (event.chat_id) state.chatId = String(event.chat_id);
      push(state, { type: "you", text: String(event.text || "") });
      break;
    case "execution":
      state.working = true;
      state.step = "Thinking…";
      break;
    case "turn_state":
      if (event.state === "working") state.working = true;
      break;
    case "progress":
      state.step = String(event.text || "");
      break;
    case "trail":
      if (event.note === "thought") break;
      if (event.state === "running") state.step = String(event.text || "");
      else if (event.state === "done" && event.text) push(state, { type: "step", text: String(event.text) });
      break;
    case "message_delta": {
      if (!ofOpenChat(state, event)) break;
      const last = state.items[state.items.length - 1];
      if (!event.new_message && last && last.type === "reply") last.text += String(event.text || "");
      else push(state, { type: "reply", text: String(event.text || "") });
      break;
    }
    case "message": {
      const text = String(event.text || "");
      if (event.level === "warn") {
        if (text.startsWith(POLICY_STOP)) {
          state.stop = text;
          alert(state, "needs_you", "Journeyman stopped: project policy.");
        }
        push(state, { type: "notice", level: "warn", text });
      } else {
        push(state, { type: "reply", text });
      }
      break;
    }
    case "notice":
      if (event.text) push(state, { type: "notice", level: event.level === "warn" ? "warn" : "info", text: String(event.text) });
      break;
    case "file_preview":
      state.change = { ...event };
      alert(state, "change_ready", "A change waits for review.");
      break;
    case "review_media":
    case "review_notes":
      if (state.change && state.change.transaction_id === event.transaction_id) {
        state.change = { ...state.change, ...event, kind: "file_preview" };
        if (kind === "review_media") state.change.media_pending = false;
      }
      break;
    case "file_decision": {
      if (state.change && state.change.transaction_id === event.transaction_id) state.change = null;
      const words = event.message || (event.accepted ? "Change accepted" : "Discarded");
      push(state, { type: "decision", accepted: !!event.accepted, text: String(words) });
      break;
    }
    case "todo":
      if (DONE_STATES.has(event.state) && (!state.todo || state.todo.todo_id === event.todo_id)) {
        state.todo = null;
      } else if (event.steps || (state.todo && state.todo.todo_id === event.todo_id)) {
        state.todo = { ...(state.todo && state.todo.todo_id === event.todo_id ? state.todo : {}), ...event };
        if (event.state === "waiting") alert(state, "needs_you", "A to-do waits for Go.");
      }
      break;
    case "art_gate":
      state.artGate = true;
      alert(state, "needs_you", "Waiting for the art direction to be approved.");
      break;
    case "art_approved":
      state.artGate = false;
      break;
    case "provider_switch":
      state.offer = event.state === "offer" ? { ...event } : null;
      if (event.state === "offer") alert(state, "needs_you", String(event.message || "Journeyman needs you."));
      break;
    case "done":
      state.working = false;
      state.step = "";
      for (const [id, known] of Object.entries(state.tasks)) if (known.ended) delete state.tasks[id];
      break;
    case "connection":
      if (event.state === "error") push(state, { type: "notice", level: "warn", text: String(event.message || "Journeyman lost its connection.") });
      break;
    default:
      break;
  }
  return state;
}

// The saved turns of a chat (GET /chats/{id}) as transcript items.
export function fromHistory(turns) {
  const items = [];
  for (const turn of turns || []) {
    items.push({ type: "you", text: String(turn.request || "") });
    for (const reply of turn.reply || []) items.push({ type: "reply", text: String(reply) });
    for (const change of turn.changes || []) items.push({ type: "step", text: `Change: ${change.summary}` });
    if (turn.outcome && turn.outcome !== "done") items.push({ type: "notice", level: "info", text: outcomeWords(turn.outcome) });
  }
  return items;
}

function outcomeWords(outcome) {
  return { cancelled: "Stopped", failed: "Didn't finish", stopped: "Stopped" }[outcome] || outcome;
}

// "10:42", or "Mon 10:42" when not today.
export function clock(seconds, now = Date.now() / 1000) {
  const when = new Date(seconds * 1000);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (now - seconds < 20 * 3600 && new Date(now * 1000).getDate() === when.getDate()) return time;
  return when.toLocaleDateString([], { weekday: "short" }) + " " + time;
}

// The diff's lines with what each is: {text, kind: add|remove|hunk|context}.
export function diffLines(diff) {
  return String(diff || "").split("\n").filter((line) => line.length).map((text) => ({
    text,
    kind: text.startsWith("+++") || text.startsWith("---") ? "file"
      : text.startsWith("@@") ? "hunk"
      : text.startsWith("+") ? "add"
      : text.startsWith("-") ? "remove" : "context",
  }));
}
