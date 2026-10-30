// MARY HUD page logic. Python drives it through window.hud.* (QWebEnginePage.runJavaScript).
// The canvas loop only runs while the HUD is shown; hide() stops it and pauses every CSS animation.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = $("hud"), you = $("you"), reply = $("reply"), log = $("log");
  const panel = document.querySelector(".panel");
  const canvas = $("wave"), g = canvas.getContext("2d");
  const core = document.querySelector(".core");

  let state = "idle";
  let colour = "#39e6ff";                         // read once per state change, not every frame
  let lastFrame = 0;
  let shown = false, looping = false;
  let level = 0, target = 0, lastLevel = 0;      // smoothed level, latest real level, its timestamp
  const history = new Float32Array(120);          // recent levels, drawn around the core
  let pending = "", ended = true, typer = null;   // typewriter queue for the reply

  // ------------------------------------------------------------------ visibility
  function show() {
    shown = true;
    root.classList.remove("hidden", "asleep", "scanning");
    root.classList.add("shown");
    void root.offsetWidth;                         // restart the scan-line animation
    root.classList.add("scanning");
    startLoop();
  }

  function hide() {
    shown = false;
    root.classList.remove("shown");
    root.classList.add("hidden");
    setTimeout(() => { if (!shown) root.classList.add("asleep"); }, 400);
  }

  function setState(name) {
    state = name;
    root.className = root.className.replace(/\bstate-\S+/, "state-" + name);
    colour = getComputedStyle(root).getPropertyValue("--c").trim() || colour;
  }

  // ------------------------------------------------------------------ text
  function refreshPanel() {
    panel.classList.toggle("blank", !you.textContent && !reply.textContent && !log.childElementCount);
  }

  function user(text) {
    you.textContent = text;
    reply.textContent = "";
    log.innerHTML = "";
    pending = "";
    refreshPanel();
  }

  function replyStart() {
    if (reply.textContent) reply.textContent += " ";  // a second reply in the same request continues
    ended = false;
    reply.classList.add("typing");
    refreshPanel();
  }

  function replyDelta(text) {
    pending += text;
    if (!typer) typer = setInterval(type, 16);
  }

  function type() {
    if (pending) {
      const n = pending.length > 80 ? 6 : 2;       // catch up when a long reply arrives at once
      reply.textContent += pending.slice(0, n);
      pending = pending.slice(n);
      refreshPanel();
    } else {
      clearInterval(typer);
      typer = null;
      if (ended) reply.classList.remove("typing");
    }
  }

  function replyEnd() {
    ended = true;
    if (!typer) reply.classList.remove("typing");
  }

  function logLine(line) {
    const div = document.createElement("div");
    div.textContent = line;
    if (line.startsWith("✓")) div.className = "ok";
    else if (line.startsWith("✗")) div.className = "err";
    log.appendChild(div);
    while (log.childElementCount > 4) log.firstChild.remove();
    refreshPanel();
  }

  function notify(text) { logLine("⏰ " + text); }

  function ask(question) {                         // a confirmation replaces the reply line
    pending = "";
    reply.textContent = question;
    reply.classList.remove("typing");
    refreshPanel();
  }

  function setLevel(x) { target = Math.max(0, Math.min(1, x)); lastLevel = performance.now(); }

  // ------------------------------------------------------------------ animation loop
  function startLoop() {
    if (looping) return;
    looping = true;
    requestAnimationFrame(frame);
  }

  function synthetic(t) {
    // No live audio yet (text mode, or before Phase 4's TTS envelope): a gentle stand-in per state.
    switch (state) {
      case "speaking":      return 0.35 + 0.35 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 2.1 + 1));
      case "listening":     return 0.10 + 0.06 * Math.sin(t * 2.2);
      case "thinking":      return 0.18 + 0.05 * Math.sin(t * 5);
      case "deep_thinking": return 0.22 + 0.08 * Math.sin(t * 6);
      case "confirming":    return 0.20 + 0.10 * Math.abs(Math.sin(t * 3));
      default:              return 0.05;
    }
  }

  function frame(now) {
    if (!shown) { looping = false; return; }
    requestAnimationFrame(frame);
    if (now - lastFrame < 32) return;              // ~30 fps is plenty for a waveform, and half the CPU
    lastFrame = now;
    const t = now / 1000;
    const want = now - lastLevel < 400 ? target : synthetic(t);
    level += (want - level) * 0.35;
    // Set the core's transform directly: changing a CSS variable on the root would restyle every ring.
    core.style.transform = `scale(${(0.86 + level * 0.34).toFixed(3)})`;
    history.copyWithin(1, 0);
    history[0] = level;
    draw(t);
  }

  function draw(t) {
    const w = canvas.width, h = canvas.height, cx = w / 2, cy = h / 2;
    g.clearRect(0, 0, w, h);
    g.lineWidth = 1.6;
    g.strokeStyle = colour;
    g.shadowColor = colour;
    g.shadowBlur = 5;
    g.globalAlpha = 0.9;
    g.beginPath();
    const n = history.length, base = 70;
    for (let i = 0; i <= n; i++) {
      const k = i % n;
      const a = (k / n) * Math.PI * 2 - Math.PI / 2;
      // Mirror the history around the circle so the newest sample sits at the top and fades out.
      const v = history[k < n / 2 ? k : n - k];
      const r = base + v * 26 * (0.6 + 0.4 * Math.sin(a * 6 + t * 3));
      const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
    }
    g.closePath();
    g.stroke();
  }

  window.hud = { show, hide, state: setState, user, replyStart, replyDelta, replyEnd,
                 log: logLine, notify, ask, level: setLevel };
  refreshPanel();
})();
