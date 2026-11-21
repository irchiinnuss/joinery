// Small DOM helpers. Text only ever goes in as text, never as HTML.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "hidden") node.hidden = !!value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function button(text, onclick, kind = "secondary", attrs = {}) {
  return h("button", { type: "button", class: `button ${kind}`, onclick, ...attrs }, text);
}

// A before/after picture compared by dragging (or with the slider for keyboards): the
// after picture is revealed through a mask, never by moving the pictures.
export function compare(before, after, label) {
  const frame = h("div", { class: "compare", role: "group", "aria-label": label });
  const back = h("img", { src: before, alt: `${label}, before`, draggable: "false" });
  const front = h("img", { src: after, alt: `${label}, after`, class: "after", draggable: "false" });
  const handle = h("div", { class: "handle", "aria-hidden": "true" });
  const slider = h("input", {
    type: "range", min: "0", max: "100", value: "50", class: "slider",
    "aria-label": "Before or after",
  });
  const set = (percent) => {
    const value = Math.max(0, Math.min(100, percent));
    frame.style.setProperty("--split", `${value}%`);
    slider.value = String(Math.round(value));
  };
  slider.addEventListener("input", () => set(Number(slider.value)));
  const drag = (event) => {
    const box = frame.getBoundingClientRect();
    set(((event.clientX - box.left) / box.width) * 100);
  };
  frame.addEventListener("pointerdown", (event) => {
    frame.setPointerCapture(event.pointerId);
    frame.classList.add("held");
    drag(event);
  });
  frame.addEventListener("pointermove", (event) => {
    if (frame.hasPointerCapture(event.pointerId)) drag(event);
  });
  const release = () => frame.classList.remove("held");
  frame.addEventListener("pointerup", release);
  frame.addEventListener("pointercancel", release);
  set(50);
  frame.append(back, front, handle);
  return h("div", { class: "compare-wrap" }, frame, slider);
}

// One picture with no other side (a new file, or a removed one).
export function single(src, label, side) {
  return h("figure", { class: "single" },
    h("img", { src, alt: `${label}, ${side}` }),
    h("figcaption", { text: side === "after" ? "New" : "Removed" }));
}

// A sound before and after, one player each.
export function sounds(before, after, label) {
  return h("div", { class: "ab", role: "group", "aria-label": label },
    before ? h("label", {}, "Before", h("audio", { controls: true, preload: "none", src: before })) : null,
    after ? h("label", {}, "After", h("audio", { controls: true, preload: "none", src: after })) : null);
}

// A short message at the bottom for a moment; [kind] colours it.
export function toast(text, kind = "info") {
  const shelf = document.getElementById("toasts");
  if (!shelf || !text) return;
  const note = h("div", { class: `toast ${kind}`, role: "status", text });
  shelf.append(note);
  setTimeout(() => {
    note.classList.add("leaving");
    setTimeout(() => note.remove(), 160);
  }, 3200);
}
