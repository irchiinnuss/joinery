// The phone page's state model (joinery/remote/static/js/state.js), run by
// tests/test_remote_page.py with `node --test`.
import assert from "node:assert/strict";
import test from "node:test";

import {
  apply, checksLine, diffLines, emptyProject, facts, fromHistory, needsOpening, notesWorstFirst,
} from "../../joinery/remote/static/js/state.js";

function play(...events) {
  const state = emptyProject();
  for (const event of events) apply(state, event);
  return state;
}

test("a turn: the request, steps, a streamed reply and the end", () => {
  const state = play(
    { kind: "chat", chat_id: "c1", mode: "fresh" },
    { kind: "request", chat_id: "c1", text: "Make the hedge taller" },
    { kind: "execution", name: "Sol" },
    { kind: "trail", text: "Opening hedge.blend", state: "running" },
    { kind: "trail", text: "Opening hedge.blend", state: "done" },
    { kind: "message_delta", text: "Made it ", new_message: true },
    { kind: "message_delta", text: "taller." },
  );
  assert.equal(state.chatId, "c1");
  assert.equal(state.working, true);
  assert.deepEqual(state.items.map((item) => [item.type, item.text]), [
    ["you", "Make the hedge taller"],
    ["step", "Opening hedge.blend"],
    ["reply", "Made it taller."],
  ]);
  apply(state, { kind: "done", ok: true });
  assert.equal(state.working, false);
  assert.equal(state.step, "");
});

test("a change waits, gets its pictures, then is decided", () => {
  const state = play({ kind: "file_preview", transaction_id: "tx1", summary: "Taller", media_pending: true, media: [] });
  assert.equal(state.change.summary, "Taller");
  assert.equal(state.alerts.at(-1).kind, "change_ready");
  apply(state, { kind: "review_media", transaction_id: "tx1", media: [{ after: "media/tx1/0/after" }] });
  assert.equal(state.change.media_pending, false);
  assert.equal(state.change.media[0].after, "media/tx1/0/after");
  apply(state, { kind: "review_media", transaction_id: "other", media: [] });
  assert.equal(state.change.media.length, 1);
  apply(state, { kind: "file_decision", transaction_id: "tx1", accepted: true });
  assert.equal(state.change, null);
  assert.equal(state.items.at(-1).text, "Change accepted");
});

test("a Rule Zero stop is kept until read, with no retry", () => {
  const state = play({ kind: "message", level: "warn", text: "Stopped by project policy: a generative service" });
  assert.match(state.stop, /generative service/);
  assert.equal(state.alerts.at(-1).kind, "needs_you");
  apply(state, { kind: "message", level: "warn", text: "Something else" });
  assert.match(state.stop, /generative service/);
});

test("a to-do waits for Go, then goes away when done", () => {
  const state = play({ kind: "todo", todo_id: "t1", state: "waiting", steps: ["Model", "Paint"], done_when: [] });
  assert.equal(state.todo.todo_id, "t1");
  apply(state, { kind: "todo", todo_id: "t1", state: "going" });
  assert.equal(state.todo.state, "going");
  assert.deepEqual(state.todo.steps, ["Model", "Paint"]);
  apply(state, { kind: "todo", todo_id: "t1", state: "done" });
  assert.equal(state.todo, null);
});

test("tasks and other chats' turns stay out of the open chat", () => {
  const state = play(
    { kind: "task", task_id: "k1", title: "Fix the menu", state: "working", chat_id: "c2" },
    { kind: "message_delta", task_id: "k1", text: "elsewhere", new_message: true },
  );
  assert.equal(state.items.length, 0);
  assert.equal(state.tasks.k1.title, "Fix the menu");
});

test("the art gate and a provider offer", () => {
  const state = play({ kind: "art_gate" }, { kind: "provider_switch", state: "offer", to: "codex", to_name: "Codex CLI" });
  assert.equal(state.artGate, true);
  assert.equal(state.offer.to_name, "Codex CLI");
  apply(state, { kind: "art_approved" });
  apply(state, { kind: "provider_switch", state: "switched" });
  assert.equal(state.artGate, false);
  assert.equal(state.offer, null);
});

test("what needs opening before Accept", () => {
  assert.equal(needsOpening({ review_notes: [{ severity: "info" }] }), false);
  assert.equal(needsOpening({ review_notes: [{ severity: "block" }] }), true);
  assert.equal(needsOpening({ done_when: [{ status: "inconclusive" }] }), true);
  assert.equal(needsOpening({ done_when: [{ status: "pass" }] }), false);
  assert.equal(needsOpening({ failed_checks: [{}] }), true);
});

test("notes worst first, three facts, one checks line, a diff", () => {
  const notes = notesWorstFirst({ review_notes: [{ severity: "info", text: "a" }, { severity: "block", text: "b" }, { severity: "warn", text: "c" }] });
  assert.deepEqual(notes.map((note) => note.text), ["b", "c", "a"]);
  assert.deepEqual(facts({ joinery_facts: ["1", "2"], facts: [{ text: "3" }, "4"] }), ["1", "2", "3"]);
  assert.equal(checksLine({ failed_checks: [{}, {}] }), "2 checks failed");
  assert.equal(checksLine({ checks: ["lint"] }), "Checks passed");
  assert.deepEqual(diffLines("--- a\n+++ b\n@@ x\n-old\n+new\n same").map((line) => line.kind),
    ["file", "file", "hunk", "remove", "add", "context"]);
});

test("a saved chat as transcript items", () => {
  const items = fromHistory([{ request: "Hi", reply: ["Hello"], changes: [{ summary: "Door" }], outcome: "cancelled" }]);
  assert.deepEqual(items.map((item) => item.type), ["you", "reply", "step", "notice"]);
  assert.equal(items[3].text, "Stopped");
});
