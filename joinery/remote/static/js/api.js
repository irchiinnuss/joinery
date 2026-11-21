// The Remote server's API (joinery/remote/server.py). Cookies carry the device token;
// a change also carries the custom header a cross-site form can't send.

export class Offline extends Error {}

export async function get(path) {
  return call(path);
}

export async function post(path, body) {
  return call(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Joinery-Remote": "1" },
    body: JSON.stringify(body),
  });
}

async function call(path, options = {}) {
  let answer;
  try {
    answer = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  } catch (error) {
    throw new Offline(String(error));
  }
  const data = await answer.json().catch(() => ({}));
  return { status: answer.status, ok: answer.ok, data };
}

// The project's live events, resumed from the last seq after a drop.
export class Stream {
  constructor(projectId, handlers) {
    this.projectId = projectId;
    this.handlers = handlers;
    this.seq = 0;
    this.started = 0;
    this.source = null;
    this.closed = false;
    this.retry = 1000;
  }

  open() {
    if (this.closed) return;
    const path = `/api/projects/${this.projectId}/events?since=${this.seq}&started=${this.started}`;
    const source = new EventSource(path);
    this.source = source;
    source.addEventListener("hello", (message) => {
      const hello = JSON.parse(message.data);
      if (hello.started !== this.started) this.seq = 0;
      this.started = hello.started;
      // Events up to here are the catch-up: shown, never announced.
      this.caughtUp = hello.seq;
      this.retry = 1000;
      this.handlers.hello(hello);
    });
    source.addEventListener("event", (message) => {
      this.seq = Number(message.lastEventId) || this.seq;
      this.handlers.event(JSON.parse(message.data), this.seq <= (this.caughtUp || 0));
    });
    source.addEventListener("notice", (message) => this.handlers.notice(JSON.parse(message.data)));
    source.addEventListener("gone", (message) => {
      const why = JSON.parse(message.data || "{}");
      source.close();
      this.handlers.gone(why.reason || "closed");
      if (why.reason !== "revoked") this.later();
    });
    source.onerror = () => {
      source.close();
      this.handlers.down();
      this.later();
    };
  }

  later() {
    if (this.closed) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.open(), this.retry);
    this.retry = Math.min(this.retry * 2, 15000);
  }

  close() {
    this.closed = true;
    clearTimeout(this.timer);
    if (this.source) this.source.close();
  }
}
