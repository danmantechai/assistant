"use strict";
// MARY's settings app. Plain JavaScript, no framework: every page is drawn from the local API
// (mary/settings/server.py), changes collect in a draft until Save, and text from files is always set as text,
// never as HTML. Nothing here is fetched from the internet.

// ------------------------------------------------------------------ icons (stroke paths, 24x24)

const ICONS = {
  identity: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>',
  personality: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c.9 1.2 2.1 1.8 3.5 1.8s2.6-.6 3.5-1.8M9 9.8v.4M15 9.8v.4"/>',
  stories: '<path d="M12 6.5C10.5 5 8 4.5 4 4.5v14c4 0 6.5.5 8 2 1.5-1.5 4-2 8-2v-14c-4 0-6.5.5-8 2zM12 6.5v14"/>',
  conversations: '<path d="M5 4.5h9.5a2 2 0 0 1 2 2V12a2 2 0 0 1-2 2H9.5L6 17v-3H5a2 2 0 0 1-2-2V6.5a2 2 0 0 1 2-2z"/><path d="M19.5 9.5a1.5 1.5 0 0 1 1.5 1.5v5a1.5 1.5 0 0 1-1.5 1.5h-.5V20l-3-2.5h-4"/>',
  remembered: '<path d="M6.5 4.5h11v16L12 17l-5.5 3.5z"/><path d="M9.5 9h5"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  photos: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="M4 17l5-4.5 4 3.5 3-2.5 4 3.5"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  pin: '<path d="M12 21s6.5-5.6 6.5-11a6.5 6.5 0 0 0-13 0C5.5 15.4 12 21 12 21z"/><circle cx="12" cy="10" r="2.3"/>',
  calendar: '<rect x="4" y="5.5" width="16" height="14.5" rx="2.5"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  people: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19c0-3.2 2.4-5 5.5-5s5.5 1.8 5.5 5"/><circle cx="17" cy="9.5" r="2.4"/><path d="M16 14.2c3 0 4.8 1.5 4.8 4.3"/>',
  code: '<path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  voice: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
  wake: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  about: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="11" r="2.2"/><path d="M5.8 16.5c.6-1.5 1.8-2.2 3.2-2.2s2.6.7 3.2 2.2M14.5 10h4M14.5 13.5h3"/>',
  model: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  listening: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
  skills: '<path d="M4 7.5h4.2a2.3 2.3 0 1 1 4.6 0H17v4.2a2.3 2.3 0 1 1 0 4.6V20H4z"/>',
  connections: '<path d="M9 3v5M15 3v5M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0zM12 17v4"/>',
  play: '<path d="M8 5.5l10.5 6.5L8 18.5z" fill="currentColor"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  chevron: '<path d="M7 10l5 5 5-5"/>',
  selector: '<path d="M8 9.5l4-4 4 4M8 14.5l4 4 4-4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M5 20h14"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  restart: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
};

function icon(name) {
  const holder = document.createElement("span");
  holder.innerHTML = `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;  // constant markup only
  return holder.firstChild;
}

// ------------------------------------------------------------------ small helpers

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "value") node.value = v;
    else if (k === "style") node.style.cssText = v;   // through the DOM: the page's CSP refuses style="" attributes
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}

async function api(method, path, body) {
  const opts = { method, headers: { "X-MARY": "1" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const type = r.headers.get("Content-Type") || "";
  if (!type.startsWith("application/json")) {
    if (!r.ok) throw new Error("That didn't work. Try again.");
    return r.blob();
  }
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "That didn't work. Try again.");
  return data;
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const clone = (x) => JSON.parse(JSON.stringify(x ?? null));
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const slug = (text) => (text || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
const hueOf = (text) => [...(text || "?")].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const GREETING = /^(hey|hi|hello|ok|okay|yo)\b[\s,]*/i;

function avatar(name, size = "") {
  return el("span", { class: `avatar ${size}`, style: `--hue:${hueOf(name)}`, text: (name || "?").trim().slice(0, 1).toUpperCase(), "aria-hidden": "true" });
}

function getDeep(obj, path) {
  return path.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), obj);
}
function setDeep(obj, path, value) {
  const keys = path.split(".");
  let o = obj;
  for (const k of keys.slice(0, -1)) o = (o[k] = o[k] && typeof o[k] === "object" ? o[k] : {});
  o[keys.at(-1)] = value;
}
function deleteDeep(obj, path) {
  const keys = path.split(".");
  const parents = [obj];
  for (const k of keys.slice(0, -1)) {
    const next = parents.at(-1)[k];
    if (!next || typeof next !== "object") return;
    parents.push(next);
  }
  delete parents.at(-1)[keys.at(-1)];
  for (let i = parents.length - 1; i > 0; i--) if (!Object.keys(parents[i]).length) delete parents[i - 1][keys[i - 1]];
}
function merged(...objs) {
  const out = {};
  for (const o of objs) for (const [k, v] of Object.entries(o || {})) {
    out[k] = v && typeof v === "object" && !Array.isArray(v) ? merged(out[k], v) : v;
  }
  return out;
}

function readB64(file) {
  return new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",")[1] || "");
    r.onerror = () => fail(new Error("Couldn't read that file."));
    r.readAsDataURL(file);
  });
}

function bytesToB64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function download(name, text) {
  const a = el("a", { href: URL.createObjectURL(new Blob([text], { type: "application/json" })), download: name });
  document.body.append(a);
  a.click();
  a.remove();
}

function pickFile(accept) {
  return new Promise((ok) => {
    const input = el("input", { type: "file", accept, hidden: true });
    input.addEventListener("change", () => { ok(input.files[0] || null); input.remove(); });
    document.body.append(input);
    input.click();
  });
}

let toastTimer;
function toast(text, error = false) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.className = "toast" + (error ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), error ? 7000 : 3200);
}

function choices(kind) {
  if (!S.choices[kind]) S.choices[kind] = api("GET", `/api/choices/${kind}`).catch(() => ({ items: [], note: "Couldn't load this list." }));
  return S.choices[kind];
}

// One shared player: starting a sample stops the one before; pressing the same button again stops it.
const Player = {
  audio: new Audio(), button: null,
  stop() {
    this.audio.pause();
    if (this.button) this.button.replaceChildren(icon("play"));
    this.button = null;
  },
  async play(button, source) {
    const again = this.button === button;
    this.stop();
    if (again) return;
    this.button = button;
    button.replaceChildren(icon("stop"));
    button.classList.add("busy");
    try {
      const src = typeof source === "function" ? await source() : source;
      if (this.button !== button) return;
      this.audio.src = src;
      await this.audio.play();
    } catch (e) {
      if (e.message) toast(e.message, true);
      this.stop();
    } finally {
      button.classList.remove("busy");
    }
  },
};
Player.audio.addEventListener("ended", () => Player.stop());

// ------------------------------------------------------------------ state and the draft (unsaved changes)

const S = {
  state: null,           // /api/state
  editing: null,         // the persona being edited
  her: null,             // its details (/api/personas/<key>)
  draft: { settings: {}, persona: {}, profile: null },
  choices: {},
  saved: false,          // saved this visit while MARY isn't running: say when it applies
  restarting: false,
  trainer: null,         // the wake-word recording session
  voiceFilter: "all",
};

const herNow = (field) => (field in S.draft.persona ? S.draft.persona[field] : S.her[field]);
const assistant = () => (herNow("name") || "").trim() || "the assistant";
const profileNow = () => S.draft.profile || S.state.profile;
const settingNow = (path) => (path in S.draft.settings ? S.draft.settings[path] : S.state.settings.values[path].value);
const DEFAULT_VOICE = { provider: "kokoro", voice: "af_heart", speed: 1, elevenlabs: { voice_id: "", model: "eleven_v4_turbo" } };
const voiceNow = () => merged(DEFAULT_VOICE, S.her.voice, S.draft.persona.voice);
const WAKE_KEYS = ["phrase", "model", "names", "alone", "misheard", "bare"];
const wakeBase = () => S.her.wake.map((w) => Object.fromEntries(WAKE_KEYS.map((k) => [k, w[k]])));
const wakeNow = () => ("wake" in S.draft.persona ? S.draft.persona.wake : wakeBase());

function dirty() {
  return Object.keys(S.draft.settings).length > 0 || Object.keys(S.draft.persona).length > 0 || S.draft.profile !== null;
}

function setSetting(path, value) {
  if (same(value, S.state.settings.values[path].value)) delete S.draft.settings[path];
  else S.draft.settings[path] = value;
  renderBar();
}

function setHer(field, value) {
  if (same(value, S.her[field])) delete S.draft.persona[field];
  else S.draft.persona[field] = value;
  renderBar();
}

function setWake(list) {
  if (same(list, wakeBase())) delete S.draft.persona.wake;
  else S.draft.persona.wake = list;
  renderBar();
}

function setVoice(path, value) {
  const d = clone(S.draft.persona.voice) || {};
  setDeep(d, path, value);
  if (same(getDeep(merged(DEFAULT_VOICE, S.her.voice), path), value)) deleteDeep(d, path);
  if (Object.keys(d).length) S.draft.persona.voice = d;
  else delete S.draft.persona.voice;
  renderBar();
}

function setProfile(part, value) {
  const p = { ...profileNow(), [part]: value };
  S.draft.profile = same(p, S.state.profile) ? null : p;
  renderBar();
}

function discard() {
  S.draft = { settings: {}, persona: {}, profile: null };
  render();
}

async function save(quiet = false) {
  renderBar("Saving…");
  try {
    if (Object.keys(S.draft.settings).length) {
      await api("POST", "/api/settings", { changes: S.draft.settings });
      S.draft.settings = {};
    }
    if (Object.keys(S.draft.persona).length) {
      S.her = await api("POST", `/api/personas/${S.editing}`, { fields: S.draft.persona });
      S.draft.persona = {};
    }
    if (S.draft.profile) {
      await api("POST", "/api/profile", S.draft.profile);
      S.draft.profile = null;
    }
    S.state = await api("GET", "/api/state");
    S.saved = true;
    if (!quiet) toast("Saved");
    render();
    return true;
  } catch (e) {
    toast(e.message, true);
    render();
    return false;
  }
}

async function restart() {
  try {
    await api("POST", "/api/restart", {});
  } catch (e) {
    toast(e.message, true);
    return;
  }
  S.restarting = true;
  renderBar();
  let deadline = Date.now() + 30000;
  while (Date.now() < deadline) {   // wait for MARY to close…
    try { await api("GET", "/api/ping"); await sleep(700); } catch { break; }
  }
  deadline = Date.now() + 120000;
  while (Date.now() < deadline) {   // …and come back
    await sleep(1500);
    try { await api("GET", "/api/ping"); break; } catch { /* still starting */ }
  }
  try {
    S.state = await api("GET", "/api/state");
    S.her = await api("GET", `/api/personas/${S.editing}`);
    toast("MARY restarted");
  } catch {
    toast("MARY is still starting. Reload this page in a moment.", true);
  }
  S.restarting = false;
  S.saved = false;
  render();
}

// ------------------------------------------------------------------ controls

function toggle(on, onChange, label) {
  const b = el("button", { class: "toggle", type: "button", role: "switch", "aria-checked": String(!!on), "aria-label": label });
  b.addEventListener("click", () => {
    const now = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(now));
    onChange(now);
  });
  return b;
}

// A dropdown whose options keep their real values (numbers, null) rather than strings. pending: its options are
// still loading (sel.fill brings them).
function select(options, value, onChange, label, pending = false) {
  const sel = el("select", { "aria-label": label });
  let values = [];
  sel.fill = (opts, missing = "not available") => {
    const list = opts.some((o) => same(o[0], value)) || value === undefined ? opts : [[value, `${value ?? "None"} (${missing})`], ...opts];
    values = list.map((o) => o[0]);
    sel.replaceChildren(...list.map(([, text], i) => el("option", { value: String(i), text })));
    sel.value = String(Math.max(0, values.findIndex((v) => same(v, value))));
  };
  sel.fill(options, pending ? "loading…" : undefined);
  sel.addEventListener("change", () => onChange(values[Number(sel.value)]));
  return sel;
}

function segmented(options, value, onChange, label) {
  const box = el("div", { class: "segmented", role: "radiogroup", "aria-label": label });
  options.forEach(([v, text], i) => {
    box.append(el("button", { type: "button", role: "radio", "aria-checked": String(same(v, value)), text, onclick: () => {
      for (const b of box.children) b.setAttribute("aria-checked", "false");
      box.children[i].setAttribute("aria-checked", "true");
      onChange(v);
    } }));
  });
  return box;
}

function chips(items, onChange, placeholder, label) {
  const list = [...(items || [])];
  const box = el("div", { class: "chips" });
  const input = el("input", { type: "text", placeholder, "aria-label": label });
  const draw = () => {
    box.replaceChildren(
      ...list.map((item, i) => el("span", { class: "chip" }, el("span", { text: item, title: item }),
        el("button", { type: "button", "aria-label": `Remove ${item}`, onclick: () => { list.splice(i, 1); onChange([...list]); draw(); input.focus(); } }, icon("x")))),
      input);
  };
  const add = () => {
    for (const part of input.value.split(",")) {
      const v = part.trim();
      if (v && !list.includes(v)) list.push(v);
    }
    input.value = "";
    onChange([...list]);
    draw();
    input.focus();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
    else if (e.key === "Backspace" && !input.value && list.length) { list.pop(); onChange([...list]); draw(); input.focus(); }
  });
  input.addEventListener("blur", () => { if (input.value.trim()) add(); });
  box.addEventListener("click", (e) => { if (e.target === box) input.focus(); });
  draw();
  return box;
}

function textInput(value, onChange, label, placeholder = "") {
  const input = el("input", { type: "text", value: value ?? "", placeholder, "aria-label": label });
  input.addEventListener("input", () => onChange(input.value));
  return input;
}

// A writing box with a gentle length guide (a small model follows a short description better).
function writingBox(value, onChange, label, placeholder, cls = "") {
  const area = el("textarea", { "aria-label": label, placeholder, class: cls, value: value || "" });
  const note = el("span");
  const show = () => {
    const n = area.value.trim().length;
    note.textContent = n > 1800 ? "Long. Shorter descriptions work better and start faster." : n > 0 ? "Good length" : "";
    note.className = n > 1800 ? "warn" : "";
  };
  area.addEventListener("input", () => { show(); onChange(area.value); });
  show();
  return el("div", { class: "stack", style: "gap:0;padding-bottom:16px" }, area, el("div", { class: "meter-line" }, note, el("span")));
}

// A small "i" that says its text on hover or focus: explanations stay out of the way of the page.
function info(text) {
  return text ? el("span", { class: "tip", tabindex: "0", role: "img", title: text, "aria-label": text, text: "i" }) : null;
}

function card(title, sub, ...body) {
  return el("section", { class: "card" }, title && el("div", { class: "card-head" }, el("div", {}, el("h2", {}, title, info(sub)))), ...body);
}

function row(label, help, control, opts = {}) {
  return el("div", { class: "row" + (opts.wide ? " wide" : "") },
    el("div", {}, el("div", { class: "label" }, label, info(help), opts.changed && el("span", { class: "changed-dot", title: "Not saved yet" })), opts.reset),
    el("div", { class: "control" }, control));
}

function head(title, lead, ...right) {
  return el("div", { class: "head" }, el("div", { class: "grow" }, el("h1", {}, title, info(lead))), ...right);
}

// A setting from the schema: label, help, the right control, a dot while unsaved, and Reset when it's not the default.
function settingRow(f) {
  const cur = S.state.settings.values[f.path];
  const value = settingNow(f.path);
  const onChange = (v) => { setSetting(f.path, v); render(); };
  const changed = f.path in S.draft.settings;
  const reset = !changed && cur.origin === "yours" && !same(cur.value, cur.base)
    ? el("button", { class: "reset", type: "button", text: "Reset to default", onclick: () => resetSetting(f.path) }) : null;
  let control;
  if (f.kind === "switch") control = toggle(value, (v) => setSetting(f.path, v), f.label);
  else if (f.kind === "number") {
    const input = el("input", { type: "number", value: value ?? "", min: f.min, max: f.max, step: f.step || "any", "aria-label": f.label });
    input.addEventListener("change", () => { if (input.value !== "" && input.checkValidity()) onChange(Number(input.value)); });
    control = el("span", { class: "actions" }, input, f.unit && el("span", { class: "muted", text: f.unit }));
  } else if (f.kind === "choice") control = select(f.choices.map((c) => [c[0], c[1]]), value, onChange, f.label);
  else if (f.kind === "segmented") control = segmented(f.choices.map((c) => [c[0], c[1]]), value, onChange, f.label);
  else if (f.kind === "words") control = chips(value, (v) => setSetting(f.path, v), "Add a word", f.label);
  else if (f.kind === "mic") {
    control = select([[null, "Windows' default microphone"]], value, onChange, f.label, true);
    choices("mics").then((c) => control.fill(c.items.map((m) => [m.id, m.name])));
  } else if (f.kind === "hotkey") control = hotkeyControl(value, onChange);
  else control = textInput(value, (v) => setSetting(f.path, v), f.label);
  const help = f.kind === "segmented" ? f.choices.find((c) => same(c[0], value))?.[2] || f.help : f.help;
  return row(f.label, help, control, { wide: f.kind === "words", changed, reset });
}

async function resetSetting(path) {
  try {
    S.state = await api("POST", "/api/settings/reset", { path });
    delete S.draft.settings[path];
    toast("Back to the default");
  } catch (e) {
    toast(e.message, true);
  }
  render();
}

function fieldsCard(page, title, sub, only) {
  const fields = S.state.settings.pages[page].filter((f) => !only || only.includes(f.path));
  const basic = fields.filter((f) => !f.advanced);
  const more = fields.filter((f) => f.advanced);
  return el("section", { class: "card" },
    title && el("div", { class: "card-head" }, el("div", {}, el("h2", {}, title, info(sub)))),
    basic.map(settingRow),
    more.length > 0 && el("details", { class: "more" }, el("summary", {}, "Advanced", icon("chevron")), more.map(settingRow)));
}

// The hotkey: press Change, then the keys ("ctrl+space").
const KEYNAMES = { Space: "space", Enter: "enter", Tab: "tab", Backspace: "backspace", Insert: "insert", Delete: "delete", Home: "home", End: "end",
  PageUp: "pageup", PageDown: "pagedown", ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Pause: "pause", Backquote: "`" };
const pretty = (combo) => (combo || "").split("+").filter(Boolean).map((k) => (k.length === 1 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1)));

function hotkeyControl(value, onChange) {
  const keys = el("span", { class: "hotkey" }, pretty(value).map((k) => el("kbd", { text: k })));
  const button = el("button", { class: "btn small", type: "button", text: "Change" });
  const box = el("span", { class: "actions" }, keys, button);
  let listening = false;
  const onKey = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") return stop();
    if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;
    const key = KEYNAMES[e.code] || (/^Key[A-Z]$/.test(e.code) ? e.code.slice(3).toLowerCase() : /^Digit\d$/.test(e.code) ? e.code.slice(5)
      : /^F\d{1,2}$/.test(e.code) ? e.code.toLowerCase() : null);
    if (!key) return;
    const mods = [e.ctrlKey && "ctrl", e.altKey && "alt", e.shiftKey && "shift", e.metaKey && "win"].filter(Boolean);
    if (!mods.length && !/^f\d/.test(key)) { keys.replaceChildren(el("kbd", { text: "Add Ctrl, Alt, Shift or Win" })); return; }
    window.removeEventListener("keydown", onKey, true);
    onChange([...mods, key].join("+"));
  };
  const stop = () => { window.removeEventListener("keydown", onKey, true); render(); };
  button.addEventListener("click", () => {
    if (listening) return stop();
    listening = true;
    box.classList.add("listening");
    keys.replaceChildren(el("kbd", { text: "Press the keys…" }));
    button.textContent = "Cancel";
    window.addEventListener("keydown", onKey, true);
  });
  return box;
}

// ------------------------------------------------------------------ the sidebar: persona switcher and pages

// Memory: who the assistant is and what it knows (yourself included). General: how it sounds, wakes, hears and runs.
// Identity, Personality, Voice, Wake word and Stories edit the persona in the switcher.
const NAV = [
  ["Memory", [["identity", "Identity"], ["personality", "Personality"], ["about", "About you"], ["remembered", "Remembered"], ["stories", "Stories"], ["photos", "Photos"], ["people", "People"]]],
  ["General", [["voice", "Voice"], ["wake", "Wake word"], ["listening", "Listening"], ["model", "Model"], ["skills", "Skills"], ["connections", "Connections"]]],
];

function renderSide(page) {
  document.getElementById("nav").replaceChildren(...NAV.flatMap(([group, pages]) => [el("div", { class: "group", text: group }),
    ...pages.map(([id, label, soon]) => el("a", { href: `#${id}`, "aria-current": id === page ? "page" : null },
      icon(id), el("span", { class: "grow", text: label }), soon && el("span", { class: "pill", text: soon })))]));
  const active = S.state.personas.find((p) => p.active);
  const her = S.her;
  const action = (name, text, run, opts = {}) => el("button", { type: "button", role: "menuitem", class: opts.danger ? "danger" : null,
    disabled: opts.disabled || undefined, title: opts.title, onclick: () => { close(); run(); } }, icon(name), el("span", { class: "grow", text }));
  const button = el("button", { class: "switch-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": `Persona: ${assistant()}. Change` },
    avatar(herNow("name")),
    el("span", { class: "who" }, el("b", { text: herNow("name") || "Untitled" }),
      el("span", { class: S.her.active ? "on" : "", text: S.her.active ? "Active" : `Active: ${active ? active.name : "none"}` })),
    icon("selector"));
  const close = () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); document.removeEventListener("click", outside, true); };
  const outside = (e) => { if (!menu.contains(e.target) && !button.contains(e.target)) close(); };
  const menu = el("div", { class: "menu", role: "menu", hidden: true },
    S.state.personas.map((p) => el("button", { type: "button", role: "menuitem", onclick: () => { close(); switchPersona(p.key); } },
      avatar(p.name, "small"), el("span", { class: "grow", text: p.name }),
      p.active ? el("span", { class: "pill good", text: "Active" }) : !p.private ? el("span", { class: "pill", text: "Built-in" }) : null)),
    el("hr"),
    action("plus", "New persona", () => openNewPersona()),
    action("upload", "Import a character card", () => importCard()),
    el("hr"),
    el("div", { class: "menu-label", text: herNow("name") || "This persona" }),
    action("code", "Show the prompt", showPrompt),
    action("download", "Export as a character card", exportCard),
    action("copy", "Duplicate", () => openNewPersona("copy")),
    her.private && action("trash", "Remove", removePersona, { danger: true, disabled: her.active, title: her.active ? "Make another persona active first" : null }));
  button.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) { document.addEventListener("click", outside, true); menu.querySelector("button")?.focus(); }
  });
  menu.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); button.focus(); } });
  document.getElementById("switcher").replaceChildren(button, menu);

  const st = S.state;
  document.getElementById("status").replaceChildren(el("span", { class: "dot" + (st.running ? " on" : "") }),
    el("span", { text: st.running ? (st.ready ? "MARY is running" : "MARY is starting…") : "MARY isn't running" }));
}

async function switchPersona(key) {
  if (key === S.editing) return;
  if (Object.keys(S.draft.persona).length && !confirm(`Discard unsaved changes to ${assistant()}?`)) return;
  try {
    S.her = await api("GET", `/api/personas/${key}`);
    S.editing = key;
    S.draft.persona = {};
    if (S.trainer) leaveTrainer();
    render();
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ Assistant: Identity, Personality, Writing

// On every page that edits the persona: say so when it isn't the active one.
function editingBanner() {
  if (S.her.active) return null;
  const active = S.state.personas.find((p) => p.active);
  const twin = active && active.name.trim().toLowerCase() === assistant().toLowerCase();   // a copy, same name
  const text = !active ? `You're editing ${assistant()}. No persona is active.`
    : twin ? `You're editing the ${S.her.private ? "other" : "built-in"} ${assistant()}, not the active one.`
      : `You're editing ${assistant()}. ${active.name} is active.`;
  return el("div", { class: "banner info" }, el("span", { class: "grow", text }),
    el("button", { class: "btn small", type: "button", text: twin ? "Make this one active" : `Make ${assistant()} active`, onclick: () => usePersona(S.her.key) }));
}

function partOfDay() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function greetingPreview() {
  const you = profileNow().name;
  const g = (you ? herNow("greeting") : herNow("greeting_no_name")) || "";
  return g.replaceAll("{part}", partOfDay()).replaceAll("{name}", you || "").replace(/\s+([,.!?])/g, "$1");
}

function pageIdentity() {
  const her = S.her;
  const title = el("h1", { text: herNow("name") || "Untitled" });
  const sub = her.private ? "Your persona" : "Built-in persona. Your changes are saved as your own copy.";
  const preview = el("div", { class: "help" }, el("span", { text: "Preview: " }), el("i", { text: greetingPreview() }));
  const greet = textInput(herNow("greeting"), (v) => { setHer("greeting", v); preview.lastChild.textContent = greetingPreview(); }, "Greeting");
  const rename = (v) => { setHer("name", v); title.textContent = v.trim() || "Untitled"; renderSide(currentPage()[0]); };
  return el("div", {},
    el("div", { class: "head" }, avatar(herNow("name"), "large"), el("div", { class: "grow", title: sub }, title),
      her.active && el("span", { class: "pill good" }, icon("check"), "Active")),
    editingBanner(),
    card(null, null,
      row("Name", "What the assistant is called.", textInput(herNow("name"), rename, "Name")),
      row("Nicknames", `What you call ${assistant()}. Never used as your name.`, chips(herNow("nicknames"), (v) => setHer("nicknames", v), "Add a nickname", "Nicknames"), { wide: true }),
      row("Greeting", `Said when ${assistant()} starts. {part} becomes Good morning, afternoon or evening; {name} is your name.`,
        el("div", { class: "stack", style: "width:100%;gap:6px" }, greet, preview), { wide: true })),
    card("Wake responses", "One of these is said when the wake word is heard. For a sound the voice can't spell, write the text, then the sounds between slashes: Mm-hmm? /mˈhm?/",
      row("Any time", null, chips(herNow("replies"), (v) => setHer("replies", v), "Add a response", "Responses"), { wide: true }),
      row("Before noon, also", null, chips(herNow("morning_replies"), (v) => setHer("morning_replies", v), "Add a morning response", "Morning responses"), { wide: true })),
    messagesCard());
}

function pagePersonality() {
  const soulBox = writingBox(herNow("soul"), (v) => setHer("soul", v), "Personality", `You are ${assistant()}: …`);
  const apply = (p) => {
    const text = p.text.replaceAll("{name}", assistant());
    if (herNow("soul").trim() && !confirm(`Replace the current personality with “${p.label}”?`)) return;
    const area = soulBox.querySelector("textarea");
    area.value = text;
    area.dispatchEvent(new Event("input"));
  };
  return el("div", {},
    head("Personality", `Who ${assistant()} is, and how ${assistant()} talks.`),
    editingBanner(),
    card(`Who ${assistant()} is`, "Written to the assistant: “You are…”. Warmth, humour, what they care about, and how they write (short messages, emojis, favourite words). Say what they do rather than what they don't; a model copies the words it reads.",
      el("div", { class: "presets" }, el("span", { class: "muted", style: "align-self:center;font-size:13.5px", text: "Start from" }),
        S.state.settings.presets.map((p) => el("button", { type: "button", text: p.label, onclick: () => apply(p) }))),
      soulBox),
    card("Speaking style", "How the assistant talks out loud, with a few example exchanges. Real phrases work best. Keep facts like times or the weather out of the examples, or they get repeated as if true.",
      writingBox(herNow("speech"), (v) => setHer("speech", v), "Speaking style", "How you talk:\n- …\n\nUser: Good morning.\nYou: …")));
}

// Real messages from the person the persona is: how they write comes from these and the personality, not a
// separate description.
function messagesCard() {
  const files = S.her.texts.length ? S.her.texts.map((t) => el("div", { class: "item" },
    el("div", { class: "grow" }, el("div", { text: t.label }), el("div", { class: "sub", text: `${t.count} messages` })),
    el("button", { class: "icon-btn danger", type: "button", "aria-label": `Remove ${t.label}`, onclick: () => removeTexts(t) }, icon("trash"))))
    : [el("div", { class: "empty", text: "No messages yet." })];
  return card("Real messages", `Messages ${assistant()} really wrote. A couple are shown whenever ${assistant()} writes something for you, so it sounds like them. They stay on this PC.`,
    el("div", { class: "list" }, files),
    el("div", { class: "add-line", style: "align-items:center;flex-wrap:wrap" }, el("button", { class: "btn", type: "button", onclick: importTexts }, icon("upload"), "Import messages"),
      info("A .txt file with messages separated by blank lines, or a chat export.")));
}

async function importTexts() {
  const file = await pickFile(".txt,.zip");
  if (!file) return;
  if (file.size > 30e6) return toast("That file is too big (30 MB at most).", true);
  try {
    const data = await readB64(file);
    const found = await api("POST", `/api/personas/${S.editing}/texts/inspect`, { filename: file.name, data });
    let sender = null;
    if (found.kind === "chat") {
      sender = await askSender(found.senders);
      if (!sender) return;
    } else if (!confirm(`Import ${found.count} messages from ${file.name}?`)) return;
    const out = await api("POST", `/api/personas/${S.editing}/texts`, { filename: file.name, data, sender });
    S.her = out.persona;
    toast(`Imported ${out.count} messages`);
    render();
  } catch (e) { toast(e.message, true); }
}

function askSender(senders) {
  return new Promise((ok) => {
    const dialog = document.getElementById("dialog");
    const list = el("div", { class: "radio-list" }, senders.map(([name, count], i) => el("label", {},
      el("input", { type: "radio", name: "sender", value: name, checked: i === 0 || undefined }), el("span", { class: "grow", text: name }), el("span", { class: "muted", text: `${count} messages` }))));
    const form = el("form", { method: "dialog" },
      el("h2", { id: "dialog-title", text: "Whose messages?" }),
      list,
      el("div", { class: "actions end" }, el("button", { class: "btn", type: "button", text: "Cancel", onclick: () => { dialog.close(); ok(null); } }),
        el("button", { class: "btn primary", type: "submit", text: "Import" })));
    form.addEventListener("submit", (e) => { e.preventDefault(); dialog.close(); ok(form.querySelector("input:checked")?.value || null); });
    dialog.replaceChildren(form);
    dialog.showModal();
  });
}

async function removeTexts(t) {
  if (!confirm(`Remove “${t.label}” (${t.count} messages)? A copy is kept in your backups.`)) return;
  try { S.her = await api("POST", `/api/personas/${S.editing}/texts/delete`, { file: t.file }); render(); }
  catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ Memory: Stories (the persona's own)

function pageStories() {
  const box = el("div", { class: "list" });
  const draw = (editing = null) => {
    const items = S.her.lore.map((l) => editing === l.file ? storyForm(l, draw) : el("div", { class: "story" },
      el("div", { class: "top" }, el("b", { text: l.title }),
        el("span", { class: "actions" }, el("button", { class: "icon-btn", type: "button", "aria-label": `Edit ${l.title}`, onclick: () => draw(l.file) }, icon("edit")),
          el("button", { class: "icon-btn danger", type: "button", "aria-label": `Remove ${l.title}`, onclick: () => removeStory(l) }, icon("trash")))),
      el("p", { text: l.text }),
      el("div", { class: "kw" }, l.keywords.map((k) => el("span", { class: "pill", text: k })))));
    if (!items.length && editing !== "new") items.push(el("div", { class: "empty", text: "No stories yet." }));
    box.replaceChildren(...items, editing === "new" ? storyForm(null, draw)
      : el("div", { class: "add-line" }, el("button", { class: "btn", type: "button", onclick: () => draw("new") }, icon("plus"), "Add a story")));
  };
  draw();
  return el("div", {},
    head("Stories", `Memories, sayings and family facts ${assistant()} brings up when one of their words is said. Each persona has its own.`),
    editingBanner(),
    card(null, null, box));
}

function storyForm(l, draw) {
  const title = el("input", { type: "text", value: l?.title || "", placeholder: "Sunday jollof", "aria-label": "Title" });
  let words = l?.keywords || [];
  const text = el("textarea", { class: "short", value: l?.text || "", placeholder: "Every Sunday the whole family…", "aria-label": "The story" });
  return el("div", { class: "story stack" },
    title, chips(words, (v) => (words = v), "Words that bring it up", "Words that bring it up"), text,
    el("div", { class: "actions end" }, el("button", { class: "btn", type: "button", text: "Cancel", onclick: () => draw() }),
      el("button", { class: "btn primary", type: "button", text: "Save story", onclick: async () => {
        try {
          S.her = await api("POST", `/api/personas/${S.editing}/lore`, { title: title.value, keywords: words, text: text.value, old_file: l?.file });
          toast("Story saved");
          render();
        } catch (e) { toast(e.message, true); }
      } })));
}

async function removeStory(l) {
  if (!confirm(`Remove “${l.title}”? A copy is kept in your backups.`)) return;
  try { S.her = await api("POST", `/api/personas/${S.editing}/lore/delete`, { file: l.file }); render(); }
  catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ the persona's actions (the switcher's menu)

async function showPrompt() {
  const dialog = document.getElementById("dialog");
  const body = el("div", {}, el("p", { class: "muted", text: "Loading…" }));
  dialog.replaceChildren(el("form", { method: "dialog" },
    el("h2", { id: "dialog-title", text: `${assistant()}'s prompt` }), body,
    el("div", { class: "actions end" }, el("button", { class: "btn primary", type: "submit", text: "Close" }))));
  dialog.showModal();
  try {
    const p = await api("GET", `/api/personas/${S.editing}/prompt`);
    body.replaceChildren(el("p", { class: "help", style: "margin:0 0 10px", text: `Everything ${assistant()} is told at the start, as last saved: about ${p.tokens.toLocaleString()} tokens, ${p.persona_tokens.toLocaleString()} of them from this persona, plus the tools' descriptions. Shorter prompts start faster.` }),
      el("pre", { class: "prompt", text: p.text }));
  } catch (e) { body.replaceChildren(el("p", { class: "muted", text: e.message })); }
}

// With a voice ID set, ask whether the card should carry it; null when cancelled.
function askVoiceId() {
  return new Promise((ok) => {
    const dialog = document.getElementById("dialog");
    const box = el("input", { type: "checkbox" });
    const form = el("form", { method: "dialog" },
      el("h2", { id: "dialog-title", text: "Export as a character card" }),
      el("div", { class: "radio-list" }, el("label", {}, box, el("span", { class: "grow", text: "Include the ElevenLabs voice ID. Only share a cloned voice with people you'd trust with it." }))),
      el("div", { class: "actions end" }, el("button", { class: "btn", type: "button", text: "Cancel", onclick: () => { dialog.close(); ok(null); } }),
        el("button", { class: "btn primary", type: "submit", text: "Export" })));
    form.addEventListener("submit", (e) => { e.preventDefault(); dialog.close(); ok(box.checked); });
    dialog.replaceChildren(form);
    dialog.showModal();
  });
}

async function exportCard() {
  const key = S.her.key;
  const withVoice = voiceNow().elevenlabs.voice_id ? await askVoiceId() : false;
  if (withVoice === null) return;
  try {
    const json = await api("GET", `/api/personas/${key}/card${withVoice ? "?voice=1" : ""}`);
    download(`${key}.card.json`, JSON.stringify(json, null, 2));
  } catch (e) { toast(e.message, true); }
}

async function usePersona(key) {
  try {
    if (Object.keys(S.draft.persona).length && !(await save(true))) return;
    S.state = await api("POST", `/api/personas/${key}/use`, {});
    S.her = await api("GET", `/api/personas/${key}`);
    S.saved = true;
    toast(`${S.her.name} is now active`);
    render();
  } catch (e) { toast(e.message, true); }
}

async function removePersona() {
  if (!confirm(`Remove ${assistant()}? The folder is moved to your backups, not deleted.`)) return;
  try {
    S.state = await api("POST", `/api/personas/${S.editing}/delete`, {});
    S.draft.persona = {};
    S.editing = S.state.settings.persona;
    S.her = await api("GET", `/api/personas/${S.editing}`);
    location.hash = "identity";
    render();
  } catch (e) { toast(e.message, true); }
}

async function importCard(file, as) {
  file = file || await pickFile(".json,.png");
  if (!file) return;
  try {
    const out = await api("POST", "/api/personas/import", { filename: file.name, data: await readB64(file), as });
    S.state = out.state;
    S.her = await api("GET", `/api/personas/${out.key}`);
    S.editing = out.key;
    S.draft.persona = {};
    location.hash = "identity";
    toast(`Imported ${S.her.name}`);
    render();
  } catch (e) {
    if (/already have/.test(e.message)) {
      const name = prompt("A persona with that name exists. Short name for this one (a-z, 0-9):");
      if (name) return importCard(file, slug(name));
    }
    toast(e.message, true);
  }
}

function openNewPersona(mode = "questions") {
  const dialog = document.getElementById("dialog");
  const pick = (value, text) => el("label", {}, el("input", { type: "radio", name: "mode", value, checked: value === mode || undefined }), el("span", { text }));
  const name = el("input", { type: "text", placeholder: "Ama" });
  const who = el("input", { type: "text", placeholder: "my grandmother, a wise mentor, a cheerful assistant" });
  let nicknames = [];
  const callsYou = el("input", { type: "text", value: S.state.profile.name || "", placeholder: "Sam" });
  let preset = null;
  const presetBox = el("div", { class: "presets", style: "margin:0" });
  presetBox.append(...S.state.settings.presets.map((p) => el("button", { type: "button", text: p.label, onclick: (e) => {
    preset = preset === p ? null : p;
    for (const b of presetBox.children) b.style.cssText = "";
    if (preset) e.currentTarget.style.cssText = "border-color:var(--accent);color:var(--accent)";
  } })));
  const traits = [0, 1, 2].map((i) => el("input", { type: "text", placeholder: ["warm", "funny", "patient"][i], "aria-label": `Word ${i + 1}` }));
  const sayings = el("textarea", { class: "short", placeholder: "Have you eaten?\nTake your time." });
  const storyTitle = el("input", { type: "text", placeholder: "The garden" });
  let storyWords = [];
  const storyText = el("textarea", { class: "short", placeholder: "There were tomatoes behind the house…" });
  const short = el("input", { type: "text", pattern: "[a-z0-9][a-z0-9_\\-]{0,39}", value: mode === "copy" ? slug(`${S.her.key}_copy`) : "" });
  let touched = mode === "copy";
  short.addEventListener("input", () => (touched = true));
  name.addEventListener("input", () => { if (!touched) short.value = slug(name.value); });

  const questions = el("div", { class: "stack", style: "gap:16px" },
    el("label", {}, "Name", name),
    el("label", {}, "Who are they to you?", who),
    el("div", { class: "field" }, "What do you call them?", chips([], (v) => (nicknames = v), "Add a nickname", "Nicknames")),
    el("label", {}, "What should they call you?", callsYou),
    el("div", { class: "field" }, "Personality", presetBox, el("div", { class: "three" }, traits)),
    el("label", {}, "Things they often say", sayings),
    el("div", { class: "field" }, "A story (optional)", storyTitle, chips([], (v) => (storyWords = v), "Words that bring it up", "Words that bring it up"), storyText));
  const form = el("form", { method: "dialog" },
    el("h2", { id: "dialog-title", text: "New persona" }),
    el("div", { class: "radio-list" }, pick("questions", "Answer a few questions"), pick("template", "Start blank"), pick("copy", `Duplicate ${S.her.name}`)),
    questions,
    el("label", {}, "Short name", short),
    el("div", { class: "actions end" }, el("button", { class: "btn", type: "button", text: "Cancel", onclick: () => dialog.close() }),
      el("button", { class: "btn primary", type: "submit", text: "Create" })));
  const modeNow = () => form.querySelector("input[name=mode]:checked").value;
  const sync = () => { questions.hidden = modeNow() !== "questions"; };
  form.addEventListener("change", (e) => { if (e.target.name === "mode") sync(); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const m = modeNow();
    if (m === "questions" && !name.value.trim()) { toast("Give the persona a name.", true); return name.focus(); }
    const body = { key: short.value.trim() || slug(name.value) };
    if (!body.key) { toast("Give it a short name.", true); return short.focus(); }
    if (m === "copy") body.copy_from = S.her.key;
    if (m === "questions") {
      body.answers = { name: name.value.trim(), who: who.value.trim(), nicknames, soul: preset ? preset.text : "",
        traits: traits.map((t) => t.value.trim()).filter(Boolean), sayings: sayings.value.split("\n").map((s) => s.trim()).filter(Boolean),
        story: { title: storyTitle.value.trim(), keywords: storyWords, text: storyText.value.trim() } };
      if (callsYou.value.trim() !== (S.state.profile.name || "")) body.calls_you = callsYou.value.trim();
    }
    try {
      const out = await api("POST", "/api/personas/new", body);
      S.state = out.state;
      S.her = await api("GET", `/api/personas/${out.key}`);
      S.editing = out.key;
      S.draft.persona = {};
      dialog.close();
      location.hash = "identity";
      toast(`Created ${S.her.name}`);
      render();
    } catch (err) { toast(err.message, true); }
  });
  dialog.replaceChildren(form);
  sync();
  dialog.showModal();
  (mode === "questions" ? name : short).focus();
}

// ------------------------------------------------------------------ Voice

function voiceTile(v, selected, onPick, source) {
  const play = el("button", { class: "play", type: "button", "aria-label": `Play ${v.name}` }, icon("play"));
  play.addEventListener("click", (e) => { e.stopPropagation(); Player.play(play, source); });
  return el("div", { class: "tile", role: "radio", tabindex: "0", "aria-checked": String(selected), onclick: onPick,
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(); } } },
    el("div", { class: "grow" }, el("b", { text: v.name }), el("span", { text: v.detail })),
    v.recommended && el("span", { class: "pill accent", text: "Best" }), play);
}

const sampleText = () => greetingPreview() || `Hi, I'm ${assistant()}.`;

async function kokoroSample(voice) {
  return URL.createObjectURL(await api("POST", "/api/sample", { provider: "kokoro", voice, speed: voiceNow().speed, text: sampleText() }));
}

function cardHead(title, sub, right) {
  return el("div", { class: "card-head", style: "padding-top:0" }, el("div", {}, el("h2", {}, title, info(sub))), right);
}

function pageVoice() {
  const v = voiceNow();
  const cloud = v.provider === "elevenlabs";
  const kokoroCard = el("section", { class: "card pad" });
  if (cloud) {
    const backup = select([[v.voice, v.voice]], v.voice, (x) => setVoice("voice", x), "Backup voice", true);
    choices("kokoro").then((c) => backup.fill(c.items.map((x) => [x.id, `${x.name} · ${x.detail}`])));
    kokoroCard.append(cardHead("Backup voice", "On this PC, for when ElevenLabs can't be reached."), row("Voice", null, backup));
  } else {
    const gallery = el("div", { class: "tiles" }, el("p", { class: "muted", text: "Loading voices…" }));
    const draw = (items) => gallery.replaceChildren(...items.filter((x) => S.voiceFilter === "all" || x.gender === S.voiceFilter)
      .map((x) => voiceTile(x, x.id === voiceNow().voice, () => { setVoice("voice", x.id); render(); }, () => kokoroSample(x.id))));
    choices("kokoro").then((c) => draw(c.items));
    const filters = segmented([["all", "All"], ["female", "Female"], ["male", "Male"]], S.voiceFilter, (f) => { S.voiceFilter = f; choices("kokoro").then((c) => draw(c.items)); }, "Show voices");
    const speedValue = el("span", { class: "muted", text: `${Number(v.speed).toFixed(2)}×` });
    const speed = el("input", { type: "range", min: 0.5, max: 2, step: 0.05, value: v.speed, "aria-label": "Speaking speed" });
    speed.addEventListener("input", () => { speedValue.textContent = `${Number(speed.value).toFixed(2)}×`; setVoice("speed", Number(speed.value)); });
    kokoroCard.append(cardHead("Voices on this PC", "Free and private. Press play to hear one.", filters), gallery,
      el("div", { class: "row", style: "margin-top:12px" }, el("div", {}, el("div", { class: "label", text: "Speaking speed" })), el("div", { class: "control" }, speed, speedValue)));
  }

  let cloudCard = null;
  if (cloud && !S.state.keys.elevenlabs) {
    cloudCard = el("section", { class: "card pad" }, cardHead("Connect ElevenLabs", "Add your ElevenLabs key to use your own or cloned voices."),
      el("a", { class: "btn primary", href: "#connections", text: "Go to Connections" }));
  } else if (cloud) {
    const tiles = el("div", { class: "tiles" }, el("p", { class: "muted", text: "Loading your voices…" }));
    const note = el("p", { class: "help" });
    choices("elevenlabs").then((c) => {
      note.textContent = c.note || "";
      tiles.replaceChildren(...c.items.map((x) => voiceTile(x, x.id === voiceNow().elevenlabs.voice_id, () => { setVoice("elevenlabs.voice_id", x.id); render(); },
        x.preview || (async () => { throw new Error("This voice has no free preview. Try a sentence below."); }))));
    });
    const credits = el("div", { class: "credits" });
    api("GET", "/api/credits").then((c) => {
      if (!c.known) return;
      const left = Math.max(0, c.limit - c.used);
      credits.replaceChildren(el("span", { text: `${left.toLocaleString()} of ${c.limit.toLocaleString()} credits left · renews ${new Date(c.resets * 1000).toLocaleDateString()}` }),
        el("div", { class: "bar-meter" }, el("i", { style: `width:${Math.min(100, (left / c.limit) * 100).toFixed(1)}%` })));
    }).catch(() => {});
    const id = textInput(v.elevenlabs.voice_id, (x) => setVoice("elevenlabs.voice_id", x.trim()), "Voice ID", "Voice ID");
    cloudCard = el("section", { class: "card pad" }, cardHead("Your ElevenLabs voices", "Previews are free. Speech uses your plan's credits."),
      tiles, note,
      el("div", { class: "row", style: "margin-top:8px" }, el("div", {}, el("div", { class: "label", text: "Quality" }), el("div", { class: "help", text: "Fast uses half the credits." })),
        el("div", { class: "control" }, segmented([["eleven_v4_turbo", "Fast"], ["eleven_v4", "Best"]], v.elevenlabs.model, (m) => setVoice("elevenlabs.model", m), "Quality"))),
      el("div", { class: "row" }, el("div", {}, el("div", { class: "label", text: "Credits" })), el("div", { class: "control", style: "display:block" }, credits)),
      el("details", { class: "more" }, el("summary", {}, "Use a voice ID", icon("chevron")), el("div", { style: "padding-bottom:8px" }, id)));
  }

  const tryText = el("input", { type: "text", value: sampleText(), "aria-label": "Sentence to try" });
  const tryBtn = el("button", { class: "play", type: "button", "aria-label": "Play this sentence" }, icon("play"));
  tryBtn.addEventListener("click", () => Player.play(tryBtn, async () => {
    const now = voiceNow();
    const text = tryText.value.trim();
    if (now.provider === "elevenlabs" && S.state.keys.elevenlabs) {
      const cost = Math.ceil(text.length * (now.elevenlabs.model === "eleven_v4" ? 1 : 0.5));
      if (!confirm(`This uses about ${cost} ElevenLabs credits. Play it?`)) throw new Error("");
      return URL.createObjectURL(await api("POST", "/api/sample", { provider: "elevenlabs", text, voice_id: now.elevenlabs.voice_id, model: now.elevenlabs.model }));
    }
    return URL.createObjectURL(await api("POST", "/api/sample", { provider: "kokoro", text, voice: now.voice, speed: now.speed }));
  }));

  return el("div", {},
    head("Voice", `How ${assistant()} sounds.`),
    editingBanner(),
    S.state.settings.voice_override.length > 0 && el("div", { class: "banner" }, el("span", { class: "grow",
      text: `Your config file sets the voice (tts: ${S.state.settings.voice_override.join(", ")}), which overrides this persona's. Remove those lines to use the voice chosen here.` })),
    el("div", { style: "margin-bottom:18px" }, segmented([["kokoro", "On this PC"], ["elevenlabs", "ElevenLabs"]], v.provider, (p) => { setVoice("provider", p); render(); }, "Voice provider")),
    cloudCard, kokoroCard,
    card("Try a sentence", null, el("div", { class: "add-line", style: "border-top:0;padding-top:4px" }, tryText, tryBtn)));
}

// ------------------------------------------------------------------ Wake word

const WAKE_STATUS = { "ready": ["good", "Ready"], "built-in": ["accent", "Built-in"], "needs training": ["warn", "Needs training"], "needs more takes": ["warn", "Needs more takes"] };
const BUILT_IN = [["hey_jarvis", "Hey Jarvis"], ["alexa", "Alexa"], ["hey_mycroft", "Hey Mycroft"], ["hey_rhasspy", "Hey Rhasspy"]];
const isBuiltIn = (model) => BUILT_IN.some(([m]) => m === model);

function statusOf(w) {
  const saved = S.her.wake.find((x) => x.model === w.model);
  if (saved) return saved.status;
  return isBuiltIn(w.model) ? "built-in" : "needs training";
}

function pageWake() {
  if (S.trainer) return pageTrainer();
  const list = wakeNow();
  const rows = list.map((w, i) => {
    const status = statusOf(w);
    const [tone, label] = WAKE_STATUS[status] || ["", status];
    const train = status === "needs training" || status === "needs more takes";
    return el("div", { class: "item", style: "flex-wrap:wrap" },
      el("div", { class: "grow", style: "font-weight:600", text: `“${w.phrase}”` }),
      el("span", { class: `pill ${tone}`, text: label }),
      train && el("button", { class: "btn small primary", type: "button", onclick: () => openTrainer(w.phrase) }, icon("mic"), status === "needs training" ? "Train" : "Record more"),
      status === "ready" && el("button", { class: "btn small", type: "button", text: "Retrain", onclick: () => openTrainer(w.phrase) }),
      el("button", { class: "icon-btn danger", type: "button", "aria-label": `Remove ${w.phrase}`, onclick: () => { const l = clone(wakeNow()); l.splice(i, 1); setWake(l); render(); } }, icon("trash")),
      !isBuiltIn(w.model) && el("details", { class: "more", style: "width:100%;margin:0" },
        el("summary", { style: "padding:2px 0;font-size:13.5px" }, "Spellings", icon("chevron")),
        chips(w.names || [], (v) => { const l = clone(wakeNow()); l[i].names = v; setWake(l); }, "Add a spelling", "Spellings")));
  });
  const phrase = el("input", { type: "text", placeholder: "Hey Grandma", "aria-label": "Your own wake phrase" });
  const addOwn = () => {
    const text = phrase.value.trim().replace(/\s+/g, " ").replace(/(^|\s)(\p{L})/gu, (m, a, b) => a + b.toUpperCase());
    const name = text.replace(GREETING, "");
    if (!name) return toast("Add a name to the phrase, like “Hey Grandma”.", true);
    const model = slug(text);
    if (wakeNow().some((w) => w.model === model)) return toast("That phrase is already there.", true);
    setWake([...clone(wakeNow()), { phrase: text, model, names: [name.toLowerCase()], alone: [], misheard: [], bare: false }]);
    render();
  };
  phrase.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addOwn(); } });
  const builtins = BUILT_IN.filter(([m]) => !list.some((w) => w.model === m));
  return el("div", {},
    head("Wake word", `What you say to get ${assistant()}'s attention.`),
    editingBanner(),
    !settingNow("wakeword.enabled") && el("div", { class: "banner" },
      el("span", { class: "grow", text: "The wake word is off, so only the hotkey starts a conversation." }),
      el("a", { class: "btn small", href: "#listening", text: "Turn it on in Listening" })),
    card("Wake phrases", `${assistant()} answers to these. A phrase of your own is trained on your voice, right here in the browser.`,
      el("div", { class: "list" }, rows.length ? rows : [el("div", { class: "empty", text: "No wake phrase yet. Add one below." })]),
      el("div", { class: "add-line" }, phrase, el("button", { class: "btn", type: "button", onclick: addOwn }, icon("plus"), "Add")),
      builtins.length > 0 && el("div", { style: "padding:0 0 18px" }, el("h3", { text: "Ready-made, no training needed" }),
        el("div", { class: "presets", style: "margin:0" }, builtins.map(([m, p]) => el("button", { type: "button", text: p, onclick: () => {
          setWake([...clone(wakeNow()), { phrase: p, model: m, names: [p.replace(GREETING, "").toLowerCase()], alone: [], misheard: [], bare: false }]);
          render();
        } }))))));
}

async function openTrainer(phrase) {
  if (dirty() && !(await save(true))) return;   // the phrase is saved to the persona before it's trained
  try {
    const plan = await api("GET", `/api/wake/plan?phrase=${encodeURIComponent(phrase)}`);
    const running = !!(plan.training && plan.training.running);
    S.trainer = { plan, step: running ? "train" : "intro", more: plan.status === "needs more takes" || plan.status === "ready",
      style: 0, inStyle: 0, near: 0, state: "", stateClass: "", busy: false, training: plan.training };
    location.hash = "wake";
    render();
    if (running) pollTraining();
  } catch (e) { toast(e.message, true); }
}

// The microphone, through an AudioWorklet (recorder.js): raw sound, without echo cancelling or noise suppression,
// as MARY hears it. Recordings go to this PC's MARY only.
const Mic = {
  ctx: null, stream: null, chunks: null, level: 0, meters: new Set(),
  async open() {
    if (this.ctx) return;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/recorder.js");
    const source = this.ctx.createMediaStreamSource(this.stream);
    const node = new AudioWorkletNode(this.ctx, "mary-recorder");
    node.port.onmessage = (e) => {
      const block = e.data;
      let peak = 0;
      for (let i = 0; i < block.length; i++) peak = Math.max(peak, Math.abs(block[i]));
      this.level = Math.max(peak, this.level * 0.92);
      if (this.chunks) this.chunks.push(block);
      for (const m of this.meters) m.style.width = `${Math.min(100, this.level * 140)}%`;
    };
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    source.connect(node).connect(mute).connect(this.ctx.destination);  // pulled through, but silent
  },
  close() {
    if (!this.ctx) return;
    this.stream.getTracks().forEach((t) => t.stop());
    this.ctx.close();
    Object.assign(this, { ctx: null, stream: null, chunks: null, level: 0 });
  },
  meter() {
    const bar = el("i");
    this.meters.add(bar);
    return el("div", { class: "level" }, bar);
  },
  async beep() {
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, this.ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.18, this.ctx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, this.ctx.currentTime + 0.09);
    osc.connect(gain).connect(this.ctx.destination);
    osc.start();
    osc.stop(this.ctx.currentTime + 0.1);
    await sleep(140);
  },
  start() { this.chunks = []; },
  stop() {
    const chunks = this.chunks || [];
    this.chunks = null;
    const n = chunks.reduce((a, c) => a + c.length, 0);
    const pcm = new Int16Array(n);
    let o = 0;
    for (const c of chunks) for (let i = 0; i < c.length; i++) pcm[o++] = Math.max(-32768, Math.min(32767, c[i] * 32768));
    return { data: bytesToB64(new Uint8Array(pcm.buffer)), rate: this.ctx.sampleRate };
  },
  async record(seconds) {
    this.start();
    await sleep(seconds * 1000);
    return this.stop();
  },
};

function leaveTrainer() {
  S.trainer = null;
  clearTimeout(pollTimer);
  Mic.close();
  Mic.meters.clear();
  api("POST", "/api/wake/pause", { seconds: 0 }).catch(() => {});
  render();
}

function trainerSteps() {
  const t = S.trainer;
  const steps = [["intro", "Microphone"], ["takes", "Your phrase"]];
  if (!t.more) steps.push(["near", "Sound-alikes"]);
  if (!t.more && (t.plan.need_speech || t.plan.need_room)) steps.push(["extra", "Reading and room"]);
  steps.push(["train", "Training"]);
  return steps;
}

function nextStep() {
  const steps = trainerSteps().map((s) => s[0]);
  S.trainer.step = steps[steps.indexOf(S.trainer.step) + 1] || "train";
  S.trainer.state = "";
  render();
}

function pageTrainer() {
  const t = S.trainer;
  const steps = trainerSteps();
  const at = steps.findIndex((s) => s[0] === t.step);
  Mic.meters.clear();
  const view = { intro: trainIntro, takes: trainTakes, near: trainNear, extra: trainExtra, train: trainRun }[t.step]();
  return el("div", {},
    el("button", { class: "btn ghost", type: "button", style: "margin:-8px 0 10px -10px", onclick: leaveTrainer }, icon("back"), "Wake word"),
    head(`Train “${t.plan.phrase}”`, t.more ? "Record a few more takes, then train again." : "About 5 minutes of recording, then 10 to 20 minutes of training while you carry on."),
    el("div", { class: "steps" }, steps.map(([id, label], i) => el("span", { class: i === at ? "now" : i < at ? "done" : "", text: `${i + 1}. ${label}` }))),
    view);
}

function trainIntro() {
  const t = S.trainer;
  const ready = !!Mic.ctx;
  return el("section", { class: "card pad" },
    el("h2", { text: ready ? "Microphone ready" : "Allow the microphone" }),
    el("p", { class: "help", text: ready ? "Say something: the bar should move. Use the microphone MARY listens with, from where you usually talk."
      : "The browser asks once. Recordings stay on this PC, in MARY's data folder, and are only used to train this wake word." }),
    ready ? Mic.meter() : null,
    el("div", { class: "actions", style: "margin-top:18px" }, ready
      ? el("button", { class: "btn primary", type: "button", text: "Start recording", onclick: nextStep })
      : el("button", { class: "btn primary", type: "button", onclick: async () => {
        try { await Mic.open(); render(); } catch { toast("The microphone isn't available. Allow it from the browser's address bar.", true); }
      } }, icon("mic"), "Allow microphone"),
    t.more && t.plan.takes >= t.plan.min_takes && el("button", { class: "btn", type: "button", text: "Skip to training", onclick: () => { t.step = "train"; render(); } })));
}

function sayCard(prompt, how, extra, buttons) {
  const t = S.trainer;
  return el("section", { class: "card pad" },
    el("div", { class: "say" }, el("div", { class: "prompt", text: prompt }), el("div", { class: "how", text: how }),
      el("div", { class: `state ${t.stateClass || ""}`, text: t.state || "" }), Mic.meter()),
    extra, el("div", { class: "actions", style: "justify-content:center;margin-top:16px" }, buttons));
}

function showState(text, cls = "") {
  S.trainer.state = text;
  S.trainer.stateClass = cls;
  const s = document.querySelector(".say .state");
  if (s) { s.textContent = text; s.className = `state ${cls}`; }
}

async function takeOnce(kind, seconds, lead) {
  showState("Get ready…");
  await sleep(lead * 1000);
  await Mic.beep();
  showState("Recording…", "rec");
  const audio = await Mic.record(seconds);
  showState("Checking…");
  const out = await api("POST", "/api/wake/take", { phrase: S.trainer.plan.phrase, kind, ...audio });
  showState(out.ok ? "Got it" : out.why, out.ok ? "ok" : "again");
  return out;
}

function trainTakes() {
  const t = S.trainer;
  const styles = t.more ? [{ how: "in your normal voice", count: 10 }] : t.plan.styles;
  const style = styles[Math.min(t.style, styles.length - 1)];
  const total = styles.reduce((a, s) => a + s.count, 0);
  const before = styles.slice(0, t.style).reduce((a, s) => a + s.count, 0);
  const far = style.how.includes("metres");
  const progress = () => {
    const count = document.querySelector(".take-count");
    if (count) count.textContent = `Take ${Math.min(t.inStyle + 1, style.count)} of ${style.count} · ${before + t.inStyle} of ${total} in all`;
    const fill = document.querySelector(".take-bar i");
    if (fill) fill.style.width = `${((before + t.inStyle) / total) * 100}%`;
  };
  const run = async () => {
    t.busy = true;
    render();
    api("POST", "/api/wake/pause", { seconds: 120 }).catch(() => {});
    try {
      if (far) await sleep(4000);
      let tries = 0;
      while (S.trainer === t && t.busy && t.inStyle < style.count && tries < style.count * 3) {
        tries++;
        const out = await takeOnce("positive", 3, 0.6);
        if (out.ok) { t.inStyle++; t.plan.takes = out.takes; }
        if (S.trainer !== t) return;
        progress();
        await sleep(500);
      }
      if (S.trainer !== t) return;
      t.busy = false;
      if (t.inStyle >= style.count) {
        t.inStyle = 0;
        t.style++;
        t.state = "";
        if (t.style >= styles.length) return nextStep();
      }
      render();
    } catch (e) { t.busy = false; toast(e.message, true); render(); }
  };
  const how = `Say it ${style.how}, after each beep.${far ? " Walk there after pressing Start: the beeps begin a few seconds later." : ""}`;
  return sayCard(`“${t.plan.phrase}”`, how,
    el("div", { class: "stack", style: "max-width:420px;margin:14px auto 0;text-align:center" },
      el("div", { class: "take-count muted", text: `Take ${t.inStyle + 1} of ${style.count} · ${before + t.inStyle} of ${total} in all` }),
      el("div", { class: "bar-meter take-bar" }, el("i", { style: `width:${((before + t.inStyle) / total) * 100}%` }))),
    t.busy ? el("button", { class: "btn", type: "button", text: "Pause", onclick: () => { t.busy = false; showState("Pausing after this take…"); } })
      : el("button", { class: "btn primary", type: "button", text: t.inStyle || t.style ? "Continue" : "Start", onclick: run }));
}

function trainNear() {
  const t = S.trainer;
  const near = t.plan.near;
  const run = async () => {
    t.busy = true;
    render();
    try {
      while (S.trainer === t && t.busy && t.near < near.length) {
        const prompt = document.querySelector(".say .prompt");
        if (prompt) prompt.textContent = `“${near[t.near]}”`;
        const out = await takeOnce("near_miss", 3, 1.2);
        if (out.ok) t.near++;
        await sleep(400);
      }
      if (S.trainer !== t) return;
      t.busy = false;
      if (t.near >= near.length) return nextStep();
      render();
    } catch (e) { t.busy = false; toast(e.message, true); render(); }
  };
  return sayCard(`“${near[Math.min(t.near, near.length - 1)]}”`,
    `Say exactly what's shown, after each beep. These must not wake ${assistant()}. ${t.near} of ${near.length} done.`, null,
    [t.busy ? el("button", { class: "btn", type: "button", text: "Pause", onclick: () => { t.busy = false; } })
      : el("button", { class: "btn primary", type: "button", text: t.near ? "Continue" : "Start", onclick: run }),
    el("button", { class: "btn ghost", type: "button", text: "Skip", onclick: () => { t.busy = false; nextStep(); } })]);
}

function trainExtra() {
  const t = S.trainer;
  const p = t.plan;
  const reading = p.need_speech;
  const seconds = reading ? 25 : 20;
  const run = async () => {
    t.busy = true;
    render();
    try {
      await Mic.beep();
      Mic.start();
      for (let left = seconds; left > 0 && S.trainer === t; left--) {
        showState(`Recording… ${left} s`, "rec");
        await sleep(1000);
      }
      if (S.trainer !== t) return;
      const audio = Mic.stop();
      const out = await api("POST", "/api/wake/take", { phrase: p.phrase, kind: reading ? "speech" : "room", ...audio });
      t.busy = false;
      if (!out.ok) { showState(out.why, "again"); return render(); }
      if (reading) p.need_speech = false; else p.need_room = false;
      t.state = "";
      if (!p.need_speech && !p.need_room) return nextStep();
      render();
    } catch (e) { t.busy = false; toast(e.message, true); render(); }
  };
  return sayCard(reading ? "Read this out" : "Your room's normal sound", reading ? "In your normal voice, about 25 seconds." : "Stay quiet, type, or leave the TV on, for 20 seconds.",
    reading ? el("p", { class: "read", text: p.read_aloud }) : null,
    t.busy ? null : el("button", { class: "btn primary", type: "button", text: "Start", onclick: run }));
}

let pollTimer = null;
async function pollTraining() {
  clearTimeout(pollTimer);
  const t = S.trainer;
  if (!t) return;
  try { t.training = await api("GET", `/api/wake/train?phrase=${encodeURIComponent(t.plan.phrase)}`); } catch { /* keep the last one */ }
  if (S.trainer !== t) return;
  if (t.training && t.training.running) pollTimer = setTimeout(pollTraining, 2000);
  else if (t.training && t.training.done) {
    try {
      S.her = await api("GET", `/api/personas/${S.editing}`);
      S.state = await api("GET", "/api/state");
    } catch { /* shown on the next render */ }
  }
  if (currentPage()[0] === "wake") render();
}

function trainRun() {
  const t = S.trainer;
  const p = t.plan;
  const tr = t.training;
  Mic.close();
  if (tr && tr.running) {
    const pct = Math.round((tr.progress || 0) * 100);
    return el("section", { class: "card pad" }, el("h2", { text: tr.stage || "Training" }),
      el("p", { class: "help", text: "Running in the background at low priority. You can leave this page, or use MARY; she's a little slower meanwhile." }),
      el("div", { class: "bar-meter", style: "margin-top:14px;height:10px" }, el("i", { style: `width:${pct}%` })),
      el("p", { class: "muted", style: "margin:8px 0 0", text: `${pct}%` }));
  }
  if (tr && tr.done && !tr.error && tr.report) {
    const r = tr.report;
    const caught = r.recall_your_voice_heldout == null ? "–" : `${Math.round(r.recall_your_voice_heldout * 100)}%`;
    return el("section", { class: "card pad" },
      el("div", { class: "result" }, el("span", { class: `pill ${tr.passed ? "good" : "warn"}`, text: tr.passed ? "Ready" : "Not good enough yet" }),
        el("div", {}, el("h2", { text: tr.passed ? `“${p.phrase}” is trained` : `“${p.phrase}” needs more takes` }),
          el("p", { class: "help", text: tr.passed ? (S.state.running ? "Restart MARY to start using it." : "It works the next time MARY starts.")
            : `${tr.why ? tr.why[0].toUpperCase() + tr.why.slice(1) : "It missed too many of your takes"}. Record a few more and train again.` }))),
      el("div", { class: "numbers" },
        el("div", { class: "number" }, el("b", { text: caught }), el("span", { text: "of your test takes caught" })),
        el("div", { class: "number" }, el("b", { text: String(r.false_triggers_per_hour_heldout) }), el("span", { text: "false wake-ups an hour, before the double-check" })),
        el("div", { class: "number" }, el("b", { text: String(r.your_recordings) }), el("span", { text: "of your takes used" }))),
      el("div", { class: "actions", style: "margin-top:18px" },
        !tr.passed && el("button", { class: "btn primary", type: "button", text: "Record more takes", onclick: () => { Object.assign(t, { more: true, step: "intro", style: 0, inStyle: 0, training: null }); render(); } }),
        tr.passed && S.state.running && S.state.can_restart && el("button", { class: "btn primary", type: "button", onclick: () => { leaveTrainer(); restart(); } }, icon("restart"), "Restart MARY"),
        el("button", { class: "btn", type: "button", text: "Done", onclick: leaveTrainer })));
  }
  const start = async () => {
    try {
      t.training = await api("POST", "/api/wake/train", { phrase: p.phrase });
      render();
      pollTraining();
    } catch (e) { toast(e.message, true); }
  };
  return el("section", { class: "card pad" },
    el("h2", { text: tr && tr.error ? "Training stopped" : "Ready to train" }),
    el("p", { class: "help", text: tr && tr.error ? tr.error : `${p.takes} takes recorded. Training takes 10 to 20 minutes on this PC, at low priority.` }),
    p.download && el("div", { class: "banner info", style: "margin:14px 0 0" }, el("span", { class: "grow", text: "The first training downloads 185 MB of background sounds from openWakeWord (on Hugging Face). Only once." })),
    el("div", { class: "actions", style: "margin-top:18px" }, el("button", { class: "btn primary", type: "button", text: "Start training", onclick: start }),
      el("button", { class: "btn", type: "button", text: "Record more first", onclick: () => { Object.assign(t, { more: true, step: "intro", style: 0, inStyle: 0 }); render(); } })));
}

// ------------------------------------------------------------------ Memory: About you, Photos

function pageAbout() {
  const p = profileNow();
  const memories = (text) => text.split("\n").map((line) => line.trim()).filter(Boolean);  // one memory per line
  return el("div", {},
    el("div", { class: "head" }, avatar(p.name || "You", "large"), el("div", { class: "grow" }, el("h1", { text: p.name || "About you" }))),
    el("section", { class: "card" },
      row("Your name", `What ${assistant()} calls you, now and then.`, textInput(p.name, (v) => setProfile("name", v), "Your name", "Sam"))),
    card("About you", `Write about yourself in your own words: what you do, the people in your life, what you like. ${assistant()} always has it in mind.`,
      writingBox(p.about || "", (v) => setProfile("about", v.trim()), "About you", "I'm a teacher in … I live with … I like …")),
    card("Memories", `One per line. ${assistant()} adds a line when you say “remember that…”, and takes one away when you say “forget that…”.`,
      writingBox((p.facts || []).join("\n"), (v) => setProfile("facts", memories(v)), "Memories", "One per line: My sister's name is Ada", "short")),);
}

// ------------------------------------------------------------------ Memory: Photos

const PH = { filters: { year: "", month: "", place: "", folder: "", flag: "", person: "", q: "", order: "newest" }, picked: new Set(), timer: 0, ids: [] };
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DATE_FROM = { exif: "from the camera", filename: "from the file's name", album: "from the folder's date",
  person: "set by you", folder: "from the folder's name", file: "from the file's date", none: "unknown" };
const FLAG_TEXT = { undated: "No date", guess: "Date is a guess", check: "Needs a look", copies: "Copies", noplace: "No place" };
const ROW_PX = 190;   // how tall a row of photos is aimed to be

// A date as exact as you know it: a year, then a month, then a day, or a whole decade ("the 1990s").
function dateEditor(onSave, saveLabel = "Save the date", extra = null) {
  const year = el("input", { type: "number", min: "1900", max: String(new Date().getFullYear()), placeholder: "Year", "aria-label": "Year", style: "width:90px" });
  const month = select([[0, "Month"], ...MONTHS.map((m, i) => [i + 1, m])], 0, () => {}, "Month");
  const day = el("input", { type: "number", min: "1", max: "31", placeholder: "Day", "aria-label": "Day", style: "width:70px" });
  const decade = el("input", { type: "checkbox", id: "decade", "aria-label": "A whole decade" });
  return el("div", { class: "stack" },
    el("div", { class: "actions" }, year, month, day),
    el("label", { class: "check" }, decade, " Sometime in that decade"),
    extra,
    el("div", { class: "actions end" }, el("button", { class: "btn primary small", type: "button", text: saveLabel, onclick: () => {
      onSave({ year: year.value, month: month.selectedIndex || null, day: day.value || null, decade: decade.checked });
    } })));
}

function placeEditor(onSave) {
  const input = el("input", { type: "text", placeholder: "Where was it?", "aria-label": "Place" });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") onSave(input.value); });
  return el("div", { class: "actions" }, input, el("button", { class: "btn primary small", type: "button", text: "Save", onclick: () => onSave(input.value) }));
}

// The heading a photo sits under, like Google Photos: a day when the day is known, else its month, year or decade.
function dayLabel(p) {
  if (!p.taken) return "No date";
  const d = new Date(p.taken);
  if (p.precision === "second" || p.precision === "day") return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  if (p.precision === "month") return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  return p.when;
}

function progressText(p) {
  if (p.state === "scanning") return "Looking through your folders…";
  if (p.reading > 0) return `Reading photos · ${(p.found - p.reading).toLocaleString()} of ${p.found.toLocaleString()}`;
  if (p.faces && p.faces.on && p.faces.ready && p.state === "faces") return "Looking for faces…";
  if (p.text && p.ocr && p.text_left > 0) return `Reading the words in photos · ${(p.photos - p.text_left).toLocaleString()} of ${p.photos.toLocaleString()}`;
  return "";
}

function pagePhotos() {
  clearInterval(PH.timer);
  const st = { s: null, items: [], total: 0, more: false, loading: false, last: null, tiles: null, token: 0 };
  const top = el("div", { class: "gp-top" });
  const editor = el("div", { class: "gp-editor", hidden: true });
  const chipRow = el("div", { class: "gp-chips" });
  const status = el("div", { class: "gp-status", hidden: true });
  const body = el("div", { class: "gp-body" });
  const end = el("div", { class: "gp-end" });
  const page = el("div", { class: "gp" }, top, editor, chipRow, status, body, end);
  const observer = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting) && st.more && !st.loading) loadMore(); }, { rootMargin: "600px" });
  observer.observe(end);

  const reload = async () => {
    try { st.s = await api("GET", "/api/photos"); drawAll(); }
    catch (e) { body.replaceChildren(el("p", { class: "muted loading", text: e.message })); }
  };
  PH.refresh = reload;
  const act = async (path, payload, done) => {
    try { await api("POST", path, payload); if (done) toast(done); await reload(); return true; } catch (e) { toast(e.message, true); return false; }
  };

  // ---- the grid
  const tile = (p) => {
    const ar = p.width && p.height ? p.width / p.height : 1.5;
    const box = el("div", { class: "gp-tile" + (PH.picked.has(p.id) ? " picked" : ""), style: `flex:${(ar * 100).toFixed(1)} 1 ${(ar * ROW_PX).toFixed(0)}px` });
    const check = el("button", { type: "button", class: "gp-check", "aria-label": "Select this photo", "aria-pressed": String(PH.picked.has(p.id)), onclick: (e) => {
      e.stopPropagation();
      PH.picked.has(p.id) ? PH.picked.delete(p.id) : PH.picked.add(p.id);
      box.classList.toggle("picked", PH.picked.has(p.id));
      check.setAttribute("aria-pressed", String(PH.picked.has(p.id)));
      drawTop();
    } }, icon("check"));
    const open = el("button", { type: "button", class: "gp-open", "aria-label": `${p.when}${p.place_label ? ", " + p.place_label : ""}`, onclick: () => (PH.picked.size ? check.click() : photoDialog(p.id)) },
      el("i", { style: `padding-bottom:${(100 / ar).toFixed(2)}%` }), el("img", { src: p.thumb, alt: "", loading: "lazy" }));
    box.append(open, check);
    return box;
  };

  const addItems = (items) => {
    for (const p of items) {
      const label = dayLabel(p);
      if (st.last !== label) {
        st.last = label;
        st.tiles = el("div", { class: "gp-tiles" });
        body.append(el("section", { class: "gp-group" }, el("h3", { text: label }), st.tiles));
      }
      st.tiles.append(tile(p));
      PH.ids.push(p.id);
    }
  };

  const loadMore = async (fresh = false) => {
    if (st.loading) return;
    st.loading = true;
    const token = fresh ? ++st.token : st.token;
    if (fresh) { st.items = []; st.last = null; st.tiles = null; PH.ids = []; body.replaceChildren(); }
    const f = PH.filters;
    const qs = new URLSearchParams({ order: f.order, offset: String(st.items.length) });
    for (const k of ["year", "month", "place", "folder", "flag", "person", "q"]) if (f[k]) qs.set(k, f[k]);
    try {
      const r = await api("GET", `/api/photos/list?${qs}`);
      if (token !== st.token) return;
      st.items.push(...r.items);
      st.more = r.more;
      st.total = r.total;
      addItems(r.items);
      if (!st.items.length) body.replaceChildren(el("div", { class: "gp-nothing" }, icon("search"), el("div", { text: "Nothing matches" }),
        el("button", { class: "btn small", type: "button", text: "Clear filters", onclick: () => { Object.assign(PH.filters, { year: "", month: "", place: "", folder: "", flag: "", person: "", q: "" }); drawTop(); drawChips(); loadMore(true); } })));
    } catch (e) { if (token === st.token) body.replaceChildren(el("div", { class: "gp-nothing", text: e.message })); }
    finally { st.loading = false; }
    if (st.more && end.getBoundingClientRect().top < window.innerHeight + 400) loadMore();
  };

  // ---- the top: search, or what to do with the photos picked
  const drawTop = () => {
    const ids = [...PH.picked];
    editor.hidden = true;
    editor.replaceChildren();
    if (ids.length) {
      const show = (node) => { editor.hidden = false; editor.replaceChildren(node); };
      top.replaceChildren(el("div", { class: "gp-select" },
        el("button", { class: "icon-btn", type: "button", "aria-label": "Clear the selection", onclick: () => { PH.picked.clear(); body.querySelectorAll(".gp-tile.picked").forEach((n) => { n.classList.remove("picked"); n.querySelector(".gp-check").setAttribute("aria-pressed", "false"); }); drawTop(); } }, icon("x")),
        el("b", { text: `${ids.length} selected` }), el("span", { class: "grow" }),
        el("button", { class: "btn small", type: "button", onclick: () => show(dateEditor((d) => act("/api/photos/fix", { ids, date: d }, "Date saved").then(() => PH.picked.clear()))) }, icon("calendar"), "Date"),
        el("button", { class: "btn small", type: "button", onclick: () => show(placeEditor((v) => act("/api/photos/fix", { ids, place: v }, "Place saved").then(() => PH.picked.clear()))) }, icon("pin"), "Place"),
        ids.length > 1 && el("button", { class: "btn small", type: "button", text: "Same photo", onclick: async () => { if (await act("/api/photos/merge", { ids }, "Kept as one photo")) PH.picked.clear(); } }),
        el("button", { class: "btn small danger", type: "button", onclick: () => removePhotos(ids) }, icon("trash"), "Remove")));
      return;
    }
    const f = PH.filters;
    const search = el("input", { type: "search", value: f.q, placeholder: "Search your photos", "aria-label": "Search your photos" });
    let t = 0;
    search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { f.q = search.value.trim(); loadMore(true); }, 300); });
    top.replaceChildren(el("div", { class: "gp-search" }, icon("search"), search),
      el("button", { class: "btn", type: "button", onclick: () => foldersDialog(st.s, reload) }, icon("folder"), "Folders"));
  };

  const removePhotos = async (ids) => {
    if (!confirm(`Remove ${ids.length === 1 ? "this photo" : `these ${ids.length} photos`} from ${assistant()}'s memory?\n\nThe record, the thumbnail and the words read from ${ids.length === 1 ? "it" : "them"} are deleted for good. Your original files are not touched (the originals stay where they are), and they won't be added again.`)) return;
    if (await act("/api/photos/remove", { ids }, "Removed")) PH.picked.clear();
  };

  // ---- the filters, as chips
  const drawChips = () => {
    const f = PH.filters, s = st.s;
    if (!s || !s.enabled || !s.progress.photos) { chipRow.replaceChildren(); return; }
    const apply = (k, v) => { f[k] = v; if (k === "year") f.month = ""; loadMore(true); if (k === "year") drawChips(); };
    const chip = (label, options, value, key) => {
      const sel = select(options, value, (v) => apply(key, v), label);
      sel.classList.add("gp-chip");
      sel.classList.toggle("on", !!value);
      return sel;
    };
    const fa = s.facets;
    const flags = [["", "Everything"], ["undated", `No date (${fa.undated})`], ["guess", `Date is a guess (${fa.guess})`], ["check", `Needs a look (${fa.check})`], ["copies", `Copies (${fa.copies})`], ["noplace", `No place (${fa.noplace})`]];
    chipRow.replaceChildren(...[
      chip("Year", [["", "Any year"], ...fa.years.map((y) => [String(y.year), `${y.year}`])], f.year, "year"),
      f.year ? chip("Month", [["", "Any month"], ...MONTHS.map((m, i) => [String(i + 1), m])], f.month, "month") : null,
      fa.places.length ? chip("Place", [["", "Any place"], ...fa.places.map((p) => [p.place, p.place])], f.place, "place") : null,
      fa.people && fa.people.length ? chip("Person", [["", "Anyone"], ...fa.people.map((p) => [String(p.id), p.name])], f.person, "person") : null,
      s.folders.length > 1 ? chip("Folder", [["", "Any folder"], ...s.folders.map((d) => [String(d.id), d.label])], f.folder, "folder") : null,
      chip("Show", flags, f.flag, "flag"),
      el("span", { class: "grow" }),
      chip("Order", [["newest", "Newest first"], ["oldest", "Oldest first"], ["added", "Recently added"]], f.order, "order")].filter(Boolean));
  };

  const drawStatus = () => {
    const p = st.s.progress, text = progressText(p);
    status.hidden = !text;
    if (!text) { status.replaceChildren(); return; }
    const bar = p.reading > 0 && p.found ? el("div", { class: "bar-meter" }, el("i", { style: `width:${Math.max(2, ((p.found - p.reading) / p.found) * 100).toFixed(1)}%` })) : null;
    status.replaceChildren(...[el("span", { text }), bar].filter(Boolean));
  };

  const emptyState = () => {
    const s = st.s;
    const path = el("input", { type: "text", placeholder: "or paste a folder's address", "aria-label": "Folder address" });
    const add = async (p) => { try { await api("POST", "/api/photos/folders", { path: p }); toast("Folder added"); await reload(); } catch (e) { toast(e.message, true); } };
    const choose = el("button", { class: "btn primary", type: "button", onclick: async () => {
      choose.disabled = true;
      try { const r = await api("POST", "/api/photos/folders/pick", {}); if (r.path) await add(r.path); } catch (e) { toast(e.message, true); }
      choose.disabled = false;
    } }, icon("folder"), "Choose a folder");
    return el("div", { class: "gp-empty" }, icon("photos"), el("h2", { text: "Add your photos" }),
      el("div", { class: "actions" }, choose),
      s.suggestions.length > 0 && el("div", { class: "actions wrap" }, ...s.suggestions.map((g) => el("button", { class: "chip-btn", type: "button", title: g.path, text: g.label, onclick: () => add(g.path) }))),
      el("div", { class: "actions" }, path, el("button", { class: "btn", type: "button", text: "Add", onclick: () => add(path.value) })));
  };

  const drawAll = () => {
    const s = st.s;
    if (!s.enabled) { page.replaceChildren(el("div", { class: "gp-empty" }, icon("photos"), el("h2", { text: "Photos are off" }), el("p", { class: "muted", text: "Set photos: enabled: true in config.yaml." }))); return; }
    drawTop();
    drawChips();
    drawStatus();
    st.shown = s.progress.photos;
    if (!s.folders.length) { body.replaceChildren(emptyState()); end.hidden = true; settings.hidden = false; return; }
    end.hidden = false;
    settings.hidden = false;
    loadMore(true);
  };
  const settings = el("details", { class: "gp-settings", hidden: true }, el("summary", { text: "Settings" }), fieldsCard("photos", null, null));
  page.append(settings);

  PH.timer = setInterval(async () => {
    if (location.hash.slice(1).split("/")[0] !== "photos") { clearInterval(PH.timer); observer.disconnect(); return; }
    if (!st.s || !st.s.enabled || document.hidden || document.querySelector("dialog[open]")) return;
    try {
      const s = await api("GET", "/api/photos");
      const changed = s.progress.photos !== st.shown;
      st.s.progress = s.progress;
      drawStatus();
      if (changed) { st.s = s; st.shown = s.progress.photos; drawChips(); if (!PH.picked.size) loadMore(true); }
    } catch (e) { /* the next tick tries again */ }
  }, 2500);
  reload();
  return page;
}

// ---- the folders: add, set a date for an album, look again, remove
async function foldersDialog(s0, done) {
  const dialog = document.getElementById("dialog");
  const body = el("div", { class: "stack" });
  dialog.replaceChildren(el("form", { method: "dialog", class: "photo-dialog" }, el("h2", { id: "dialog-title", text: "Photo folders" }), body,
    el("div", { class: "actions end" }, el("button", { class: "btn primary", type: "submit", text: "Done" }))));
  dialog.addEventListener("close", () => done && done(), { once: true });
  dialog.showModal();
  let s = s0;
  const redraw = async () => { try { s = await api("GET", "/api/photos"); draw(); } catch (e) { toast(e.message, true); } };
  const act = async (path, payload, msg) => { try { await api("POST", path, payload); if (msg) toast(msg); await redraw(); } catch (e) { toast(e.message, true); } };
  const draw = () => {
    const p = s.progress;
    const path = el("input", { type: "text", placeholder: "Paste a folder's address", "aria-label": "Folder address" });
    const add = async (v) => { try { await api("POST", "/api/photos/folders", { path: v }); toast("Folder added"); path.value = ""; await redraw(); } catch (e) { toast(e.message, true); } };
    const choose = el("button", { class: "btn", type: "button", onclick: async () => {
      choose.disabled = true;
      try { const r = await api("POST", "/api/photos/folders/pick", {}); if (r.path) await add(r.path); } catch (e) { toast(e.message, true); }
      choose.disabled = false;
    } }, icon("folder"), "Choose a folder");
    const rows = s.folders.map((d) => {
      const edit = el("div", { class: "folder-edit", hidden: true });
      const place = el("input", { type: "text", placeholder: "A place for the whole folder", value: d.place || "", "aria-label": "Place for this folder" });
      const scope = select([["unsure", "Photos whose date is a guess"], ["all", "Every photo in it"]], d.date ? d.date.scope : "unsure", () => {}, "Which photos");
      const openEdit = () => {
        edit.hidden = !edit.hidden;
        if (edit.hidden) return;
        edit.replaceChildren(...[d.date && el("div", { class: "muted", text: `Dated ${d.date.label}` }),
          dateEditor((dt) => act("/api/photos/folders/album", { id: d.id, date: dt, scope: scope.value === "all" ? "all" : "unsure", place: place.value }, "Saved"), "Save", scope),
          place, el("div", { class: "actions end" },
            d.date && el("button", { class: "btn small", type: "button", text: "Take the date back", onclick: () => act("/api/photos/folders/album", { id: d.id, clear: true }, "Back to what was read") }),
            el("button", { class: "btn small", type: "button", text: "Save the place", onclick: () => act("/api/photos/folders/album", { id: d.id, place: place.value }, "Saved") }))].filter(Boolean));
      };
      return el("div", { class: "item folder" }, el("div", { class: "grow" },
        el("div", { style: "font-weight:560", text: d.label }), el("div", { class: "sub", text: `${d.path} · ${d.photos.toLocaleString()} photo${d.photos === 1 ? "" : "s"}` }), edit),
        el("span", { class: "actions" },
          el("button", { class: "btn small", type: "button", text: "Dates and place", onclick: openEdit }),
          el("button", { class: "btn small", type: "button", text: "Look again", onclick: () => act("/api/photos/scan", {}, "Looking for new photos") }),
          el("button", { class: "btn small danger", type: "button", text: "Remove", onclick: async () => {
            if (!confirm(`Remove “${d.label}” from ${assistant()}'s memory?\n\nThe records, thumbnails and words read from its ${d.photos} photo${d.photos === 1 ? "" : "s"} are deleted for good. The photos on your PC are not touched (the originals stay where they are).`)) return;
            await act("/api/photos/folders/remove", { id: d.id }, "Folder removed"); } })));
    });
    const notes = [];
    if (p.files.cloud > 0) notes.push(`${p.files.cloud} online-only OneDrive file${p.files.cloud === 1 ? "" : "s"} not opened`);
    if (p.files.unreadable > 0) notes.push(`${p.files.unreadable} file${p.files.unreadable === 1 ? "" : "s"} couldn't be read`);
    if (p.copies) notes.push(`${p.copies} ${p.copies === 1 ? "copy" : "copies"} kept as one`);
    const working = progressText(p);
    body.replaceChildren(...[
      el("div", { class: "list" }, rows.length ? rows : [el("div", { class: "empty", text: "No folders yet." })]),
      s.suggestions.length > 0 && el("div", { class: "actions wrap" }, ...s.suggestions.map((g) => el("button", { class: "chip-btn", type: "button", title: g.path, text: g.label, onclick: () => add(g.path) }))),
      el("div", { class: "add-line" }, path, el("button", { class: "btn", type: "button", text: "Add", onclick: () => add(path.value) }), choose),
      (notes.length > 0 || working) && el("div", { class: "muted small", text: [working, ...notes].filter(Boolean).join(" · ") }),
      !p.places && el("div", { class: "actions" }, el("button", { class: "btn small", type: "button", text: "Add place names…", onclick: async (e) => {
        if (!confirm("Fetch the list of places?\n\nIt is about 11 MB, from download.geonames.org (free, CC BY 4.0). Only the list comes to this PC: your photos and their positions are never sent anywhere.")) return;
        const b = e.currentTarget; b.disabled = true; b.textContent = "Fetching…";
        try { const r = await api("POST", "/api/photos/places", {}); toast(`${r.places.toLocaleString()} places; ${r.photos_named} photo${r.photos_named === 1 ? "" : "s"} named`); await redraw(); }
        catch (err) { toast(err.message, true); b.disabled = false; b.textContent = "Add place names…"; } } }))].filter(Boolean));
  };
  draw();
}

// ---- one photo, large, with what is known about it beside it (arrow keys move through the photos)
async function photoDialog(id) {
  document.querySelector("dialog.viewer")?.remove();
  const v = el("dialog", { class: "viewer", "aria-label": "Photo" });
  document.body.append(v);
  const stage = el("div", { class: "stage" });
  const side = el("aside", { class: "v-info" });
  v.append(stage, side);
  let cur = id, changed = false;
  const keys = (e) => { if (e.key === "ArrowLeft") go(-1); else if (e.key === "ArrowRight") go(1); };
  v.addEventListener("close", () => { document.removeEventListener("keydown", keys); v.remove(); if (changed && PH.refresh) PH.refresh(); });
  v.addEventListener("click", (e) => { if (e.target === v) v.close(); });
  document.addEventListener("keydown", keys);
  v.showModal();
  const go = (d) => { const n = PH.ids[PH.ids.indexOf(cur) + d]; if (n) show(n); };
  const fix = async (payload, done) => {
    try { await api("POST", "/api/photos/fix", { ids: [cur], ...payload }); toast(done); changed = true; await show(cur); } catch (e) { toast(e.message, true); }
  };
  async function show(pid) {
    cur = pid;
    let p;
    try { p = await api("GET", `/api/photos/${pid}`); } catch (e) { side.replaceChildren(el("p", { class: "muted", text: e.message })); return; }
    const at = PH.ids.indexOf(pid);
    const big = el("img", { src: p.thumb, alt: p.caption || p.when });
    const loader = new Image();
    loader.onload = () => { big.src = loader.src; };
    loader.src = p.view;
    stage.replaceChildren(...[el("button", { class: "icon-btn v-close", type: "button", "aria-label": "Close", onclick: () => v.close() }, icon("x")), big,
      at > 0 && el("button", { class: "v-nav prev", type: "button", "aria-label": "Previous photo", onclick: () => go(-1) }, icon("back")),
      at >= 0 && at < PH.ids.length - 1 && el("button", { class: "v-nav next", type: "button", "aria-label": "Next photo", onclick: () => go(1) }, icon("next"))].filter(Boolean));   // a bare false would be written as the word
    const editor = el("div", {});
    const caption = el("input", { type: "text", value: p.caption || "", placeholder: "Add a description", "aria-label": "Description" });
    caption.addEventListener("change", () => fix({ caption: caption.value }, "Saved"));
    const who = p.people && p.people.faces.length ? el("div", { class: "v-people" },
      ...p.people.faces.map((f) => el("div", { class: "who-face" }, el("img", { src: f.crop, alt: "" }), el("span", { text: f.name || "No name yet" }))),
      p.people.unnamed > 0 && el("a", { class: "muted small", href: "#people", onclick: () => v.close(), text: "Add names" })) : null;
    const similar = el("div", {});
    api("GET", `/api/photos/${pid}/similar`).then((r) => {
      if (!r.items.length) return;
      similar.replaceChildren(el("h4", { text: "Might be the same photo" }), el("div", { class: "photo-grid small" }, ...r.items.map((o) => el("div", { class: "photo" },
        el("img", { src: o.thumb, alt: o.when, loading: "lazy" }),
        el("button", { class: "btn small", type: "button", text: "Same photo", onclick: async () => { try { await api("POST", "/api/photos/merge", { ids: [pid, o.id] }); toast("Kept as one photo"); changed = true; v.close(); } catch (e) { toast(e.message, true); } } })))));
    }).catch(() => {});
    side.replaceChildren(...[
      el("div", { class: "v-head" }, el("h2", { text: p.when }),
        (p.guess || p.flag) && el("span", { class: "pill warn", text: p.flag ? "Check the date" : "Estimated" })),
      el("div", { class: "v-line" }, icon("pin"), el("span", { text: p.place_label || "No place" })),
      who, caption,
      el("div", { class: "actions" },
        el("button", { class: "btn small", type: "button", onclick: () => editor.replaceChildren(dateEditor((d) => fix({ date: d }, "Date saved"))) }, icon("calendar"), "Date"),
        el("button", { class: "btn small", type: "button", onclick: () => editor.replaceChildren(placeEditor((x) => fix({ place: x }, "Place saved"))) }, icon("pin"), "Place"),
        p.source === "person" && el("button", { class: "btn small", type: "button", text: "Undo date", onclick: () => fix({ clear: "date" }, "Back to what was read") }),
        p.place_source === "person" && el("button", { class: "btn small", type: "button", text: "Undo place", onclick: () => fix({ clear: "place" }, "Back to what was read") }),
        el("button", { class: "btn small danger", type: "button", onclick: async () => {
          if (!confirm(`Remove this photo from ${assistant()}'s memory? The record, thumbnail and words are deleted for good; the original file is not touched.`)) return;
          try { await api("POST", "/api/photos/remove", { ids: [pid] }); toast("Removed"); changed = true; v.close(); } catch (e) { toast(e.message, true); } } }, icon("trash"), "Remove")),
      editor,
      el("details", { class: "more" }, el("summary", {}, "Details", icon("chevron")),
        el("div", { class: "stack" },
          el("div", { class: "muted small", text: `${DATE_FROM[p.source] || ""}${p.place_label ? ` · place ${p.place_source === "person" ? "set by you" : "from the GPS position"}` : ""}` }),
          p.text && el("div", { class: "words", text: p.text }),
          ...p.file_list.map((f) => el("div", { class: "item" }, el("div", { class: "grow" }, el("div", { class: "sub", text: f.path }), el("div", { class: "sub", text: `${f.width}×${f.height} · ${(f.size / 1048576).toFixed(1)} MB${f.exists ? "" : " · not found where it was"}` })),
            f.exists && el("button", { class: "btn small", type: "button", text: "Show in folder", onclick: async () => { try { await api("POST", `/api/photos/${pid}/show`, {}); } catch (e) { toast(e.message, true); } } }))))),
      similar].filter(Boolean));
  }
  await show(id);
}


// ------------------------------------------------------------------ Memory: People

const PE = { timer: 0, singles: false };

// A round picture of one face. Clicking opens its photo, or (when `onClick` is given) picks it.
function faceButton(f, opts = {}) {
  return el("button", { type: "button", class: "face" + (opts.size ? ` ${opts.size}` : "") + (opts.picked && opts.picked.has(f.id) ? " picked" : ""),
    title: opts.title || "See the photo", "aria-label": opts.title || "A face. See the photo", "aria-pressed": opts.picked ? String(opts.picked.has(f.id)) : null,
    "data-id": String(f.id), onclick: (e) => (opts.onClick ? opts.onClick(f, e.currentTarget) : photoDialog(f.photo)) }, el("img", { src: f.crop, alt: "", loading: "lazy" }));
}

const photoCount = (n) => `${n.toLocaleString()} photo${n === 1 ? "" : "s"}`;

function pagePeople() {
  clearInterval(PE.timer);
  const body = el("div", { class: "pe" });
  const st = { s: null, g: null };
  const reload = async () => {
    try {
      st.s = await api("GET", "/api/people");
      st.g = st.s.enabled && (st.s.on || st.s.status.faces) ? await api("GET", `/api/people/groups${PE.singles ? "?singles=1" : ""}`) : null;
      draw();
    } catch (e) { body.replaceChildren(el("p", { class: "muted loading", text: e.message })); }
  };
  PE.refresh = reload;
  const act = async (path, payload, done) => {
    try { const r = await api("POST", path, payload); if (done) toast(done); await reload(); return r; } catch (e) { toast(e.message, true); return null; }
  };
  const named = () => st.s.people;

  const summary = () => {
    const s = st.s, p = s.status;
    if (!s.on) return "";
    if (!s.models.present) return "";
    if (p.left > 0 && s.photos) return `Looking for faces · ${(s.photos - p.left).toLocaleString()} of ${s.photos.toLocaleString()} photos`;
    if (p.working) return "Looking for the people you named…";
    const parts = [];
    if (p.named) parts.push(`${p.named} ${p.named === 1 ? "person" : "people"}`);
    if (p.groups) parts.push(`${p.groups.toLocaleString()} to name`);
    if (p.usable) parts.push(`${p.usable.toLocaleString()} faces`);
    return parts.join(" · ");
  };

  // ---- the switch, the models
  const topCard = () => {
    const s = st.s, m = s.models;
    const onSwitch = async (on) => {
      try { await api("POST", "/api/people/switch", { on }); } catch (e) { toast(e.message, true); }
      await reload();
    };
    const fetchModels = async (e) => {
      if (!confirm(`Get the two face models?\n\nAbout ${m.mb} MB: ${m.files.map((f) => `${f.name.split("_")[1]} (${f.mb} MB)`).join(" and ")}, from huggingface.co/opencv (OpenCV's model collection). YuNet is MIT-licensed, SFace Apache-2.0. Only the models come to this PC: your photos and faces are never sent anywhere.`)) return;
      const b = e.currentTarget; b.disabled = true; b.textContent = "Fetching…";
      try { await api("POST", "/api/people/models", {}); toast("Face models ready"); } catch (err) { toast(err.message, true); }
      await reload();
    };
    const wipe = async () => {
      if (!confirm("Delete all face data?\n\nEvery face fingerprint, face picture, group and name is deleted for good, and recognising faces is switched off. Your photos are not touched.")) return;
      try { await api("POST", "/api/people/delete-all", {}); toast("All face data deleted"); } catch (e) { toast(e.message, true); }
      await reload();
    };
    const line = summary();
    const bar = s.on && m.present && s.status.left > 0 && s.photos > 0 ? el("div", { class: "bar-meter" }, el("i", { style: `width:${Math.max(2, ((s.photos - s.status.left) / s.photos) * 100).toFixed(1)}%` })) : null;
    return el("section", { class: "card pe-top" },
      el("div", { class: "pe-switch" }, el("div", { class: "grow" }, el("h2", {}, "Recognise people", info("Finds the faces in your photos so you can name them once. Faces are read on this PC, kept only here, deleted with the person, and never sent anywhere. A name is only ever one you type.")),
        line && el("div", { class: "muted", text: line })),
        !s.allowed ? el("span", { class: "muted", text: "Off in config.yaml" }) : !s.on ? el("button", { class: "btn small", type: "button", text: "Turn it on again", onclick: () => onSwitch(true) }) : null),
      s.on && !m.present && el("div", { class: "notice" }, el("span", { text: `Needs two face models · ${m.mb} MB` }),
        el("button", { class: "btn primary small", type: "button", text: "Get them…", onclick: fetchModels })),
      s.on && s.photos === 0 && el("div", { class: "muted", text: "Add a folder on the Photos page first." }),
      bar,
      s.on && !S.state.running && s.status.left > 0 && el("div", { class: "muted small", text: `${assistant()} isn't running: this carries on while the window is open.` }),
      (s.status.faces > 0) && el("details", { class: "pe-more" }, el("summary", { text: "More" }),
        el("div", { class: "actions" },
          el("button", { class: "btn small", type: "button", text: "Look at every photo again", onclick: () => act("/api/people/look-again", {}, "Looking again") }),
          el("button", { class: "btn small danger", type: "button", text: "Delete all face data…", onclick: wipe }))));
  };

  // ---- "Is this …?"
  const questionCard = (q) => {
    const picked = new Set(q.faces.map((f) => f.id));
    const strip = el("div", { class: "faces" });
    const yes = el("button", { class: "btn primary small", type: "button" });
    const no = el("button", { class: "btn small", type: "button" });
    const label = () => {
      yes.textContent = "Yes";
      no.textContent = "No";
      yes.disabled = no.disabled = !picked.size;
      for (const b of strip.children) b.setAttribute("aria-pressed", String(picked.has(Number(b.dataset.id))));
    };
    for (const f of q.faces) strip.append(faceButton(f, { size: "large", picked, title: "Click to include or leave out. Double-click for the photo", onClick: (face, b) => {
      picked.has(f.id) ? picked.delete(f.id) : picked.add(f.id);
      b.classList.toggle("picked", picked.has(f.id));
      label();
    } }));
    strip.addEventListener("dblclick", (e) => { const b = e.target.closest(".face"); if (b) { const f = q.faces.find((x) => x.id === Number(b.dataset.id)); if (f) photoDialog(f.photo); } });
    yes.addEventListener("click", () => act("/api/people/move", { faces: [...picked], person: q.person.id }, `Added to ${q.person.name}`));
    no.addEventListener("click", () => act("/api/people/deny", { faces: [...picked], person: q.person.id }, `Noted: not ${q.person.name}`));
    label();
    return el("div", { class: "pe-q" }, el("h3", { text: `Is this ${q.person.name}?` }), strip, el("div", { class: "actions" }, yes, no));
  };

  // ---- "The same person?"
  const sameCard = (pair) => {
    const side = (c) => el("div", { class: "half" }, faceStrip(c.samples.slice(0, 3), { size: "small" }), el("div", { class: "who", text: c.name || "No name yet" }));
    const joinThem = async () => {
      const [a, b] = pair.a.state === "named" ? [pair.a, pair.b] : pair.b.state === "named" ? [pair.b, pair.a] : [pair.a, pair.b];
      if (a.state === "named" && b.state === "named" && !confirm(`Join ${a.name} and ${b.name} into one person?\n\nThey'll be called ${a.name} from now on.`)) return;
      await act("/api/people/merge", { keep: a.id, other: b.id }, "Joined");
    };
    return el("div", { class: "pe-q" }, el("div", { class: "pair" }, side(pair.a), el("span", { class: "muted", text: "same person?" }), side(pair.b)),
      el("div", { class: "actions" }, el("button", { class: "btn primary small", type: "button", text: "Yes", onclick: joinThem }),
        el("button", { class: "btn small", type: "button", text: "No", onclick: () => act("/api/people/not-same", { a: pair.a.id, b: pair.b.id }, "Noted") })));
  };

  const faceStrip = (faces, opts = {}) => el("div", { class: "faces" }, ...faces.map((f) => faceButton(f, opts)));

  // ---- a person or a group of faces, round, with a name under it; clicking opens it
  const tile = (c) => el("button", { type: "button", class: "pe-tile", onclick: () => personDialog(c) },
    c.samples.length ? el("img", { src: c.samples[0].crop, alt: "" }) : avatar(c.name || "?", "large"),
    el("span", { class: "n", text: c.name || "Add a name" }), c.state === "named" && el("span", { class: "c", text: photoCount(c.photos) }));

  // ---- the person's own window: a name, their faces (to take some out), and what can be done with them
  async function personDialog(c) {
    const dialog = document.getElementById("dialog");
    const picked = new Set();
    const strip = el("div", { class: "faces" });
    const bar = el("div", { class: "actions" });
    const more = el("div", { class: "add-line", hidden: true });
    const name = el("input", { type: "text", value: c.name || "", placeholder: "Add a name", "aria-label": "Name", maxlength: "80" });
    const relation = el("input", { type: "text", value: c.relation || "", placeholder: "How you know them (optional)", "aria-label": "How you know them", maxlength: "40" });
    const saveName = async () => { if (await act("/api/people/name", { id: c.id, name: name.value, relation: relation.value }, "Saved")) dialog.close(); };
    name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveName(); } });
    const others = named().filter((o) => o.id !== c.id);
    const joinSel = others.length ? select([[0, c.state === "named" ? "Join with…" : "The same person as…"], ...others.map((o) => [o.id, o.name])], 0, async (id) => {
      if (!id) return;
      if (c.state === "named" && !confirm(`Join ${c.name} and ${others.find((o) => o.id === id).name} into one person?\n\nThey'll be called ${c.name} from now on.`)) { dialog.close(); return; }
      if (await act("/api/people/merge", { keep: c.state === "named" ? c.id : id, other: c.state === "named" ? id : c.id }, "Joined")) dialog.close();
    }, "Join with another person") : null;
    const foot = [
      c.state === "named" && el("button", { class: "btn small", type: "button", text: "Their photos", onclick: () => { dialog.close(); PH.filters.person = String(c.id); location.hash = "#photos"; } }),
      joinSel,
      c.state === "named" ? el("button", { class: "btn small", type: "button", text: "Remove the name", onclick: async () => { if (await act("/api/people/unname", { id: c.id }, "The faces stay together, without a name")) dialog.close(); } })
        : c.state === "unnamed" && el("button", { class: "btn small", type: "button", text: "Not someone I know", onclick: async () => { if (await act("/api/people/ignore", { id: c.id }, "Set aside")) dialog.close(); } }),
      c.state === "ignored" && el("button", { class: "btn small", type: "button", text: "Bring back", onclick: async () => { if (await act("/api/people/unignore", { id: c.id }, "Back in the groups to name")) dialog.close(); } }),
      el("button", { class: "btn small danger", type: "button", text: "Delete…", onclick: async () => {
        if (!confirm(`Delete ${c.name || "these faces"}?\n\n${c.name ? "Their name, " : ""}${c.faces} face fingerprint${c.faces === 1 ? "" : "s"} and the face pictures are deleted for good, here and in the copies ${assistant()} keeps before an update. The photos stay, and aren't looked at for faces again unless you ask.`)) return;
        if (await act("/api/people/remove", { id: c.id }, "Deleted")) dialog.close(); } })].filter(Boolean);
    dialog.replaceChildren(el("form", { method: "dialog", class: "photo-dialog" },
      c.state !== "ignored" && el("div", { class: "actions pe-name" }, name, c.state === "named" || c.name ? relation : null, el("button", { class: "btn primary small", type: "button", text: "Save", onclick: saveName })),
      c.state === "ignored" && el("h2", { text: "Set aside" }),
      strip, more, bar, el("div", { class: "actions" }, ...foot),
      el("div", { class: "actions end" }, el("button", { class: "btn primary", type: "submit", text: "Done" }))));
    dialog.showModal();
    dialog.addEventListener("close", () => reload(), { once: true });
    let offset = 0;
    const drawBar = () => {
      bar.replaceChildren(...[
        picked.size ? el("button", { class: "btn small", type: "button", text: `These ${picked.size} are someone else`,
          onclick: async () => { try { await api("POST", "/api/people/split", { id: c.id, faces: [...picked] }); toast("Taken out"); picked.clear(); offset = 0; strip.replaceChildren(); await load(); } catch (e) { toast(e.message, true); } } }) : null,
        picked.size && named().filter((o) => o.id !== c.id).length ? select([[0, "Move them to…"], ...named().filter((o) => o.id !== c.id).map((o) => [o.id, o.name])], 0, async (id) => {
          if (!id) return;
          try { await api("POST", "/api/people/move", { faces: [...picked], person: id }); toast("Moved"); picked.clear(); offset = 0; strip.replaceChildren(); await load(); } catch (e) { toast(e.message, true); } }, "Move them to") : null].filter(Boolean));
    };
    const load = async () => {
      try {
        const r = await api("GET", `/api/people/${c.id}/faces?offset=${offset}`);
        strip.append(...r.items.map((f) => faceButton(f, { size: "large", picked, title: "Click to pick. Double-click for the photo", onClick: (face, b) => {
          picked.has(f.id) ? picked.delete(f.id) : picked.add(f.id); b.classList.toggle("picked", picked.has(f.id)); drawBar(); } })));
        offset += r.items.length;
        more.hidden = !r.more;
        drawBar();
      } catch (e) { strip.replaceChildren(el("p", { class: "muted", text: e.message })); more.hidden = true; }
    };
    strip.addEventListener("dblclick", async (e) => {
      const b = e.target.closest(".face");
      if (!b) return;
      dialog.close();
      try { const r = await api("GET", `/api/people/${c.id}/faces?offset=0`); const f = r.items.find((x) => x.id === Number(b.dataset.id)); if (f) photoDialog(f.photo); } catch (err) { toast(err.message, true); }
    });
    more.replaceChildren(el("button", { class: "btn", type: "button", text: "Show more", onclick: load }));
    await load();
  }

  const draw = () => {
    const s = st.s;
    if (!s.enabled) { body.replaceChildren(el("div", { class: "gp-empty" }, icon("people"), el("h2", { text: "Photos are off" }))); return; }
    const parts = [topCard()];
    const show = s.on || s.status.faces > 0;
    if (show && s.questions.length) parts.push(el("section", { class: "card" }, el("h2", { text: "Is this…?" }), ...s.questions.map(questionCard)));
    if (show && s.same.length) parts.push(el("section", { class: "card" }, el("h2", { text: "The same person?" }), ...s.same.map(sameCard)));
    if (show && s.people.length) parts.push(el("section", { class: "card" }, el("h2", { text: "People" }), el("div", { class: "pe-grid" }, ...s.people.map(tile))));
    if (show && st.g && (st.g.items.length || st.g.singles)) {
      const g = st.g;
      parts.push(el("section", { class: "card" }, el("h2", {}, "Who are these?", info("Groups of faces that look like one person. Name a group once and every photo of them is found.")),
        el("div", { class: "pe-grid" }, ...g.items.map(tile)),
        (g.more || (!PE.singles && g.singles > 0) || PE.singles) && el("div", { class: "add-line" },
          g.more && el("button", { class: "btn", type: "button", text: "Show more", onclick: async () => {
            const r = await api("GET", `/api/people/groups?offset=${g.items.length}${PE.singles ? "&singles=1" : ""}`);
            g.items.push(...r.items); g.more = r.more; draw(); } }),
          !PE.singles && g.singles > 0 && el("button", { class: "btn", type: "button", text: `Faces seen once (${g.singles.toLocaleString()})`, onclick: () => { PE.singles = true; reload(); } }),
          PE.singles && el("button", { class: "btn", type: "button", text: "Hide faces seen once", onclick: () => { PE.singles = false; reload(); } }))));
    }
    if (show && s.ignored.length) parts.push(el("details", { class: "card" }, el("summary", { text: `Set aside (${s.status.ignored})` }), el("div", { class: "pe-grid" }, ...s.ignored.map(tile))));
    body.replaceChildren(...parts.filter(Boolean));
  };

  PE.timer = setInterval(async () => {
    if (location.hash.slice(1).split("/")[0] !== "people") { clearInterval(PE.timer); return; }
    if (!st.s || !st.s.enabled || document.hidden || document.querySelector("dialog[open]")) return;
    if (!st.s.on && !st.s.status.faces) return;
    if (document.activeElement && document.activeElement.tagName === "INPUT" && body.contains(document.activeElement)) return;
    try {
      const s = await api("GET", "/api/people");
      const changed = s.status.faces !== st.s.status.faces || s.status.groups !== st.s.status.groups || s.status.questions !== st.s.status.questions || s.status.left !== st.s.status.left || s.status.working !== st.s.status.working;
      if (changed) { st.s = s; st.g = await api("GET", `/api/people/groups${PE.singles ? "?singles=1" : ""}`); draw(); }
    } catch (e) { /* the next tick tries again */ }
  }, 3000);
  reload();
  return el("div", {}, head("People"), body);
}


// ------------------------------------------------------------------ Memory: What MARY remembers

const KIND_LABEL = { fact: "Learned", diary: "Diary", reflection: "Day summary", core: "About you" };

function memoryLine(it, changed, opts = {}) {
  const text = el("div", { class: "grow" });
  const box = el("div", { class: "item mem" }, text);
  const meta = [KIND_LABEL[it.kind] || it.kind, it.when];
  if (it.ended) meta.push(`ended ${it.ended}${it.ended_why ? `, ${it.ended_why}` : ""}`);
  const show = () => {
    text.replaceChildren(el("div", { class: "mem-text", text: it.text }),
      el("div", { class: "sub", text: meta.join(" · ") }));
  };
  const actions = el("span", { class: "actions" });
  const draw = () => {
    show();
    actions.replaceChildren();
    if (!it.can_edit) {
      actions.append(el("span", { class: "muted", text: "Change it on About you" }));
      return;
    }
    if (!it.ended) {
      actions.append(
        el("button", { class: "icon-btn", type: "button", "aria-label": "Edit", title: "Edit", onclick: edit }, icon("edit")));
      if (it.kind === "fact") {
        actions.append(el("button", { class: "btn small", type: "button", text: "Forget", title: "Ends it but keeps it, with the date", onclick: forget }));
      }
    }
    actions.append(el("button", { class: "icon-btn danger", type: "button", "aria-label": "Delete for good", title: "Delete for good", onclick: erase }, icon("trash")));
  };
  const edit = () => {
    const input = el("input", { type: "text", value: it.text, "aria-label": "The memory" });
    text.replaceChildren(input);
    actions.replaceChildren(
      el("button", { class: "btn small", type: "button", text: "Cancel", onclick: draw }),
      el("button", { class: "btn small primary", type: "button", text: "Save", onclick: async () => {
        try { it = await api("POST", "/api/memory/edit", { id: it.id, text: input.value }); draw(); toast("Saved"); }
        catch (e) { toast(e.message, true); }
      } }));
    input.focus();
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") actions.querySelector(".primary").click(); if (e.key === "Escape") draw(); });
  };
  const forget = async () => {
    try { it = await api("POST", "/api/memory/end", { id: it.id }); toast(`${assistant()} has forgotten that`); changed(); }
    catch (e) { toast(e.message, true); }
  };
  const erase = async () => {
    if (!confirm(`Delete this for good?\n\n“${it.text}”\n\nIt is removed from ${assistant()}'s memory and its files. This can't be undone.`)) return;
    try { await api("POST", "/api/memory/delete", { id: it.id }); toast("Deleted for good"); changed(); }
    catch (e) { toast(e.message, true); }
  };
  draw();
  box.append(actions);
  return box;
}

function pageRemembered() {
  const body = el("div", {});
  const M = S.mem || (S.mem = { which: "current", day: "" });
  const reload = () => api("GET", "/api/memory").then(draw).catch((e) => body.replaceChildren(el("p", { class: "muted loading", text: e.message })));

  const draw = (info) => {
    if (!info.enabled) {
      body.replaceChildren(card(null, null, el("div", { class: "empty", text: "Memory is switched off. Set memory: enabled: true in your config to turn it on." })));
      return;
    }
    const c = info.counts;
    const results = el("div", { class: "list" });
    const search = el("input", { type: "text", placeholder: `Ask what ${assistant()} remembers: a name, a place, a day…`, "aria-label": "Search the memory" });
    let timer = 0;
    const run = async () => {
      const q = search.value.trim();
      if (q.length < 2) { results.replaceChildren(); return; }
      try {
        const r = await api("GET", `/api/memory/search?q=${encodeURIComponent(q)}`);
        results.replaceChildren(...(r.items.length ? r.items.map((it) => memoryLine(it, () => { run(); reload(); }))
          : [el("div", { class: "empty", text: `Nothing about “${q}”.` })]));
      } catch (e) { results.replaceChildren(el("div", { class: "empty", text: e.message })); }
    };
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 300); });

    const facts = el("div", { class: "list" });
    const drawFacts = async (offset = 0) => {
      try {
        const r = await api("GET", `/api/memory/list?kind=fact&which=${M.which}&offset=${offset}`);
        const rows = r.items.map((it) => memoryLine(it, reload));
        if (!rows.length && !offset) rows.push(el("div", { class: "empty", text: M.which === "current"
          ? "Nothing learned yet." : "Nothing has ended." }));
        if (r.more) {
          const more = el("div", { class: "add-line" }, el("button", { class: "btn", type: "button", text: "Show more", onclick: () => { more.remove(); drawFacts(offset + 50); } }));
          rows.push(more);
        }
        if (offset) facts.append(...rows); else facts.replaceChildren(...rows);
      } catch (e) { facts.replaceChildren(el("div", { class: "empty", text: e.message })); }
    };
    drawFacts();
    const add = el("input", { type: "text", placeholder: "Add a fact: My sister's name is Ama", "aria-label": "Add a fact" });
    const addFact = async () => {
      if (!add.value.trim()) return;
      try { const r = await api("POST", "/api/memory/add", { text: add.value }); add.value = ""; toast(r.status === "already" ? "Already known" : "Added"); reload(); }
      catch (e) { toast(e.message, true); }
    };
    add.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addFact(); } });

    const days = info.days;
    const day = days.some((d) => d.day === M.day) ? M.day : days[0]?.day || "";
    const diary = el("div", { class: "list" });
    const drawDay = async (d) => {
      if (!d) { diary.replaceChildren(el("div", { class: "empty", text: "Nothing in the diary yet." })); return; }
      try {
        const [lines, sum] = await Promise.all([api("GET", `/api/memory/list?kind=diary&day=${d}`), api("GET", `/api/memory/list?kind=reflection&day=${d}`)]);
        diary.replaceChildren(...sum.items.map((it) => el("div", { class: "item mem summary" }, el("div", { class: "grow" },
          el("div", { class: "mem-text", text: it.text }), el("div", { class: "sub", text: "Summary of the day" })))),
          ...lines.items.map((it) => memoryLine(it, reload)),
          ...(lines.more ? [el("div", { class: "empty", text: "Only the first 50 lines of this day are shown." })] : []));
      } catch (e) { diary.replaceChildren(el("div", { class: "empty", text: e.message })); }
    };
    drawDay(day);
    const dayPick = days.length ? select(days.map((d) => [d.day, `${new Date(d.day + "T12:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" })} (${d.count})`]),
      day, (v) => { M.day = v; drawDay(v); }, "Day") : null;

    body.replaceChildren(...[
      card("Search", `Look through everything ${assistant()} remembers, as ${assistant()} would.`,
        el("div", { class: "add-line", style: "border-top:0" }, search), results),
      card("Learned about you", `Facts ${assistant()} has picked up. Forgetting one ends it and keeps it with its date; deleting removes it for good.`,
        el("div", { class: "segbar" }, segmented([["current", `Current (${c.fact.current})`], ["ended", `Ended (${c.fact.ended})`]], M.which,
          (v) => { M.which = v; drawFacts(); }, "Which facts")),
        facts, M.which === "current" && el("div", { class: "add-line" }, add, el("button", { class: "btn", type: "button", text: "Add", onclick: addFact }))),
      card("Diary", `One line for each thing you asked, kept on this PC. What a skill read out (messages, the screen) is never written.`,
        dayPick && el("div", { class: "segbar" }, dayPick), diary),
      fieldsCard("memory", "Learning and keeping", null),
      info.cloud && (settingNow("memory.consolidate.enabled") || info.learn)
        ? el("div", { class: "banner info" }, el("span", { class: "grow", text: `${assistant()}'s model runs in the cloud, so with background learning on, what you said (never ${assistant()}'s replies, and nothing from messages or the screen) is sent to it while you're away.` }))
        : null,
      el("p", { class: "muted small", title: `Also plain text in ${info.folder}: facts.md and the diary folder open in Notepad, and edits are picked up within a few minutes.`, text: `${c.diary.current} diary lines · ${c.fact.current} learned facts · ${c.reflection.current} day summaries` })].filter(Boolean));   // (a bare null would be written as the word "null")
  };
  reload();
  return el("div", {}, head(`What ${assistant()} remembers`, `${assistant()} keeps a diary of what you ask and learns facts about you. It all stays on this PC.`), body);
}

// ------------------------------------------------------------------ General: Listening, Model, Skills, Connections

// ------------------------------------------------------------------ Model: on this PC, Ollama's cloud, or a service

const SERVICES = [["OpenAI", "https://api.openai.com/v1"], ["OpenRouter", "https://openrouter.ai/api/v1"],
  ["Groq", "https://api.groq.com/openai/v1"], ["LM Studio on this PC", "http://127.0.0.1:1234/v1"]];
const ACRONYMS = new Set(["gpt", "oss", "glm", "llm"]);
const isCloudName = (id) => /[:-]cloud$/.test(id || "");
const isLocalUrl = (url) => /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/i.test(url || "");
const hostOf = (url) => { try { return new URL(url).host; } catch { return url; } };

// "qwen3.5:2b-q4_K_M" -> Qwen 3.5, 2B
function modelLabel(id) {
  const [base, tag = ""] = id.split(":");
  const words = base.split(/[-_/]/).filter(Boolean).map((w) => {
    const m = w.match(/^([a-z]+)(\d.*)?$/i);
    if (!m) return w;
    const name = ACRONYMS.has(m[1].toLowerCase()) ? m[1].toUpperCase() : m[1][0].toUpperCase() + m[1].slice(1);
    return m[2] ? `${name} ${m[2]}` : name;
  });
  const size = (tag.match(/(\d+(?:\.\d+)?)b\b/i) || [])[1];
  return { title: words.join(" "), size: size ? `${size}B` : "" };
}

function modelHint(m) {
  const b = parseFloat(m.params) || parseFloat((m.id.match(/(\d+(?:\.\d+)?)b/i) || [])[1]) || 0;
  if (!b) return "";
  return b < 3 ? "Fastest replies" : b < 6 ? "Follows instructions better, about 3× slower" : "Slow on most PCs";
}

function askCloud(where) {
  return confirm(`${where} runs off this PC: what you say, and what ${assistant()} reads on your screen to answer, is sent there. Use it?`);
}

function modelRow(m, chosen, recommended, onPick) {
  const label = modelLabel(m.id);
  const size = label.size || m.params;
  const facts = m.cloud ? [m.params && `${m.params} parameters`, "runs on ollama.com", m.levels.length > 0 && "thinking levels"]
    : [size && `${size} parameters`, m.size_gb && `${m.size_gb} GB`, modelHint(m)];
  const pick = () => { if (!chosen) onPick(); };
  return el("div", { class: "model-row", role: "radio", tabindex: "0", "aria-checked": String(chosen), onclick: pick,
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } } },
    el("span", { class: "dot-radio" }),
    el("div", { class: "grow" },
      el("div", { class: "top" }, el("b", { text: label.title }), size && el("span", { class: "muted", text: size }),
        recommended && el("span", { class: "pill accent", text: "Recommended" })),
      el("div", { class: "sub", text: facts.filter(Boolean).join(" · ") }),
      el("code", { text: m.id })));
}

// Adding a model to Ollama, with progress: a download for one on this PC, a small entry for a cloud one.
function pullLine() {
  S.pullEl = el("div", { class: "pull" });
  drawPull();
  return S.pullEl;
}

function drawPull() {
  const p = S.pull, box = S.pullEl;
  if (!box) return;
  if (!p) return box.replaceChildren();
  const pct = p.total ? Math.round((p.completed / p.total) * 100) : 0;
  const text = p.error || (p.running ? (p.total ? `Adding ${p.name}: ${pct}% of ${(p.total / 1e9).toFixed(1)} GB` : `Adding ${p.name}: ${p.status || "starting"}…`) : `Added ${p.name}`);
  box.replaceChildren(el("div", { class: p.error ? "help warn-text" : "help", text }),
    ...(p.running && p.total > 0 ? [el("div", { class: "bar-meter" }, el("i", { style: `width:${pct}%` }))] : []));
}

async function addModel(input, cloudTab) {
  const name = input.value.trim();
  if (!name) return input.focus();
  const cloud = isCloudName(name);
  if (cloudTab && !cloud) return toast("Cloud models end in :cloud or -cloud, like gpt-oss:120b-cloud.", true);
  if (!cloud && !confirm(`Download ${name} from ollama.com? A model on this PC is usually 1 to 5 GB.`)) return;
  try {
    S.pull = await api("POST", "/api/models/pull", { name });
    drawPull();
    while (S.pull.running) {
      await sleep(800);
      S.pull = await api("GET", `/api/models/pull?name=${encodeURIComponent(name)}`);
      drawPull();
    }
    if (S.pull.error) return toast(S.pull.error, true);
    S.choices.ollama = null;
    S.pull = null;
    toast(`Added ${name}`);
    render();
  } catch (e) { toast(e.message, true); }
}

async function testModel(out) {
  const provider = settingNow("brain.provider");
  out.replaceChildren(el("span", { class: "muted", text: "Asking it to say hello…" }));
  try {
    const r = await api("POST", "/api/models/test", { provider, url: settingNow("brain.api.url"),
      model: provider === "api" ? settingNow("brain.api.model") : settingNow("llm.model") });
    out.replaceChildren(r.ok ? el("span", { class: "good-text", text: `It answered in ${r.seconds} s: “${r.reply}”` })
      : el("span", { class: "warn-text", text: r.why }));
  } catch (e) { out.replaceChildren(el("span", { class: "warn-text", text: e.message })); }
}

function pageModel() {
  const F = (path) => S.state.settings.pages.model.find((x) => x.path === path);
  const provider = settingNow("brain.provider");
  const current = settingNow("llm.model");
  const url = settingNow("brain.api.url");
  const recommended = S.state.settings.values["llm.model"].base;
  const source = S.modelSource || (provider === "api" ? "api" : isCloudName(current) ? "cloud" : "local");
  const chosenNow = ["brain.provider", "llm.model", "brain.api.url", "brain.api.model"].some((p) => p in S.draft.settings);

  // what's chosen, at the top
  const facts = el("div", { class: "sub" });
  const tested = el("div", { class: "test-out" });
  const title = provider === "api" ? (settingNow("brain.api.model") || "No model chosen") : modelLabel(current).title;
  const where = provider === "api" ? (url ? `${hostOf(url)}${isLocalUrl(url) ? " (this PC)" : ""}` : "Another service")
    : isCloudName(current) ? "Ollama Cloud" : "On this PC";
  const summary = el("section", { class: "card pad now" },
    el("div", { class: "now-top" }, el("div", { class: "grow" }, el("div", { class: "eyebrow", text: chosenNow ? "Chosen, not saved yet" : "Now using" }),
      el("div", { class: "now-title" }, el("b", { text: title }), el("span", { class: "pill", text: where })), facts),
      el("button", { class: "btn", type: "button", text: "Test", onclick: () => testModel(tested) })),
    tested);

  // the lists of Ollama models (on this PC / cloud)
  const list = el("div", { class: "list model-list", role: "radiogroup", "aria-label": "Models" }, el("p", { class: "muted", text: "Looking for models…" }));
  const note = el("div");
  choices("ollama").then((c) => {
    if (!c.running) note.replaceChildren(el("div", { class: "banner" }, el("span", { class: "grow", text: c.note })));
    const item = c.items.find((m) => m.id === current);
    if (provider !== "api") {
      const level = item && item.levels.length ? ` · thinks: ${settingNow("llm.think_level")} when it thinks` : "";
      facts.textContent = item ? (item.cloud ? `${item.params} parameters · runs on ollama.com${level}` : [item.params && `${item.params} parameters`, `${item.size_gb} GB`, modelHint(item)].filter(Boolean).join(" · ")) : current;
    } else facts.textContent = isLocalUrl(url) ? "A server on this PC" : "What you say goes to this service";
    const mine = c.items.filter((m) => m.cloud === (source === "cloud"));
    list.replaceChildren(...(mine.length ? mine.map((m) => modelRow(m, provider === "ollama" && m.id === current, !m.cloud && m.id === recommended, () => {
      if (m.cloud && !(provider === "ollama" && isCloudName(current)) && !askCloud("Ollama's cloud (ollama.com)")) return;
      setSetting("llm.model", m.id);
      setSetting("brain.provider", "ollama");
      render();
    })) : [el("div", { class: "empty", text: source === "cloud" ? "No cloud models added yet." : "No models on this PC yet." })]));
    if (thinkRow) thinkRow.hidden = provider === "ollama" && !(item && item.levels.length);
  });

  let body;
  if (source === "api") {
    const keySet = S.state.keys.llm;
    const urlInput = textInput(url, (v) => setSetting("brain.api.url", v.trim()), "Service address", "https://…/v1");
    const picks = el("div", { class: "presets", style: "margin:8px 0 0" }, SERVICES.map(([name, u]) => el("button", { type: "button", text: name,
      onclick: () => { setSetting("brain.api.url", u); render(); } })));
    const key = el("input", { type: "password", autocomplete: "off", placeholder: keySet ? "Saved. Paste a new key to replace it" : "Paste the service's API key", "aria-label": "API key" });
    const saveKey = async () => {
      try {
        S.state.keys = await api("POST", "/api/keys", { name: "LLM_API_KEY", value: key.value });
        key.value = "";
        S.apiModels = {};
        toast("Key saved");
        render();
      } catch (e) { toast(e.message, true); }
    };
    key.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveKey(); } });
    const options = el("datalist", { id: "api-models" });
    const modelNote = el("div", { class: "help" });
    S.apiModels = S.apiModels || {};
    if (url) {
      S.apiModels[url] = S.apiModels[url] || api("POST", "/api/choices/api-models", { url }).catch((e) => ({ items: [], note: e.message }));
      S.apiModels[url].then((r) => { options.replaceChildren(...r.items.map((id) => el("option", { value: id }))); modelNote.textContent = r.note || `${r.items.length} models offered`; });
    }
    const modelInput = el("input", { type: "text", value: settingNow("brain.api.model"), list: "api-models", placeholder: "e.g. gpt-4.1-mini", "aria-label": "Model" });
    modelInput.addEventListener("input", () => setSetting("brain.api.model", modelInput.value.trim()));
    const use = provider === "api" ? el("span", { class: "pill good" }, icon("check"), "In use")
      : el("button", { class: "btn primary", type: "button", text: "Use this service", onclick: () => {
        if (!settingNow("brain.api.url") || !settingNow("brain.api.model")) return toast("Fill in the service's address and model first.", true);
        if (!isLocalUrl(settingNow("brain.api.url")) && !askCloud(hostOf(settingNow("brain.api.url")))) return;
        setSetting("brain.provider", "api");
        render();
      } });
    body = el("div", {},
      !isLocalUrl(url) && el("div", { class: "banner info" }, el("span", { class: "grow", text: `Any service that speaks the OpenAI chat API. What you say, and what ${assistant()} reads on your screen to answer, is sent to it.` })),
      el("section", { class: "card" },
        row("Service address", F("brain.api.url").help, el("div", { class: "stack", style: "width:100%;gap:0" }, urlInput, picks), { wide: true, changed: "brain.api.url" in S.draft.settings }),
        row("API key", keySet ? "Saved in MARY's .env file, never shown again." : "Kept in MARY's .env file, never shown again. Not needed for a server on this PC.",
          el("div", { class: "actions", style: "width:100%;flex-wrap:nowrap" }, key, el("button", { class: "btn", type: "button", text: "Save key", onclick: saveKey })), { wide: true }),
        row("Model", F("brain.api.model").help, el("div", { class: "stack", style: "width:100%;gap:4px" }, modelInput, options, modelNote), { wide: true, changed: "brain.api.model" in S.draft.settings }),
        el("div", { class: "add-line", style: "justify-content:flex-end" }, use)));
  } else {
    const cloud = source === "cloud";
    const add = el("input", { type: "text", placeholder: cloud ? "Add a cloud model, like gpt-oss:120b-cloud" : "Add a model, like llama3.2:3b", "aria-label": "Model name" });
    add.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addModel(add, cloud); } });
    body = el("div", {},
      cloud && el("div", { class: "banner info" }, el("span", { class: "grow", text: `Cloud models run on Ollama's servers, so what you say, and what ${assistant()} reads on your screen to answer, is sent to ollama.com. They need a free ollama.com account: sign this PC in once by running “ollama signin”.` })),
      note,
      el("section", { class: "card" },
        el("div", { class: "card-head" }, el("div", {}, el("h2", {}, cloud ? "Cloud models" : "Models on this PC",
          info(cloud ? "Fast, and large enough for hard requests. They use your ollama.com allowance." : "Free and private. Bigger ones follow instructions better but answer slower.")))),
        list,
        el("div", { class: "add-line" }, add, el("button", { class: "btn", type: "button", onclick: () => addModel(add, cloud) }, icon(cloud ? "plus" : "download"), cloud ? "Add" : "Download")),
        pullLine(),
        el("p", { class: "help", style: "margin:0 0 14px" }, "Find models at ", el("a", { href: cloud ? "https://ollama.com/search?c=cloud" : "https://ollama.com/search", target: "_blank", rel: "noopener noreferrer", text: cloud ? "ollama.com/search?c=cloud" : "ollama.com/search" }), ".")));
  }

  const thinkRow = settingRow(F("llm.think_level"));
  thinkRow.hidden = provider === "ollama";   // shown once the list says the model offers levels
  const local = provider === "ollama" && !isCloudName(current);
  return el("div", {},
    head("Model", `The language model that thinks for ${assistant()}.`),
    summary,
    el("div", { class: "source" }, el("span", { class: "muted", text: "Where it runs" }),
      segmented([["local", "On this PC"], ["cloud", "Ollama Cloud"], ["api", "Another service"]], source, (v) => { S.modelSource = v; render(); }, "Where it runs")),
    body,
    el("section", { class: "card" }, el("div", { class: "card-head" }, el("div", {}, el("h2", { text: "Thinking" }))),
      settingRow(F("llm.think_by_default")), thinkRow),
    el("details", { class: "more" }, el("summary", {}, "Advanced", icon("chevron")),
      el("section", { class: "card" }, settingRow(F("brain.mode")), local && settingRow(F("llm.unload_on_quit")), local && settingRow(F("llm.num_thread")))));
}

function pageListening() {
  const cloud = settingNow("stt.provider") === "elevenlabs";
  return el("div", {},
    head("Listening", `How ${assistant()} hears you.`),
    cloud && !S.state.keys.elevenlabs && el("div", { class: "banner" }, el("span", { class: "grow", text: "ElevenLabs isn't connected, so speech is recognised on this PC for now." }),
      el("a", { class: "btn small", href: "#connections", text: "Connect" })),
    fieldsCard("listening", "Starting a conversation", "Wake phrases are set on Wake word, for each persona.", ["wakeword.enabled", "hotkey.combo"]),
    fieldsCard("listening", "Microphone", null, ["audio.input_device", "audio.clean.enabled"]),
    fieldsCard("listening", "Recognition", null, ["stt.provider", "stt.words", "stt.model"]),
    fieldsCard("listening", "While you talk", null, ["vad.followup_s", "audio.chime", "vad.long_end_silence_ms"]));
}

function pageSkills() {
  const on = new Set(settingNow("skills") || []);
  const groups = {};
  for (const s of S.state.skills) (groups[s.group] = groups[s.group] || []).push(s);
  const cur = S.state.settings.values.skills;
  const canReset = !("skills" in S.draft.settings) && cur.origin === "yours" && !same(cur.value, cur.base);
  return el("div", {},
    head("Skills", `What ${assistant()} can do on this PC. Fewer skills make the prompt shorter, so replies start sooner.`,
      canReset && el("button", { class: "btn", type: "button", text: "Reset to default", onclick: () => resetSetting("skills") })),
    Object.entries(groups).map(([group, list]) => card(group, null, el("div", { class: "list", style: "padding-bottom:6px" }, list.map((s) => el("div", { class: "item" },
      el("div", { class: "grow" }, el("div", { style: "font-weight:560", text: s.title }), el("div", { class: "sub", text: s.about })),
      toggle(on.has(s.name), (v) => { v ? on.add(s.name) : on.delete(s.name); setSetting("skills", S.state.skills.map((x) => x.name).filter((n) => on.has(n))); }, s.title)))))));
}

function pageConnections() {
  const has = S.state.keys.elevenlabs;
  const input = el("input", { type: "password", autocomplete: "off", placeholder: has ? "Paste a new key to replace it" : "Paste your API key", "aria-label": "ElevenLabs API key" });
  const credits = el("div", { class: "credits" });
  if (has) api("GET", "/api/credits").then((c) => {
    if (!c.known) return;
    const left = Math.max(0, c.limit - c.used);
    credits.replaceChildren(el("span", { text: `${left.toLocaleString()} of ${c.limit.toLocaleString()} credits left this month` }),
      el("div", { class: "bar-meter" }, el("i", { style: `width:${Math.min(100, (left / c.limit) * 100).toFixed(1)}%` })));
  }).catch(() => {});
  const ollama = el("span", { class: "pill", text: "Checking…" });
  const ollamaNote = el("span", { class: "muted" });
  choices("ollama").then((c) => {
    ollama.className = `pill ${c.running ? "good" : "warn"}`;
    ollama.textContent = c.running ? "Running" : "Not running";
    const cloud = c.items.filter((m) => m.cloud).length;
    const here = c.items.length - cloud;
    ollamaNote.textContent = c.running ? `${here} model${here === 1 ? "" : "s"} on this PC${cloud ? `, ${cloud} from Ollama's cloud` : ""}` : "Starts with MARY";
  });
  const service = S.state.settings.values["brain.api.url"].value;
  const saveKey = async () => {
    try {
      S.state.keys = await api("POST", "/api/keys", { name: "ELEVENLABS_API_KEY", value: input.value });
      input.value = "";
      S.choices.elevenlabs = null;
      S.saved = true;
      toast("ElevenLabs connected");
      render();
    } catch (e) { toast(e.message, true); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveKey(); } });
  return el("div", {},
    head("Connections", "Services beyond this PC. Keys are kept in MARY's .env file and never shown again."),
    card("ElevenLabs", "Optional: faster speech recognition, and your own or cloned voices.",
      row("Status", null, el("span", { class: `pill ${has ? "good" : ""}`, text: has ? "Connected" : "Not connected" })),
      has && row("Credits", null, el("div", { style: "width:100%" }, credits)),
      row(has ? "Replace the key" : "API key", "Create one in your ElevenLabs account, under API keys.",
        el("div", { class: "actions", style: "width:100%;flex-wrap:nowrap" }, input, el("button", { class: "btn primary", type: "button", text: "Save", onclick: saveKey })))),
    card("Ollama", "Runs the language model on this PC, and passes requests on to Ollama's cloud models.", row("Status", null, el("span", { class: "actions" }, ollama, ollamaNote))),
    card("Model service", "Optional: any service that speaks the OpenAI chat API, set up on the Model page.",
      row("Address", null, el("span", { class: service ? "" : "muted", text: service ? hostOf(service) : "Not set" })),
      row("API key", null, el("span", { class: `pill ${S.state.keys.llm ? "good" : ""}`, text: S.state.keys.llm ? "Saved" : "Not set" })),
      el("div", { class: "add-line", style: "justify-content:flex-end" }, el("a", { class: "btn", href: "#model", onclick: () => { S.modelSource = "api"; }, text: "Set it up" }))));
}

const RENDER = { identity: pageIdentity, personality: pagePersonality, voice: pageVoice, wake: pageWake,
  about: pageAbout, remembered: pageRemembered, stories: pageStories, photos: pagePhotos, people: pagePeople,
  listening: pageListening, model: pageModel, skills: pageSkills, connections: pageConnections };

// ------------------------------------------------------------------ frame

function currentPage() {
  let [page, sub] = location.hash.slice(1).split("/");
  if (page === "persona") {   // the old Persona page and its tabs (#persona/stories)
    page = RENDER[sub] ? sub : "identity";
    history.replaceState(null, "", `#${page}`);
  }
  return RENDER[page] ? [page] : ["identity"];
}

function renderBar(busy) {
  const bar = document.getElementById("bar");
  let parts = null;
  if (S.restarting) parts = [el("span", { class: "msg", text: "Restarting MARY…" })];
  else if (busy) parts = [el("span", { class: "msg", text: busy })];
  else if (dirty()) parts = [el("span", { class: "msg", text: "Unsaved changes" }),
    el("button", { class: "btn", type: "button", text: "Discard", onclick: discard }),
    el("button", { class: "btn primary", type: "button", text: "Save", onclick: () => save() })];
  else if (S.state.changed && S.state.running) parts = [el("span", { class: "msg", text: "Saved. Restart MARY to use the changes." }),
    S.state.can_restart ? el("button", { class: "btn primary", type: "button", onclick: restart }, icon("restart"), "Restart")
      : el("span", { class: "muted", text: "Restart it yourself" })];
  else if (S.saved) parts = [el("span", { class: "msg", text: "Saved. Changes apply the next time MARY starts." }),
    el("button", { class: "btn", type: "button", text: "OK", onclick: () => { S.saved = false; renderBar(); } })];
  bar.hidden = !parts;
  if (parts) bar.replaceChildren(...parts);
}

function render() {
  const [page, sub] = currentPage();
  renderSide(page);
  const y = window.scrollY;
  document.getElementById("main").replaceChildren(RENDER[page](sub));
  window.scrollTo(0, y);
  renderBar();
}

async function boot() {
  try {
    S.state = await api("GET", "/api/state");
    S.editing = S.state.settings.persona;
    S.her = await api("GET", `/api/personas/${S.editing}`);
  } catch (e) {
    document.getElementById("main").replaceChildren(el("p", { class: "muted loading", text: `Couldn't load the settings: ${e.message}` }));
    return;
  }
  window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
  window.addEventListener("beforeunload", (e) => { if (dirty() || (S.trainer && S.trainer.busy)) { e.preventDefault(); e.returnValue = ""; } });
  render();
}

boot();
