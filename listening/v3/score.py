#!/usr/bin/env python3
"""score.py: turn a finished sealed listen into a page that plays the notes against the music.

    ./venv3/bin/python v3/score.py runs/<run>            # writes runs/<run>/score/index.html (+ song.m4a)
    open runs/<run>/score/index.html

It reads only what the listen left behind: state.json, notes/NNN.md, notes/after.md, seal.log and the
revealed pictures in windows/. Nothing is retyped: the notes on the page are the sealed files. The audio is
the same song.wav the pictures were measured from (transcoded to AAC), so time zero matches.

The page crops each picture to its measured panels (no lyric lane, no lyric lines), tiles the 15 s windows
side by side and scrolls them under a fixed playhead driven by audio.currentTime.

The output holds the recording, so it stays in the run folder (gitignored). Don't publish it.

    ./venv3/bin/python v3/score.py runs/<run> --youtube VIDEO_ID     # writes runs/<run>/share/

With --youtube the page holds no audio: it embeds that YouTube video and follows the player's clock. Use the
video the audio was fetched from, so time zero matches. The share folder is self-contained (index.html + the
cropped strips in w/) and has no lyric text in it; it needs to be served over http(s), since YouTube refuses
to embed in a page opened as a file.
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys

# Panel geometry of the two-picture layout (pixels in the 1568-wide PNGs see.py renders at DPI 100).
# x: the shared 15 s time axis. y: each panel's rows, with the caption strip at its top left out.
AXIS_X0, AXIS_X1, IMG_W = 118, 1545, 1568
VOICE_H, BAND_H = 1150, 1050
TRACKS = [
    # id, label, hint, image, y0, y1, share of the score's height
    ("lead", "lead voice", "cyan line = pitch", "voice", 72, 437, 0.40),
    ("backing", "other voices", "", "voice", 466, 545, 0.08),
    ("drums", "drums", "", "band", 62, 157, 0.09),
    ("bass", "bass", "", "band", 163, 274, 0.09),
    ("guitar", "guitar", "", "band", 280, 390, 0.09),
    ("piano", "keys", "", "band", 397, 507, 0.09),
    ("other", "other", "", "band", 514, 625, 0.09),
]
# The "song so far" strip of the last window's band picture: the whole song on one axis.
SOFAR_Y0, SOFAR_Y1 = 862, 943
# Lead panel frequency ticks (Hz -> y pixel in the voice picture), read off the rendered axis.
LEAD_TICKS = [("200", 386), ("400", 343), ("800", 288), ("1.6k", 223), ("3.2k", 153)]

LABELS = ("feel", "voice", "band", "words", "expect")
STRIP_H = 640          # pixel height of the composite strip written per window (the page scales it down)


def composite(run, out, nwin):
    """One JPEG per window: the track crops stacked, nothing else from the pictures (no words, no lyric lines)."""
    from PIL import Image
    os.makedirs(os.path.join(out, "w"), exist_ok=True)
    wd, total = AXIS_X1 - AXIS_X0, sum(t[6] for t in TRACKS)
    hs = [round(STRIP_H * t[6] / total) for t in TRACKS]
    boxes, y = [], 0
    for h in hs:
        boxes.append((y, h)); y += h
    for k in range(nwin):
        src = {n: Image.open(os.path.join(run, "windows", f"{k:03d}-{n}.png")).convert("RGB") for n in ("voice", "band")}
        strip = Image.new("RGB", (wd, y))
        for t, (top, h) in zip(TRACKS, boxes):
            strip.paste(src[t[3]].crop((AXIS_X0, t[4], AXIS_X1, t[5])).resize((wd, h - 1), Image.LANCZOS), (0, top))
        strip.save(os.path.join(out, "w", f"{k:03d}.jpg"), quality=86, optimize=True)
        if k == nwin - 1:
            src["band"].crop((AXIS_X0, SOFAR_Y0, AXIS_X1, SOFAR_Y1)).save(os.path.join(out, "w", "map.jpg"), quality=88)
    tracks = [dict(id=t[0], label=t[1], hint=t[2], top=top / y, h=h / y) for t, (top, h) in zip(TRACKS, boxes)]
    lead_top, lead_h = boxes[0]
    ticks = [(txt, (lead_top + (py - TRACKS[0][4]) / (TRACKS[0][5] - TRACKS[0][4]) * lead_h) / y) for txt, py in LEAD_TICKS]
    return tracks, ticks


def parse_note(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]?\s*\**(feel|voice|band|words|expect)\**\s*:\**\s*(.*)$", line, re.I)
        if m:
            cur = m.group(1).lower()
            out[cur] = m.group(2).strip()
        elif cur and line.strip():
            out[cur] += " " + line.strip()
    return out


def parse_seal_log(path):
    opened, sealed = {}, {}
    if not os.path.exists(path):
        return opened, sealed
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^(\S+) (\S+)\s+window (\d+) revealed", line)
        if m:
            opened[int(m.group(3))] = (m.group(1), m.group(2))
        m = re.match(r"^(\S+) (\S+)\s+note (\d+) sealed\s+sha256 (\w+)", line)
        if m:
            sealed[int(m.group(3))] = (m.group(1), m.group(2), m.group(4)[:10])
    return opened, sealed


def inline(text):
    """Escape, then **bold** and m:ss timestamps (made clickable by the page)."""
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\d:])(\d):([0-5]\d)(?!\d|:\d)",
               lambda m: f'<button class="t" data-t="{int(m.group(1)) * 60 + int(m.group(2))}">{m.group(0)}</button>', s)
    return s


def markdown(text):
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        if not line.strip():
            flush()
        elif line.startswith("## "):
            flush(); out.append(f"<h3>{inline(line[3:])}</h3>")
        elif line.startswith("# "):
            flush(); out.append(f"<h2>{inline(line[2:])}</h2>")
        elif line.startswith("- "):
            if para:
                flush()
            items.append(line[2:])
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return "\n".join(out)


def mmss(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


def build(run, out, audio, by, note="", youtube="", link=""):
    st = json.load(open(os.path.join(run, "state.json"), encoding="utf-8"))
    if st.get("layout") not in ("voice+band", "voice+band+harmony"):
        sys.exit("score.py needs a first-listen run with the voice and band pictures")
    nwin, win, dur = st["nwin"], float(st["window"]), float(st["duration"])
    if st.get("revealed", 0) < nwin or not os.path.exists(os.path.join(run, "notes", "after.md")):
        sys.exit("the listen isn't finished: every window revealed and notes/after.md sealed first")
    opened, sealed = parse_seal_log(os.path.join(run, "seal.log"))
    os.makedirs(out, exist_ok=True)
    tracks, ticks = composite(run, out, nwin)

    notes = []
    for k in range(nwin):
        n = parse_note(open(os.path.join(run, "notes", f"{k:03d}.md"), encoding="utf-8").read())
        notes.append({
            "k": k, "t0": k * win, "t1": min((k + 1) * win, dur),
            "span": f"{mmss(k * win)}-{mmss(min((k + 1) * win, dur))}",
            **{lab: inline(n.get(lab, "")) for lab in LABELS},
            "feelPlain": n.get("feel", ""),
            "opened": opened.get(k, ("", ""))[1], "sealed": sealed.get(k, ("", "", ""))[1],
            "sha": sealed.get(k, ("", "", ""))[2],
        })

    src = audio or os.path.join(st["work"], "song.wav")
    dst = os.path.join(out, "song.m4a")
    if not youtube and not os.path.exists(dst):
        if not os.path.exists(src):
            sys.exit(f"no audio at {src}; pass --audio")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-vn", "-c:a", "aac", "-b:a", "160k", dst],
                       check=True)

    first = opened.get(0, ("", ""))
    last = sealed.get(nwin - 1, ("", "", ""))
    took = ""
    if first[1] and last[1]:
        a = sum(int(x) * m for x, m in zip(first[1].split(":"), (3600, 60, 1)))
        b = sum(int(x) * m for x, m in zip(last[1].split(":"), (3600, 60, 1)))
        if b > a:
            took = f"{(b - a) // 60} min {(b - a) % 60} s"
    data = {
        "artist": st.get("artist", ""), "title": st.get("title", ""), "dur": dur, "win": win, "nwin": nwin,
        "notes": notes, "img": "w", "audio": "" if youtube else "song.m4a", "yt": youtube, "by": by, "note": note, "link": link,
        "date": first[0], "start": first[1], "end": last[1], "took": took,
        "tracks": tracks, "leadTicks": ticks,
        "after": markdown(open(os.path.join(run, "notes", "after.md"), encoding="utf-8").read()),
    }
    page = PAGE.replace("__TITLE__", html.escape(f"{data['title']}, listened to in order"))
    page = page.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    path = os.path.join(out, "index.html")
    open(path, "w", encoding="utf-8").write(page)
    return path


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #0b0a0e; --panel: #131218; --line: #26242e; --text: #ece7dd; --muted: #948e9c; --dim: #5f5a68;
  --cyan: #3ad8ee; --warm: #f4a259; --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Charter, Georgia, serif;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--sans); -webkit-font-smoothing: antialiased; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
button:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; border-radius: 3px; }

header { display: flex; align-items: flex-end; justify-content: space-between; gap: 32px; padding: 16px 28px 12px; }
header h1 { font-family: var(--serif); font-weight: 500; font-size: 26px; line-height: 1.15; margin: 0; letter-spacing: -0.01em; }
header h1 em { color: var(--muted); font-style: italic; }
header .sub { color: var(--muted); font-size: 13px; margin-top: 6px; }
header .how { max-width: 820px; color: var(--muted); font-size: 13px; line-height: 1.5; margin: 8px 0 0; text-wrap: pretty; }
header .lede { min-width: 0; }
header .cta { margin: 8px 0 0; font-size: 13px; color: var(--muted); }
header .cta a, #after .made a { color: var(--cyan); text-decoration: none; border-bottom: 1px solid rgba(58,216,238,.45); }
header .cta a:hover, #after .made a:hover { border-bottom-color: var(--cyan); }
#video { flex: none; width: 272px; aspect-ratio: 16 / 9; background: #000; border-radius: 6px; overflow: hidden; }
#video[hidden] { display: none; }
#video iframe { width: 100%; height: 100%; border: 0; display: block; }

#transport { display: flex; align-items: center; gap: 16px; padding: 0 28px 12px; }
#play { width: 44px; height: 44px; border-radius: 50%; background: var(--text); color: var(--bg); display: grid; place-items: center; flex: none; }
#play svg { width: 18px; height: 18px; fill: currentColor; }
#clock { font-family: var(--mono); font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; flex: none; width: 92px; }
#clock b { color: var(--text); font-weight: 500; }
#map { position: relative; flex: 1; height: 44px; border-radius: 4px; overflow: hidden; cursor: pointer; background: #000; }
#map .img { position: absolute; inset: 0; background-repeat: no-repeat; background-size: 100% 100%; }
#map .unheard { position: absolute; top: 0; bottom: 0; right: 0; background: rgba(11,10,14,.6); border-left: 2px solid var(--cyan); }
#map .tick { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(255,255,255,.14); }
#seal { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); flex: none; user-select: none; cursor: pointer; }
#seal input { accent-color: var(--cyan); margin: 0; }

#score { position: relative; overflow: hidden; background: #000; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); cursor: pointer; }
#strip { position: absolute; left: 0; top: 0; display: flex; will-change: transform; }
.col { position: relative; flex: none; overflow: hidden; border-right: 1px solid rgba(255,255,255,.22); }
.col .head { height: 22px; padding: 0 8px; font-family: var(--mono); font-size: 11px; line-height: 22px; color: var(--muted); background: var(--panel); white-space: nowrap; }
.col.now .head { color: var(--text); }
.tile { background-repeat: no-repeat; background-size: 100% 100%; }
.col .veil { position: absolute; left: 0; right: 0; top: 22px; bottom: 0; display: grid; place-items: center; background: repeating-linear-gradient(135deg, #17161c 0 14px, #131218 14px 28px); color: var(--dim); font-family: var(--mono); font-size: 12px; opacity: 0; pointer-events: none; transition: opacity .35s; }
.col.unseen .veil { opacity: 1; }
#labels { position: absolute; left: 0; top: 22px; bottom: 0; width: 150px; pointer-events: none; background: linear-gradient(90deg, rgba(0,0,0,.72), rgba(0,0,0,0)); }
#labels div { position: absolute; left: 10px; white-space: nowrap; font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: rgba(255,255,255,.82); text-shadow: 0 1px 3px #000; }
#labels div span { text-transform: none; letter-spacing: 0; color: var(--cyan); margin-left: 6px; }
#labels .hz { left: 10px; font-family: var(--mono); font-size: 10px; letter-spacing: 0; text-transform: none; color: rgba(255,255,255,.5); transform: translateY(-50%); }
#head { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--text); box-shadow: 0 0 0 1px rgba(0,0,0,.6), 0 0 12px rgba(255,255,255,.35); pointer-events: none; }
#head i { position: absolute; bottom: 0; left: 50%; transform: translateX(-50%); background: var(--text); color: var(--bg); font: 500 11px/20px var(--mono); padding: 0 6px; border-radius: 4px 4px 0 0; font-style: normal; font-variant-numeric: tabular-nums; }

#note { padding: 12px 28px 26px; max-width: 1500px; margin: 0 auto; }
#note .meta { font-family: var(--mono); font-size: 11.5px; color: var(--dim); display: flex; gap: 18px; flex-wrap: wrap; }
#note .meta b { color: var(--muted); font-weight: 500; }
#note .grid { display: grid; grid-template-columns: minmax(300px, 1.1fr) minmax(0, 2fr); gap: 40px; margin-top: 12px; }
.prior { font-size: 13px; line-height: 1.45; color: var(--muted); border-left: 2px solid var(--line); padding-left: 12px; margin-bottom: 12px; text-wrap: pretty; }
.prior span, .expect span, .cols h3 { display: block; font: 600 10.5px/1.2 var(--sans); letter-spacing: .09em; text-transform: uppercase; color: var(--dim); margin: 0 0 5px; }
.feel { font-family: var(--serif); font-weight: 400; font-size: clamp(19px, 1.5vw, 23px); line-height: 1.32; margin: 0; letter-spacing: -0.005em; text-wrap: pretty; }
.cols { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 26px; }
.cols p { margin: 0; font-size: 14px; line-height: 1.52; color: #d3cdc3; text-wrap: pretty; }
.expect { margin-top: 14px; padding: 10px 14px; background: var(--panel); border-radius: 6px; font-size: 13px; line-height: 1.5; color: #d3cdc3; text-wrap: pretty; }
.expect span { color: var(--warm); }
.expect.none { display: none; }
button.t { font-family: var(--mono); font-size: .9em; color: var(--cyan); border-bottom: 1px dotted rgba(58,216,238,.5); border-radius: 2px; padding: 0 1px; transition: background .2s, color .2s; }
button.t:hover { background: rgba(58,216,238,.16); }
button.t.now { background: var(--cyan); color: var(--bg); border-bottom-color: transparent; }
#note.swap .grid { animation: swap .45s ease-out; }
@keyframes swap { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }

#after { border-top: 1px solid var(--line); background: var(--panel); padding: 44px 28px 80px; }
#after article { max-width: 680px; margin: 0 auto; font-family: var(--serif); font-size: 18px; line-height: 1.62; }
#after .kicker { font: 600 11px/1 var(--sans); letter-spacing: .1em; text-transform: uppercase; color: var(--warm); }
#after h2 { font-weight: 500; font-size: 30px; line-height: 1.2; margin: 10px 0 20px; letter-spacing: -0.01em; }
#after h3 { font: 600 12px/1.2 var(--sans); letter-spacing: .09em; text-transform: uppercase; color: var(--muted); margin: 38px 0 12px; }
#after p { margin: 0 0 16px; text-wrap: pretty; }
#after ul { margin: 0 0 16px; padding-left: 20px; }
#after li { margin-bottom: 7px; }
#after strong { font-weight: 600; color: #fff; }
#after .made { margin-top: 44px; padding-top: 20px; border-top: 1px solid var(--line); font: 13px/1.6 var(--sans); color: var(--muted); }
@media (max-width: 900px) {
  header { flex-direction: column; align-items: flex-start; }
  #note .grid, .cols { grid-template-columns: 1fr; gap: 16px; } #seal span { display: none; }
}
@media (prefers-reduced-motion: reduce) { #note.swap .grid { animation: none; } .col .veil { transition: none; } }
</style>
</head>
<body>
<header>
  <div class="lede">
    <h1 id="h1"></h1>
    <div class="sub" id="sub"></div>
    <p class="how" id="how"></p>
    <p class="cta" id="cta" hidden>Want your own Claude to listen to a song? <a id="ctalink" target="_blank" rel="noopener">Get the tool and the Claude Code skill</a></p>
  </div>
  <div id="video" hidden></div>
</header>
<div id="transport">
  <button id="play" aria-label="Play"><svg viewBox="0 0 20 20"><path id="playicon" d="M5 3l12 7-12 7z"/></svg></button>
  <div id="clock"><b>0:00</b> / 0:00</div>
  <div id="map" title="Click to jump"><div class="img"></div><div class="unheard"></div></div>
  <label id="seal" title="Each window stayed hidden until the note for the one before it was locked"><input type="checkbox" checked> <span>hide what I hadn't seen yet</span></label>
</div>
<div id="score"><div id="strip"></div><div id="labels"></div><div id="head"><i>0:00</i></div></div>
<section id="note" aria-live="off">
  <div class="meta" id="meta"></div>
  <div class="grid">
    <div>
      <div class="prior"><span>Going in, I'd guessed</span><div id="prior"></div></div>
      <h2 class="feel" id="feel"></h2>
      <div class="expect" id="expectbox"><span>Then, before the next window opened, I guessed</span><div id="expect"></div></div>
    </div>
    <div class="cols">
      <div><h3>The voice</h3><p id="voice"></p></div>
      <div><h3>The band</h3><p id="band"></p></div>
      <div><h3>The words</h3><p id="words"></p></div>
    </div>
  </div>
</section>
<section id="after"><article><div class="kicker">Written after the last window</div><div id="afterbody"></div><div class="made" id="made"></div></article></section>

<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const fmt = t => Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0');

$('h1').innerHTML = D.title + ' <em>listened to in order</em>';
$('sub').textContent = D.artist + ' · ' + D.nwin + ' windows of ' + D.win + ' s · ' + D.by + ' · ' + D.date;
$('how').textContent = "I can't take in sound. A tool measured this recording into pictures, " + D.win +
  " seconds at a time; I read one window, wrote a note, and the note was locked before the next window opened. " +
  (D.took ? "The whole listen took " + D.took + ". " : "") + (D.note ? D.note + " " : "") +
  (D.yt ? "Press play: the music comes from YouTube, and the pictures pass under the line with what I wrote beneath them."
        : "Press play: the pictures pass under the line with what I wrote beneath them.");
$('afterbody').innerHTML = D.after;
if (D.link) { $('ctalink').href = D.link; $('cta').hidden = false; }
$('made').textContent = "Every panel is measured from the audio; no other model described the song to me. The lyric lane and lyric lines are " +
  "cropped out of the pictures here, and the notes paraphrase. Notes are the sealed files, unedited: first window opened " + D.start +
  ", last note sealed " + D.end + ". Space plays and pauses; the arrow keys step a window; any time in the text jumps there.";
if (D.link) { const a = document.createElement('a'); a.href = D.link; a.target = '_blank'; a.rel = 'noopener';
  a.textContent = 'The tool is free to use: have your own Claude listen to a song.'; $('made').append(' ', a); }

// ---- the strip: one column per window, one tile per track, each a crop of that window's picture
const strip = $('strip'), score = $('score'), labels = $('labels');
const cols = D.notes.map(n => {
  const c = document.createElement('div'); c.className = 'col';
  const h = document.createElement('div'); h.className = 'head';
  h.textContent = 'window ' + String(n.k).padStart(3, '0') + '  ' + n.span; c.appendChild(h);
  c.tile = document.createElement('div'); c.tile.className = 'tile';
  c.tile.style.backgroundImage = 'url("' + D.img + '/' + String(n.k).padStart(3, '0') + '.jpg")'; c.appendChild(c.tile);
  const v = document.createElement('div'); v.className = 'veil'; v.textContent = 'not opened yet'; c.appendChild(v);
  c.title = n.feelPlain; strip.appendChild(c); return c;
});
const trackLabels = D.tracks.map(tr => {
  const d = document.createElement('div'); d.textContent = tr.label;
  if (tr.hint) { const s = document.createElement('span'); s.textContent = tr.hint; d.appendChild(s); }
  labels.appendChild(d); return d;
});
const hz = D.leadTicks.map(([txt]) => { const d = document.createElement('div'); d.className = 'hz'; d.textContent = txt; labels.appendChild(d); return d; });

let winW = 800, pps = 53, headX = 300, H = 400;
function layout() {
  const vw = score.clientWidth;
  winW = Math.round(Math.max(520, Math.min(1150, vw * 0.62)));
  pps = winW / D.win; headX = Math.round(vw * 0.36);
  H = Math.round(Math.max(250, Math.min(440, innerHeight * (D.yt ? 0.33 : 0.36))));
  cols.forEach((c, i) => { c.style.width = Math.round((D.notes[i].t1 - D.notes[i].t0) * pps) + 'px';
    c.tile.style.width = winW + 'px'; c.tile.style.height = H + 'px'; });
  D.tracks.forEach((tr, i) => { trackLabels[i].style.top = (tr.top * H + 5) + 'px'; });
  D.leadTicks.forEach(([, f], j) => { hz[j].style.top = (f * H) + 'px'; });
  score.style.height = (H + 22) + 'px'; $('head').style.left = headX + 'px';
  labels.style.width = Math.min(150, headX) + 'px';
  const m = $('map'), mw = m.clientWidth;
  m.querySelector('.img').style.backgroundImage = 'url("' + D.img + '/map.jpg")';
  m.querySelectorAll('.tick').forEach(t => t.remove());
  for (let k = 1; k < D.nwin; k++) { const t = document.createElement('div'); t.className = 'tick'; t.style.left = (k * D.win / D.dur * mw) + 'px'; m.appendChild(t); }
  draw(true);
}

// ---- everything on screen is a function of the audio clock
let shown = -1, times = [];
function showNote(k) {
  const n = D.notes[k]; shown = k;
  $('meta').innerHTML = '<span><b>window ' + String(k).padStart(3, '0') + ' of ' + String(D.nwin - 1).padStart(3, '0') + '</b> · ' + n.span + '</span>' +
    (n.opened ? '<span>opened ' + n.opened + '</span>' : '') +
    (n.sealed ? '<span>note sealed ' + n.sealed + (k < D.nwin - 1 ? ', before window ' + String(k + 1).padStart(3, '0') + ' opened' : '') + '</span>' : '') +
    (n.sha ? '<span>sha256 ' + n.sha + '…</span>' : '');
  $('prior').innerHTML = k === 0 ? 'Nothing yet. This is the first window.' : (D.notes[k - 1].expect || 'No guess written.');
  for (const f of ['feel', 'voice', 'band', 'words', 'expect']) $(f).innerHTML = n[f];
  $('expectbox').classList.toggle('none', !n.expect);
  times = [...$('note').querySelectorAll('button.t')];
  const el = $('note'); el.classList.remove('swap'); void el.offsetWidth; el.classList.add('swap');
}
function draw(force) {
  const t = Math.min(window.__t ?? clock.now(), D.dur), k =   // window.__t: a frame renderer's clock
   Math.min(Math.floor(t / D.win), D.nwin - 1);
  strip.style.transform = 'translate3d(' + (headX - t * pps) + 'px,0,0)';
  $('head').firstElementChild.textContent = fmt(t);
  $('clock').innerHTML = '<b>' + fmt(t) + '</b> / ' + fmt(D.dur);
  $('map').querySelector('.unheard').style.left = (t / D.dur * 100) + '%';
  if (k !== shown || force) {
    const hide = $('seal').querySelector('input').checked;
    cols.forEach((c, i) => { c.classList.toggle('unseen', hide && i > k); c.classList.toggle('now', i === k); });
    if (k !== shown) showNote(k);
  }
  for (const b of times) b.classList.toggle('now', Math.abs(t - (+b.dataset.t + 0.5)) < 1.6);
}
function loop() { draw(); if (!clock.paused()) requestAnimationFrame(loop); }
function seek(t) { clock.seek(Math.max(0, Math.min(D.dur - 0.05, t))); draw(); }
function toggle() { clock.paused() ? clock.play() : clock.pause(); }
function onPlay() { $('playicon').setAttribute('d', 'M4 3h4v14H4zM12 3h4v14h-4z'); $('play').setAttribute('aria-label', 'Pause'); loop(); }
function onPause() { $('playicon').setAttribute('d', 'M5 3l12 7-12 7z'); $('play').setAttribute('aria-label', 'Play'); draw(); }
const startAt = parseFloat(new URLSearchParams(location.search).get('t')) || 0;

// ---- the clock: a local audio file, or an embedded YouTube player followed over postMessage
function audioClock() {
  const a = new Audio(D.audio); a.preload = 'auto';
  a.addEventListener('play', onPlay); a.addEventListener('pause', onPause);
  a.addEventListener('seeked', () => draw()); a.addEventListener('timeupdate', () => { if (a.paused) draw(); });
  if (startAt > 0) a.addEventListener('loadedmetadata', () => seek(startAt), { once: true });
  return { now: () => a.currentTime || 0, paused: () => a.paused, play: () => a.play(), pause: () => a.pause(), seek: t => { a.currentTime = t; } };
}
function youtubeClock() {
  const YT = 'https://www.youtube.com', box = $('video'), f = document.createElement('iframe');
  let base = startAt, stamp = performance.now(), state = -1, rate = 1, ready = false;
  const now = () => state === 1 ? base + (performance.now() - stamp) / 1000 * rate : base;
  const post = o => f.contentWindow && f.contentWindow.postMessage(JSON.stringify(Object.assign({ id: 'yt', channel: 'widget' }, o)), YT);
  const cmd = (func, args) => post({ event: 'command', func, args: args || [] });
  f.id = 'yt'; f.title = 'The recording, on YouTube'; f.allow = 'autoplay; encrypted-media; picture-in-picture'; f.allowFullscreen = true; f.referrerPolicy = 'strict-origin-when-cross-origin';
  f.src = YT + '/embed/' + D.yt + '?enablejsapi=1&playsinline=1&rel=0' + (startAt > 0 ? '&start=' + Math.floor(startAt) : '') +
    '&origin=' + encodeURIComponent(location.origin);
  f.addEventListener('load', () => { const iv = setInterval(() => { if (ready) clearInterval(iv); else post({ event: 'listening' }); }, 250); });
  addEventListener('message', e => {
    if (e.origin !== YT || e.source !== f.contentWindow) return;
    let d; try { d = JSON.parse(e.data); } catch (_) { return; }
    if (!d || !d.info) { if (d && d.event === 'onReady') ready = true; return; }
    ready = true;
    const i = d.info, was = state, cur = now();
    if (typeof i.playbackRate === 'number') rate = i.playbackRate;
    if (typeof i.playerState === 'number') state = i.playerState;
    if (typeof i.currentTime === 'number') {
      // while playing, trust our own interpolation unless the player has really moved (a seek, a stall)
      const diff = i.currentTime - cur;
      base = (state === 1 && was === 1 && Math.abs(diff) < 0.3) ? cur + diff * 0.15 : i.currentTime;
    } else base = cur;
    stamp = performance.now();
    if (state === 1 && was !== 1) onPlay(); else if (state !== 1 && was === 1) onPause(); else draw();
  });
  box.hidden = false; box.appendChild(f);
  return { now, paused: () => state !== 1, play: () => cmd('playVideo'), pause: () => cmd('pauseVideo'),
    seek: t => { base = t; stamp = performance.now(); cmd('seekTo', [t, true]); } };
}
const clock = D.yt ? youtubeClock() : audioClock();
$('play').addEventListener('click', e => { toggle(); if (e.detail) $('play').blur(); });
$('map').addEventListener('click', e => { const r = $('map').getBoundingClientRect(); seek((e.clientX - r.left) / r.width * D.dur); });
score.addEventListener('click', e => { const r = score.getBoundingClientRect(); seek(clock.now() + (e.clientX - r.left - headX) / pps); });
$('seal').querySelector('input').addEventListener('change', () => draw(true));
document.addEventListener('click', e => { const b = e.target.closest('button.t'); if (b) { seek(+b.dataset.t); if (e.detail) b.blur(); if (b.closest('#after')) scrollTo({ top: 0, behavior: 'smooth' }); } });
document.addEventListener('keydown', e => {
  if (e.target.matches('input') || (e.code === 'Space' && e.target.closest('button'))) return;
  const k = Math.floor(clock.now() / D.win);
  if (e.code === 'Space') { e.preventDefault(); toggle(); }
  else if (e.code === 'ArrowRight') { e.preventDefault(); seek((k + 1) * D.win); }
  else if (e.code === 'ArrowLeft') { e.preventDefault(); seek((clock.now() - k * D.win > 1.5 ? k : k - 1) * D.win); }
});
addEventListener('resize', layout);
layout();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="a finished run folder, e.g. runs/2026-11-06-somebody-live-v4")
    ap.add_argument("--out", help="output folder (default RUN/score, or RUN/share with --youtube)")
    ap.add_argument("--link", default="", help="a URL for a 'get the tool' link on the page")
    ap.add_argument("--youtube", default="", help="YouTube video id: embed it as the sound source and write no audio")
    ap.add_argument("--audio", help="audio file (default: song.wav in the run's cache)")
    ap.add_argument("--by", default="Claude", help="who listened, for the byline")
    ap.add_argument("--note", default="", help="a sentence for the intro, e.g. what the listener already knew of the song")
    a = ap.parse_args()
    run = os.path.abspath(a.run)
    print(build(run, os.path.abspath(a.out) if a.out else os.path.join(run, "share" if a.youtube else "score"),
                a.audio, a.by, a.note, a.youtube, a.link))


if __name__ == "__main__":
    main()
