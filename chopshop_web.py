#!/usr/bin/env python3
"""chopshop_web - the kids' "Sound Safari" review station.

A tiny local web app (Python standard library only -- no Flask, no install) that
lets a kid go through the staged slices one at a time: listen, keep or trash,
confirm or change the category, give it a fun name, and fix bad cuts by trimming
or merging with the next slice.

    python chopshop_web.py --staging ./_staging
    # then open http://localhost:8000 in a browser

Nothing here touches the final library; it only edits ``staging.json`` (see
:mod:`chopshop_core`). When the kids finish, chopshop_build.py publishes whatever
they marked "keep".

Why stdlib only: it must run on a bare interpreter the moment numpy/soundfile are
present, with zero front-end tooling, so an 11-year-old can just open a URL.
"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np
import soundfile as sf

from chopshop_core import (
    KID_LABELS,
    Slice,
    carve,
    load_mono,
    read_staging,
    write_staging,
)


# --------------------------------------------------------------------------- #
# server state -- one staging dir, edited live by the browser
# --------------------------------------------------------------------------- #
class Library:
    """In-memory view of the staging set, persisted to staging.json on edit.

    A lock guards concurrent requests (the browser fires several at once). All
    mutating handlers go through here so the on-disk manifest stays consistent.
    """

    def __init__(self, staging_dir: Path):
        self.dir = staging_dir
        self.lock = threading.Lock()
        self.slices: list[Slice] = read_staging(staging_dir)

    # -- persistence -----------------------------------------------------
    def save(self) -> None:
        write_staging(self.dir, self.slices)

    # -- queries ---------------------------------------------------------
    def summary(self) -> dict:
        kept = sum(1 for s in self.slices if s.status == "keep")
        trashed = sum(1 for s in self.slices if s.status == "trash")
        pending = sum(1 for s in self.slices if s.status == "pending")
        return {
            "total": len(self.slices),
            "kept": kept,
            "trashed": trashed,
            "pending": pending,
            "categories": [
                {"slug": slug, "emoji": emoji, "name": name}
                for slug, _phrase, emoji, name in KID_LABELS
            ],
            "slices": [s.to_dict() for s in self.slices],
        }

    def _find(self, idx: int) -> Slice | None:
        return self.slices[idx] if 0 <= idx < len(self.slices) else None

    # -- mutations -------------------------------------------------------
    def set_status(self, idx: int, status: str) -> bool:
        with self.lock:
            s = self._find(idx)
            if not s:
                return False
            s.status = status
            self.save()
            return True

    def set_category(self, idx: int, slug: str) -> bool:
        with self.lock:
            s = self._find(idx)
            if not s:
                return False
            s.category = slug
            # a deliberate human choice is full confidence
            s.confidence = 1.0
            self.save()
            return True

    def set_name(self, idx: int, name: str) -> bool:
        with self.lock:
            s = self._find(idx)
            if not s:
                return False
            s.custom_name = name.strip()
            self.save()
            return True

    def merge_with_next(self, idx: int) -> bool:
        """Glue slice ``idx`` and ``idx+1`` (same source) into one new wav."""
        with self.lock:
            a = self._find(idx)
            b = self._find(idx + 1)
            if not a or not b or a.source != b.source:
                return False
            ya, sra = self._read_wav(a)
            yb, srb = self._read_wav(b)
            if ya is None or yb is None or sra != srb:
                return False
            merged = np.concatenate([ya, yb]).astype(np.float32)
            sf.write(str(self.dir / a.staging_path), merged, sra, subtype="PCM_24")
            a.end_sec = b.end_sec
            a.status = "pending"
            # drop b's wav + record
            self._unlink(b)
            self.slices.pop(idx + 1)
            self.save()
            return True

    def trim(self, idx: int, start_frac: float, end_frac: float) -> bool:
        """Keep only [start_frac, end_frac] of the slice (0..1), re-fade, save.

        This is the "split / fix a bad cut" tool: the kid drags handles on the
        waveform to lop off a wrong attack or a trailing second sound.
        """
        with self.lock:
            s = self._find(idx)
            if not s:
                return False
            y, sr = self._read_wav(s)
            if y is None or len(y) == 0:
                return False
            lo = max(0, min(len(y), int(len(y) * start_frac)))
            hi = max(lo + 1, min(len(y), int(len(y) * end_frac)))
            seg = carve(y, sr, lo, hi)
            sf.write(str(self.dir / s.staging_path), seg, sr, subtype="PCM_24")
            span = s.end_sec - s.start_sec
            s.start_sec = round(s.start_sec + span * start_frac, 4)
            s.end_sec = round(s.start_sec + (hi - lo) / sr, 4)
            self.save()
            return True

    # -- wav helpers -----------------------------------------------------
    def _read_wav(self, s: Slice):
        try:
            return load_mono(self.dir / s.staging_path)
        except Exception:
            return None, 0

    def _unlink(self, s: Slice) -> None:
        try:
            (self.dir / s.staging_path).unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# request handler
# --------------------------------------------------------------------------- #
def make_handler(lib: Library):
    class Handler(BaseHTTPRequestHandler):
        # quieter logs -- this runs in front of kids
        def log_message(self, *_a):  # noqa: N802
            pass

        # -- helpers -----------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        # -- routes ------------------------------------------------------
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path
            if route == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif route == "/api/state":
                self._json(lib.summary())
            elif route == "/api/audio":
                self._serve_audio(parse_qs(parsed.query))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"ok": False, "error": "bad json"}, 400)

            route = urlparse(self.path).path
            idx = int(data.get("index", -1))
            ok = False
            if route == "/api/keep":
                ok = lib.set_status(idx, "keep")
            elif route == "/api/trash":
                ok = lib.set_status(idx, "trash")
            elif route == "/api/category":
                ok = lib.set_category(idx, str(data.get("slug", "")))
            elif route == "/api/name":
                ok = lib.set_name(idx, str(data.get("name", "")))
            elif route == "/api/merge":
                ok = lib.merge_with_next(idx)
            elif route == "/api/trim":
                ok = lib.trim(idx, float(data.get("start", 0.0)),
                              float(data.get("end", 1.0)))
            else:
                return self._json({"ok": False, "error": "unknown route"}, 404)
            self._json({"ok": ok, "state": lib.summary()})

        def _serve_audio(self, qs: dict) -> None:
            try:
                idx = int(qs.get("index", ["-1"])[0])
            except ValueError:
                return self._send(400, b"bad index", "text/plain")
            s = lib._find(idx)
            if not s:
                return self._send(404, b"no slice", "text/plain")
            path = lib.dir / s.staging_path
            if not path.exists():
                return self._send(404, b"missing wav", "text/plain")
            self._send(200, path.read_bytes(), "audio/wav")

    return Handler


# --------------------------------------------------------------------------- #
# the page (one self-contained file: HTML + CSS + JS)
# --------------------------------------------------------------------------- #
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sound Safari 🎧</title>
<style>
  :root { --bg:#0f1226; --card:#1b1f3b; --accent:#ffd166; --good:#06d6a0;
          --bad:#ef476f; --ink:#f5f7ff; }
  * { box-sizing:border-box; font-family:system-ui,Segoe UI,Arial,sans-serif; }
  body { margin:0; background:var(--bg); color:var(--ink); }
  header { padding:16px 20px; background:var(--card); display:flex;
           align-items:center; gap:14px; }
  header h1 { font-size:22px; margin:0; }
  .bar { flex:1; height:14px; background:#2a2f55; border-radius:8px; overflow:hidden; }
  .bar > div { height:100%; background:var(--good); width:0%; transition:width .3s; }
  .count { font-variant-numeric:tabular-nums; opacity:.85; }
  main { max-width:640px; margin:0 auto; padding:22px; }
  .card { background:var(--card); border-radius:18px; padding:22px;
          box-shadow:0 8px 30px rgba(0,0,0,.35); }
  .src { opacity:.6; font-size:13px; }
  canvas { width:100%; height:120px; background:#11142b; border-radius:12px;
           margin:12px 0; display:block; cursor:crosshair; }
  .play { width:100%; font-size:26px; padding:18px; border:none; border-radius:14px;
          background:var(--accent); color:#222; font-weight:800; cursor:pointer; }
  .guess { text-align:center; margin:16px 0 6px; font-size:15px; opacity:.85; }
  .cats { display:grid; grid-template-columns:repeat(5,1fr); gap:8px; margin-top:8px; }
  .cat { background:#252a52; border:3px solid transparent; border-radius:14px;
         padding:10px 4px; text-align:center; cursor:pointer; font-size:12px; }
  .cat .emo { font-size:26px; display:block; }
  .cat.sel { border-color:var(--accent); background:#33386a; }
  .name { width:100%; margin-top:14px; padding:12px; font-size:16px;
          border-radius:12px; border:none; }
  .row { display:flex; gap:10px; margin-top:16px; }
  .row button { flex:1; font-size:18px; padding:16px; border:none; border-radius:14px;
                font-weight:800; cursor:pointer; color:#fff; }
  .keep { background:var(--good); } .trash { background:var(--bad); }
  .tool { background:#3a3f73; }
  .done { text-align:center; padding:60px 20px; }
  .done h2 { font-size:40px; }
  .hint { font-size:12px; opacity:.6; text-align:center; margin-top:10px; }
</style>
</head>
<body>
<header>
  <h1>🎧 Sound Safari</h1>
  <div class="bar"><div id="prog"></div></div>
  <span class="count" id="count">0 / 0</span>
</header>
<main id="app"></main>

<script>
let STATE = null;      // server summary
let CUR = 0;           // index of slice under review
let TRIM = null;       // {start,end} fractions while dragging the waveform
const audio = new Audio();

async function load() {
  STATE = await (await fetch('/api/state')).json();
  CUR = firstPending();
  render();
}
function firstPending() {
  const i = STATE.slices.findIndex(s => s.status === 'pending');
  return i === -1 ? Math.min(CUR, STATE.slices.length - 1) : i;
}
async function post(route, body) {
  const r = await fetch(route, {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify(body)});
  const j = await r.json();
  if (j.state) STATE = j.state;
  return j;
}
function pct() {
  if (!STATE.total) return 0;
  return Math.round((STATE.kept + STATE.trashed) / STATE.total * 100);
}
function emojiFor(slug) {
  const c = STATE.categories.find(c => c.slug === slug);
  return c ? c.emoji : '❓';
}

function render() {
  document.getElementById('prog').style.width = pct() + '%';
  document.getElementById('count').textContent =
      (STATE.kept + STATE.trashed) + ' / ' + STATE.total;
  const app = document.getElementById('app');

  const remaining = STATE.slices.some(s => s.status === 'pending');
  if (!remaining || CUR >= STATE.slices.length) {
    app.innerHTML = `<div class="card done"><h2>🎉 All done!</h2>
      <p>You kept <b>${STATE.kept}</b> sounds and tossed ${STATE.trashed}.</p>
      <p>Tell a grown-up to run the build step to put them in Ableton!</p></div>`;
    return;
  }
  const s = STATE.slices[CUR];
  TRIM = {start:0, end:1};
  app.innerHTML = `
    <div class="card">
      <div class="src">from ${s.source} &middot; slice ${s.index}</div>
      <canvas id="wave" width="600" height="120"></canvas>
      <button class="play" onclick="play()">▶ Play sound</button>
      <div class="guess">My guess: <b>${emojiFor(s.category)} ${s.category}</b>
        ${s.confidence ? '('+Math.round(s.confidence*100)+'% sure)' : ''}</div>
      <div class="cats">${STATE.categories.map(c =>
        `<div class="cat ${c.slug===s.category?'sel':''}" onclick="pick('${c.slug}')">
           <span class="emo">${c.emoji}</span>${c.name}</div>`).join('')}</div>
      <input class="name" id="nm" placeholder="Give it a fun name (optional)"
             value="${s.custom_name||''}" onchange="rename(this.value)">
      <div class="row">
        <button class="keep" onclick="keep()">✅ Keep it!</button>
        <button class="trash" onclick="trash()">🗑️ Toss it</button>
      </div>
      <div class="row">
        <button class="tool" onclick="applyTrim()">✂️ Trim to selection</button>
        <button class="tool" onclick="merge()">🔗 Join with next</button>
      </div>
      <div class="hint">Drag on the waveform to pick a part, then Trim. Skip with Keep/Toss.</div>
    </div>`;
  drawWave();
}

function play() { audio.src = '/api/audio?index=' + CUR + '&t=' + Date.now(); audio.play(); }
async function keep()  { await post('/api/keep',  {index:CUR}); next(); }
async function trash() { await post('/api/trash', {index:CUR}); next(); }
async function pick(slug){ await post('/api/category', {index:CUR, slug}); render(); }
async function rename(v){ await post('/api/name', {index:CUR, name:v}); }
async function merge() { const j = await post('/api/merge', {index:CUR});
  if (!j.ok) alert("Can't join — no next slice from the same recording."); render(); }
async function applyTrim() {
  if (!TRIM || (TRIM.start===0 && TRIM.end===1)) { alert('Drag on the waveform first.'); return; }
  await post('/api/trim', {index:CUR, start:TRIM.start, end:TRIM.end});
  render(); play();
}
function next() { CUR = firstPending(); render(); }

// --- waveform draw + drag-to-select -------------------------------------
let peaks = null;
async function drawWave() {
  const c = document.getElementById('wave'); if (!c) return;
  const ctx = c.getContext('2d');
  try {
    const buf = await (await fetch('/api/audio?index='+CUR)).arrayBuffer();
    const ac = new (window.AudioContext||window.webkitAudioContext)();
    const dec = await ac.decodeAudioData(buf);
    const data = dec.getChannelData(0);
    const N = c.width, step = Math.max(1, Math.floor(data.length / N));
    peaks = new Array(N);
    for (let i=0;i<N;i++){ let m=0; for(let j=0;j<step;j++){ const v=Math.abs(data[i*step+j]||0); if(v>m)m=v; } peaks[i]=m; }
    paint();
  } catch(e) { ctx.fillStyle='#666'; ctx.fillText('(waveform unavailable)',10,60); }
}
function paint() {
  const c = document.getElementById('wave'); if(!c||!peaks) return;
  const ctx = c.getContext('2d'); ctx.clearRect(0,0,c.width,c.height);
  if (TRIM){ ctx.fillStyle='rgba(255,209,102,.18)';
    ctx.fillRect(TRIM.start*c.width,0,(TRIM.end-TRIM.start)*c.width,c.height); }
  ctx.strokeStyle='#7ee0c0'; ctx.beginPath();
  for(let i=0;i<peaks.length;i++){ const h=peaks[i]*c.height; ctx.moveTo(i,(c.height-h)/2); ctx.lineTo(i,(c.height+h)/2); }
  ctx.stroke();
}
let dragging=false, dragStart=0;
document.addEventListener('mousedown', e=>{ if(e.target.id==='wave'){ dragging=true;
  dragStart=e.offsetX/e.target.width; TRIM={start:dragStart,end:dragStart}; }});
document.addEventListener('mousemove', e=>{ if(dragging&&e.target.id==='wave'){
  const x=e.offsetX/e.target.width; TRIM={start:Math.min(dragStart,x),end:Math.max(dragStart,x)}; paint(); }});
document.addEventListener('mouseup', ()=>{ dragging=false; });
document.addEventListener('keydown', e=>{ if(e.code==='Space'){ e.preventDefault(); play(); }
  if(e.key==='Enter') keep(); });

load();
</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def serve(staging_dir: Path, port: int = 8000, open_browser: bool = True) -> None:
    lib = Library(staging_dir)
    if not lib.slices:
        print(f"No staged slices in {staging_dir}. Run the slice step first.")
        return
    handler = make_handler(lib)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://localhost:{port}"
    print(f"Sound Safari running at {url}  ({len(lib.slices)} sounds to review)")
    print("  Leave this window open. Press Ctrl+C when the kids are done.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping. Decisions saved to staging.json.")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="chopshop_web",
        description="Kid-friendly review station for staged sounds.",
    )
    p.add_argument("--staging", type=Path, default=Path("./_staging"),
                   help="staging directory (default ./_staging)")
    p.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    p.add_argument("--no-open", action="store_true",
                   help="don't auto-open the browser")
    args = p.parse_args(argv)
    serve(args.staging, port=args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
