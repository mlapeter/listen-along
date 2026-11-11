#!/usr/bin/env python3
"""score.py: turn a finished sealed listen into a page that plays the notes against the music.

    ./venv3/bin/python v3/score.py runs/<run>            # writes runs/<run>/score/index.html (+ song.m4a)
    open runs/<run>/score/index.html

It reads only what the listen left behind: state.json, notes/NNN.md, notes/after.md, seal.log, the revealed
pictures in windows/ and the close-ups in looks/. Nothing is retyped: the notes on the page are the sealed files.
The audio is the same song.wav the pictures were measured from (transcoded to AAC), so time zero matches.

The page crops each picture to its measured panels (no lyric lane, no lyric lines), tiles the 15 s windows
side by side and scrolls them under a fixed playhead driven by audio.currentTime. A run with the harmony picture
gets a notes track under the instrument tracks; a run with close-ups gets a bracket over each one's span and a
lens that opens under the strip while the playhead is inside it (the close-up's lead panel and piano roll, and its
timbre panel if it was made with --timbre, beside what was asked and what was seen).

    ./venv3/bin/python v3/score.py runs/<run> --second runs/<second run>

With --second (a `see.py again` listen of the same song) the page holds both listens behind one switch: the
first as above, the second with the notes track, its own notes (feel, harmony, changed), its close-ups and its
after-note.

    ./venv3/bin/python v3/score.py runs/<run> --second runs/<second run> --omit-second-after --redact NAME=REPLACEMENT

--omit-second-after leaves the second listen's after-note off the page. --redact (repeatable) replaces a whole-word
name in every displayed note text at build time. The sealed files are only read, and the footer says what was done.

    ./venv3/bin/python v3/score.py runs/<run> --pictures DIR

--pictures takes the voice and band window pictures (NNN-voice.png, NNN-band.png, with their geometry.json)
from DIR instead of the run's own windows/, e.g. a cache re-rendered after a fix in the tool. The page says so.

Panel boxes come from the tool's own files: windows/geometry.json (in the cache, or in DIR) for window pictures
and looks/LL.json for close-ups. Without them the two-picture layout's hardcoded rows are used. Every crop is
made by panel id and checked against the lyric-bearing panels of its picture.

The output holds the recording, so it stays in the run folder (gitignored). Don't publish it.

    ./venv3/bin/python v3/score.py runs/<run> --youtube VIDEO_ID     # writes runs/<run>/share/

With --youtube the page holds no audio: it embeds that YouTube video and follows the player's clock. Use the
video the audio was fetched from, so time zero matches. The share folder is self-contained (index.html + the
cropped strips in w/, n/ and c/) and has no lyric text in its images; it needs to be served over http(s), since
YouTube refuses to embed in a page opened as a file.

The footer's sentence about the lyric is measured at build time against the cached lyric (lyrics.json / lyrics.lrc):
the longest run of the lyric's words anywhere in the displayed text, and how many quoted phrases hold three or more
of them, the song's title left out. The numbers are also printed to stderr.
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------------------------- geometry
# Fallback for runs whose cache (and with it geometry.json) is gone: the two-picture layout as see.py rendered
# it at DPI 100 (1568 x 1150 voice, 1568 x 1050 band). x: the shared 15 s time axis. y: each panel's rows with
# the caption strip at its top already left out (cap 0). The lyric-bearing panels are listed so the crop check
# has something to check against.
def _fb(pid, y0, y1, **kw):
    return dict(id=pid, x0=118, x1=1545, y0=y0, y1=y1, **kw)


FALLBACK = {
    "voice": dict(w=1568, h=1150, panels=[_fb("lead", 72, 437, cap=0), _fb("backing", 466, 545, cap=0),
                                          _fb("words", 556.55, 710.0), _fb("held", 764.0, 952.0)]),
    "band": dict(w=1568, h=1050, panels=[_fb("drums", 62, 157, cap=0), _fb("bass", 163, 274, cap=0),
                                         _fb("guitar", 280, 390, cap=0), _fb("piano", 397, 507, cap=0),
                                         _fb("other", 514, 625, cap=0), _fb("sofar", 862, 943, cap=0)]),
}
# Pixels of in-panel caption (the tool's own legend, drawn over the top rows) left out of a crop, per panel id.
CAPTION = {"lead": 20, "backing": 19, "drums": 16, "notes": 31, "bassnotes": 16}
# A panel whose id matches holds lyric text (word lanes, the held-note block with its word titles). No crop may
# touch one, and since the lyric lines sit under them without a box of their own, no crop may reach below the
# first one either.
LYRIC_ID = re.compile(r"word|lyric|held|text", re.I)

INSTRUMENTS = [
    # panel id, label, hint, picture, share of the instrument tile's height
    ("lead", "lead voice", "cyan line = pitch", "voice", 0.40),
    ("backing", "other voices", "", "voice", 0.08),
    ("drums", "drums", "", "band", 0.09),
    ("bass", "bass", "", "band", 0.09),
    ("guitar", "guitar", "", "band", 0.09),
    ("piano", "keys", "", "band", 0.09),
    ("other", "other", "", "band", 0.09),
]
NOTES = [
    ("notes", "notes of the band", "green line = bass, cyan = the voice", "harmony", 0.75),
    ("bassnotes", "bass alone", "", "harmony", 0.25),
]
LOOK = [
    # the lens: the lead panel and the piano roll (their shares add up to 1), and under them the timbre panel of a
    # look made with --timbre. The band stems and the bass panel are on the strip above.
    ("lead", "lead voice", "cyan line = pitch", "voice", 0.40),
    ("notes", "notes of the band", "green line = bass", "harmony", 0.60),
    ("timbre", "the voice's harmonics", "narrow filters", "timbre", 0.45),
]
# Lead panel frequency ticks as fractions of the cropped lead panel (read off the rendered axis: Hz -> y pixel
# in a panel cropped to rows 72..437).
LEAD_TICKS = [(txt, (py - 72) / (437 - 72)) for txt, py in
              [("200", 386), ("400", 343), ("800", 288), ("1.6k", 223), ("3.2k", 153)]]
NOTE_TICKS = {"notes": ("C3", "C4", "C5", "C6"), "bassnotes": ("C2", "C3")}
TIMBRE_TICKS = {"400": "400", "800": "800", "1600": "1.6k"}     # labelled rows of a timbre panel (`hz` in LL.json)

STRIP_H = 640          # pixel height of the instrument tile written per window (the page scales it down)
NOTES_H = 440          # ... of the notes tile
LOOK_H = 560           # ... of a close-up's tile, per unit of share (its lead panel and piano roll)


def load_geometry(folder):
    p = os.path.join(folder, "geometry.json") if folder else ""
    if not p or not os.path.exists(p):
        return {}
    g = json.load(open(p, encoding="utf-8"))
    return g.get("pictures", g)


def geometry_for(path, name, candidates):
    """The geometry of picture `path` (a voice, band or harmony PNG): the first candidate whose size is the
    picture's own, else the hardcoded two-picture rows if they fit, else stop."""
    from PIL import Image
    size = Image.open(path).size
    for g in candidates:
        pic = g.get(name)
        if pic and (pic.get("w"), pic.get("h")) == size:
            return pic
    fb = FALLBACK.get(name)
    if fb and (fb["w"], fb["h"]) == size:
        return fb
    sys.exit(f"no panel geometry for {path} ({size[0]} x {size[1]}): its geometry.json is missing or describes "
             f"another size, and the hardcoded rows are for the two-picture layout only")


def crop_box(pic, pid):
    """The pixel box of panel `pid` in a picture, its caption rows left out. Refuses a lyric-bearing panel, any
    overlap with one, and anything at or below the first one (the lyric lines have no box of their own)."""
    p = next((q for q in pic["panels"] if q["id"] == pid), None)
    if p is None:
        sys.exit(f"the picture has no panel '{pid}' (it has: {', '.join(q['id'] for q in pic['panels'])})")
    assert not LYRIC_ID.search(pid), f"panel '{pid}' holds lyric text and is never cropped"
    box = (p["x0"], p["y0"] + p.get("cap", CAPTION.get(pid, 0)), p["x1"], p["y1"])
    assert box[2] > box[0] and box[3] > box[1], f"empty crop for panel '{pid}'"
    lyric = [q for q in pic["panels"] if LYRIC_ID.search(q["id"])]
    for q in lyric:
        apart = box[3] <= q["y0"] or box[1] >= q["y1"] or box[2] <= q["x0"] or box[0] >= q["x1"]
        assert apart, f"the crop of panel '{pid}' overlaps the lyric panel '{q['id']}'"
    if lyric:
        assert box[3] <= min(q["y0"] for q in lyric), f"the crop of panel '{pid}' reaches below a lyric panel"
    return box


def stack(spec, pics, geoms, height):
    """One image: the panels of `spec` cropped by id from their pictures and stacked. Returns the image, each
    track's place in it (fractions of its height) and the crop boxes."""
    from PIL import Image
    boxes = {t[0]: crop_box(geoms[t[3]], t[0]) for t in spec}
    for name, im in pics.items():
        g = geoms[name]
        assert im.size == (g["w"], g["h"]), f"a {name} picture is {im.size}, its geometry says {g['w']} x {g['h']}"
    wd = round(max(b[2] - b[0] for b in boxes.values()))
    total = sum(t[4] for t in spec)
    hs = [round(height * t[4] / total) for t in spec]
    out, y, tracks = Image.new("RGB", (wd, sum(hs))), 0, []
    for t, h in zip(spec, hs):
        out.paste(pics[t[3]].resize((wd, h - 1), Image.LANCZOS, box=boxes[t[0]]), (0, y))
        tracks.append(dict(id=t[0], label=t[1], hint=t[2], top=y / sum(hs), h=h / sum(hs)))
        y += h
    return out, tracks, boxes


def ticks_for(spec, tracks, geoms, boxes):
    """Axis labels for the page's left overlay, as (text, fraction of the tile's height): Hz on the lead panel,
    and note names on the semitone panels, taken from the geometry's labelled rows."""
    out = []
    for t, tr in zip(spec, tracks):
        pid, box = t[0], boxes[t[0]]
        if pid == "lead":
            out += [(txt, tr["top"] + f * tr["h"], "hz") for txt, f in LEAD_TICKS]
        rows = next(q for q in geoms[t[3]]["panels"] if q["id"] == pid).get("notes") or {}
        for name in NOTE_TICKS.get(pid, ()):
            if name in rows:
                f = (rows[name] - box[1]) / (box[3] - box[1])
                if 0.03 < f < 0.96:
                    out.append((name, tr["top"] + f * tr["h"], "nn"))
        hz = next(q for q in geoms[t[3]]["panels"] if q["id"] == pid).get("hz") or {}
        for name, txt in TIMBRE_TICKS.items() if pid == "timbre" else ():
            if name in hz:
                out.append((txt, tr["top"] + (hz[name] - box[1]) / (box[3] - box[1]) * tr["h"], "nn"))
    return out


def composite_windows(out, sub, nwin, spec, src, geoms):
    """One JPEG per window in out/sub: the spec's panels stacked, nothing else from the pictures."""
    from PIL import Image
    os.makedirs(os.path.join(out, sub), exist_ok=True)
    names = sorted({t[3] for t in spec})
    tracks = boxes = None
    for k in range(nwin):
        pics = {n: Image.open(src(k, n)).convert("RGB") for n in names}
        strip, tracks, boxes = stack(spec, pics, geoms, STRIP_H if sub == "w" else NOTES_H)
        strip.save(os.path.join(out, sub, f"{k:03d}.jpg"), quality=86, optimize=True)
        if sub == "w" and k == nwin - 1:
            # the "song so far" strip of the last window's band picture: the whole song on one axis
            pics["band"].crop(tuple(round(v) for v in crop_box(geoms["band"], "sofar"))).save(
                os.path.join(out, sub, "map.jpg"), quality=88)
    return dict(dir=sub, tracks=tracks, ticks=ticks_for(spec, tracks, geoms, boxes))


# ------------------------------------------------------------------------------------------------- text
ALL_LABELS = ("feel", "voice", "band", "words", "harmony", "changed", "expect")


def parse_note(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]?\s*\**(" + "|".join(ALL_LABELS) + r"|saw)\**\s*:\**\s*(.*)$", line, re.I)
        if m:
            cur = m.group(1).lower()
            out[cur] = m.group(2).strip()
        elif cur and line.strip():
            out[cur] += " " + line.strip()
    return out


def parse_seal_log(path):
    """Window opening and note sealing times, and per close-up: when and why it was asked for (logged before
    anything was drawn), the last window open at that moment, and its note's seal."""
    opened, sealed, looks, cur = {}, {}, {}, 0
    if not os.path.exists(path):
        return opened, sealed, looks
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^(\S+) (\S+)\s+window (\d+) revealed", line)
        if m:
            cur = int(m.group(3))
            opened[cur] = (m.group(1), m.group(2))
        m = re.match(r"^(\S+) (\S+)\s+note (\d+) sealed\s+sha256 (\w+)", line)
        if m:
            sealed[int(m.group(3))] = (m.group(1), m.group(2), m.group(4)[:10])
        m = re.match(r"^(\S+) (\S+)\s+look (\d+)\s+\S+\s+why: (.*)$", line)
        if m:
            looks.setdefault(int(m.group(3)), {}).update(asked=m.group(2), why=m.group(4).strip(), win=cur)
        m = re.match(r"^(\S+) (\S+)\s+look (\d+) saw sealed\s+sha256 (\w+)", line)
        if m:
            looks.setdefault(int(m.group(3)), {}).update(sealed=m.group(2), sha=m.group(4)[:10])
    return opened, sealed, looks


TIME = re.compile(r"(?<![\d:])(\d):([0-5]\d)(\.\d{1,3})?(?![\d:]|\.\d)")


def inline(text, looks=()):
    """Escape, then **bold**, m:ss and m:ss.ss timestamps and "close-up NN" (made clickable by the page)."""
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

    def stamp(m):
        t = int(m.group(1)) * 60 + int(m.group(2)) + float(m.group(3) or 0)
        # a whole second names that second (its middle is the mark); a decimal time names an instant
        return f'<button class="t" data-t="{t:g}" data-c="{t + (0 if m.group(3) else 0.5):g}">{m.group(0)}</button>'

    s = TIME.sub(stamp, s)
    if looks:
        s = re.sub(r"\bclose-up (\d\d)\b", lambda m: f'<button class="t ref" data-look="{int(m.group(1))}">{m.group(0)}</button>'
                   if int(m.group(1)) in looks else m.group(0), s)
    return s


def markdown(text, looks=()):
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para), looks) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i, looks)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        if not line.strip():
            flush()
        elif line.startswith("## "):
            flush(); out.append(f"<h3>{inline(line[3:], looks)}</h3>")
        elif line.startswith("# "):
            flush(); out.append(f"<h2>{inline(line[2:], looks)}</h2>")
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


def read(path):
    return open(path, encoding="utf-8").read()


# ------------------------------------------------------------------------------------- the lyric, measured
def words_of(s):
    s = s.lower().replace("’", "'").replace("‘", "'")
    return [w.strip("'") for w in re.findall(r"[a-z0-9']+", s) if w.strip("'")]


def lyric_grams(work, maxn=24):
    """Every run of 1..maxn consecutive words of the cached lyric (lyrics.json / lyrics.lrc), as a set."""
    seqs = []
    p = os.path.join(work, "lyrics.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        for key in ("plainLyrics", "syncedLyrics"):
            if isinstance(d, dict) and d.get(key):
                seqs.append(words_of(re.sub(r"\[[\d:.]+\]", " ", d[key])))
    p = os.path.join(work, "lyrics.lrc")
    if os.path.exists(p):
        seqs.append(words_of(re.sub(r"\[[\d:.]+\]", " ", read(p))))
    grams = set()
    for seq in seqs:
        for n in range(1, maxn + 1):
            grams.update(tuple(seq[i:i + n]) for i in range(len(seq) - n + 1))
    return grams


def longest_run(tokens, grams):
    best = []
    for i in range(len(tokens)):
        n = len(best)                      # only a longer run is news (every part of a run is itself a run)
        while i + n < len(tokens) and tuple(tokens[i:i + n + 1]) in grams:
            n += 1
        if n > len(best):
            best = tokens[i:i + n]
    return best


def lyric_stats(grams, texts, title=""):
    """How much of the lyric the texts hold word for word, with the song's title taken out first (a title that
    is a lyric line is on every page): the longest run anywhere, and inside quotation marks the longest run and
    how many quoted phrases hold a run of three or more of the lyric's words. `with_title` is the longest run
    with the title left in."""
    if not grams:
        return None
    joined = "\n".join(texts)
    untitled = re.sub(re.escape(title), " | ", joined, flags=re.I) if title else joined
    anywhere = max((longest_run(words_of(part), grams) for part in untitled.split("|")), key=len, default=[])
    quoted = [m.group(1) or m.group(2) for m in re.finditer(r'"([^"\n]{1,400})"|\u201c([^\u201d\n]{1,400})\u201d', untitled)]
    runs = [longest_run(words_of(q), grams) for q in quoted]
    longest = max(runs, key=len, default=[])
    return dict(anywhere=len(anywhere), anywhere_words=" ".join(anywhere), quoted=len(longest),
                quoted_words=" ".join(longest), phrases3=sum(1 for r in runs if len(r) >= 3),
                with_title=len(longest_run(words_of(joined), grams)))


NUMERAL = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def numeral(n):
    return NUMERAL[n] if n < len(NUMERAL) else str(n)


def lyric_claim(s):
    """What the footer may say about a listen's text, from the measurement."""
    if s is None:                           # no cached lyric to measure against: claim nothing
        return ""
    n, top = s["phrases3"], max(s["anywhere"], s["quoted"])
    if n > 2:
        return (f"quote short phrases of the lyric ({numeral(n)} of three words or more, none longer than "
                f"{numeral(top)} words, the title aside)")
    out = "paraphrase the words" if n == 0 else \
        f"mostly paraphrase the words, quoting {numeral(n)} phrase{'s' if n > 1 else ''} of three words or more"
    if top >= 3:
        out += f" (the title aside, the longest run of the lyric in them is {numeral(top)} words)"
    return out


# --------------------------------------------------------------------------------------------- one listen
def took(a, b):
    if not (a and b):
        return ""
    x, y = (sum(int(v) * m for v, m in zip(s.split(":"), (3600, 60, 1))) for s in (a, b))
    return f"{(y - x) // 60} min {(y - x) % 60} s" if y > x else ""


def read_listen(run, st, first_run=None, fix=lambda s: s, with_after=True):
    """The words of one listen: its window notes, close-ups (without their pictures yet), after-note and seals.
    `fix` is applied to every text that will be displayed (--redact); the files themselves are only read."""
    second = st.get("layout") == "second"
    nwin, win, dur = st["nwin"], float(st["window"]), float(st["duration"])
    if st.get("revealed", 0) < nwin or not os.path.exists(os.path.join(run, "notes", "after.md")):
        sys.exit(f"{run}: the listen isn't finished: every window revealed and notes/after.md sealed first")
    opened, sealed, logged = parse_seal_log(os.path.join(run, "seal.log"))

    looks, texts = [], []
    ld = os.path.join(run, "looks")
    for f in sorted(os.listdir(ld)) if os.path.isdir(ld) else []:
        m = re.match(r"^(\d+)\.json$", f)
        md = os.path.join(ld, f[:-5] + ".md")
        if not m or not os.path.exists(md):
            continue
        n, j = int(m.group(1)), json.load(open(os.path.join(ld, f), encoding="utf-8"))
        lg = logged.get(n, {})
        why, saw = fix(j.get("why") or lg.get("why", "")), parse_note(fix(read(md))).get("saw", "")
        t0, t1 = float(j["start"]), float(j["end"])
        looks.append(dict(n=n, t0=t0, t1=t1, win=min(lg.get("win", int((t1 - 1e-6) // win)), nwin - 1),
                          zoom=round(win / (t1 - t0), 1), why=why, saw=saw, asked=lg.get("asked", ""),
                          sealed=lg.get("sealed", ""), sha=lg.get("sha", ""), json=j))
        texts += [why, saw]
    nums = {lk["n"] for lk in looks}
    for lk in looks:
        lk["why"], lk["saw"] = inline(lk["why"], nums), inline(lk["saw"], nums)

    raw = [parse_note(fix(read(os.path.join(run, "notes", f"{k:03d}.md")))) for k in range(nwin)]
    labels = ["harmony", "changed"] if second else ["voice", "band", "words"] + (["harmony"] if any("harmony" in n for n in raw) else [])
    heads = dict(voice="The voice", band="The band", words="The words", harmony="The notes", changed="What changed")
    wins = []
    for k, n in enumerate(raw):
        first = ""
        if second:
            src = os.path.join(run, "windows", f"{k:03d}-first.md")     # the copy the listener had beside the picture
            if not os.path.exists(src) and first_run:
                src = os.path.join(first_run, "notes", f"{k:03d}.md")
            first = parse_note(fix(read(src))).get("feel", "") if os.path.exists(src) else ""
        texts += [n.get(lab, "") for lab in ["feel", "expect"] + labels] + [first]
        wins.append({
            "k": k, "t0": k * win, "t1": min((k + 1) * win, dur),
            "span": f"{mmss(k * win)}-{mmss(min((k + 1) * win, dur))}",
            "feel": inline(n.get("feel", ""), nums), "feelPlain": n.get("feel", ""),
            "expect": inline(n.get("expect", ""), nums), "first": inline(first),
            "cols": [inline(n.get(lab, ""), nums) for lab in labels],
            "opened": opened.get(k, ("", ""))[1], "sealed": sealed.get(k, ("", "", ""))[1],
            "sha": sealed.get(k, ("", "", ""))[2],
        })
    after = fix(read(os.path.join(run, "notes", "after.md"))) if with_after else ""
    texts.append(after)
    a, b = opened.get(0, ("", "")), sealed.get(nwin - 1, ("", "", ""))
    return dict(kind="second" if second else "first", heads=[heads[lab] for lab in labels], wins=wins, looks=looks,
                after=markdown(after, nums), date=a[0], start=a[1], end=b[1], took=took(a[1], b[1]),
                full=bool(st.get("full")), texts=texts)


def composite_looks(run, out, looks):
    """One JPEG per close-up in out/c: its lead panel, its piano roll and (a look made with --timbre) its timbre
    panel, cropped by the boxes in LL.json."""
    from PIL import Image
    if looks:
        os.makedirs(os.path.join(out, "c"), exist_ok=True)
    for lk in looks:
        geoms = lk.pop("json")["pictures"]
        spec = [t for t in LOOK if t[3] in geoms and any(q["id"] == t[0] for q in geoms[t[3]]["panels"])
                and os.path.exists(os.path.join(run, "looks", f"{lk['n']:02d}-{t[3]}.png"))]
        if not spec:
            lk["img"] = ""
            continue
        for name in {t[3] for t in spec}:
            assert all(p.get("time") for p in geoms[name]["panels"] if p["id"] in [t[0] for t in spec]), "a look panel off the time axis"
        pics = {n: Image.open(os.path.join(run, "looks", f"{lk['n']:02d}-{n}.png")).convert("RGB") for n in {t[3] for t in spec}}
        lk["tall"] = round(sum(t[4] for t in spec), 3)
        im, tracks, boxes = stack(spec, pics, geoms, round(LOOK_H * lk["tall"]))
        lk["img"] = f"c/{lk['n']:02d}.jpg"
        im.save(os.path.join(out, lk["img"]), quality=86, optimize=True)
        lk["tracks"], lk["ticks"] = tracks, ticks_for(spec, tracks, geoms, boxes)


def build(run, out, audio, by, note="", youtube="", link="", second="", pictures="", redrawn="", redact=(),
          omit_second_after=False):
    st = json.load(open(os.path.join(run, "state.json"), encoding="utf-8"))
    if st.get("layout") not in ("voice+band", "voice+band+harmony"):
        sys.exit("score.py needs a first-listen run with the voice and band pictures (a second listen goes in --second)")
    nwin, win, dur = st["nwin"], float(st["window"]), float(st["duration"])
    runs, states = [run], [st]
    if second:
        st2 = json.load(open(os.path.join(second, "state.json"), encoding="utf-8"))
        if st2.get("layout") != "second":
            sys.exit("--second needs a second listen (made with `see.py again`)")
        if (st2["nwin"], float(st2["window"])) != (nwin, win) or abs(float(st2["duration"]) - dur) > 0.01:
            sys.exit("--second is a listen of another recording, or with another window length")
        runs.append(second); states.append(st2)
    if omit_second_after and not second:
        sys.exit("--omit-second-after needs --second")
    replaced = {}

    def fix(s):       # --redact NAME=REPLACEMENT: whole words only, in displayed text only
        for name, new in redact:
            s, n = re.subn(r"(?<!\w)" + re.escape(name) + r"(?!\w)", lambda m: new, s)
            replaced[name] = replaced.get(name, 0) + n
        return s

    listens = [read_listen(r, s, run, fix, not (omit_second_after and i == 1)) for i, (r, s) in enumerate(zip(runs, states))]
    os.makedirs(out, exist_ok=True)

    # ---- pictures: instrument tiles from the first listen (or --pictures), notes tiles from whichever run has
    # the harmony picture, close-up tiles from each run's looks; every box from the tool's geometry files
    work = st.get("work", "")
    cache = load_geometry(os.path.join(work, "windows")) if work else {}
    pdir = pictures or os.path.join(run, "windows")
    vb = lambda k, n: os.path.join(pdir, f"{k:03d}-{n}.png")    # noqa: E731
    cands = [load_geometry(pictures), cache] if pictures else [cache]
    geoms = {n: geometry_for(vb(0, n), n, cands) for n in ("voice", "band")}
    inst = composite_windows(out, "w", nwin, INSTRUMENTS, vb, geoms)
    inst["split"] = next(tr["top"] for tr in inst["tracks"] if tr["id"] == "drums")   # where the voice tracks end
    hrun = next((r for r, s in zip(reversed(runs), reversed(states))
                 if os.path.exists(os.path.join(r, "windows", f"{nwin - 1:03d}-harmony.png"))), None)
    notes = None
    if hrun:
        hp = lambda k, n: os.path.join(hrun, "windows", f"{k:03d}-{n}.png")    # noqa: E731
        notes = composite_windows(out, "n", nwin, NOTES, hp, {"harmony": geometry_for(hp(0, "harmony"), "harmony", cands)})
    for r, s, ls in zip(runs, states, listens):
        composite_looks(r, out, ls["looks"])
        ls["looks"] = [lk for lk in ls["looks"] if lk["img"]]
        ls["inst"] = inst
        ls["notes"] = notes if (s["layout"] != "voice+band" and notes) else None

    src = audio or os.path.join(work, "song.wav")
    dst = os.path.join(out, "song.m4a")
    if not youtube and not os.path.exists(dst):
        if not os.path.exists(src):
            sys.exit(f"no audio at {src}; pass --audio")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-vn", "-c:a", "aac", "-b:a", "160k", dst],
                       check=True)

    # ---- the footer says what is true of this page: how much lyric its text holds, what was redrawn
    grams = lyric_grams(work) if work and os.path.isdir(work) else set()
    title = st.get("title", "")
    stats = [lyric_stats(grams, ls.pop("texts") + ([note] if i == 0 else []), title) for i, ls in enumerate(listens)]
    two = len(listens) == 2
    names = ["first listen", "second listen"]
    for ls, name in zip(listens, names):
        ls["name"] = name.capitalize()
        ls["kicker"] = f"Written after the {name}" if two else "Written after the last window"
    made = ["Every panel is measured from the audio; no other model described the song to me."]
    if two:
        made.append("The lyric lane and lyric lines are cropped out of the pictures here, so no image holds any of the words." +
                    (f" The first listen's notes {lyric_claim(stats[0])}; the second listen's notes"
                     f"{' and close-ups' if listens[1]['looks'] else ''} {lyric_claim(stats[1])}." if all(stats) else ""))
        if not listens[1]["full"]:
            made.append("In the second listen each window showed me only the picture of the notes, the timed words and my "
                        "own first note, not the voice and band pictures again" +
                        ("; each close-up came with its own voice, band and notes pictures." if listens[1]["looks"] else "."))
    else:
        made.append("The lyric lane and lyric lines are cropped out of the pictures here" +
                    (f", and the notes {lyric_claim(stats[0])}." if stats[0] else "."))
    if pictures:
        made.append(redrawn or "The voice and band strips were redrawn after a timing fix in the tool and are up to about "
                    "half a second earlier than the pictures I saw in the first listen.")
    hit = [name for name, n in replaced.items() if n]
    if hit:
        made.append(("A name in the notes was" if len(hit) == 1 else "Names in the notes were") +
                    " replaced before publishing; the seals are those of the files as written.")
    if omit_second_after:
        made.append("The note I wrote after the second listen is left off this page.")
    unedited = "otherwise unedited" if hit else "unedited"
    if two:
        made.append(f"Notes are the sealed files, {unedited}: the first listen ran from " + listens[0]["start"] + " to " +
                    listens[0]["end"] + (" on " + listens[0]["date"] if listens[0]["date"] != listens[1]["date"] else "") +
                    ", the second from " + listens[1]["start"] + " to " + listens[1]["end"] +
                    (" on " + listens[1]["date"] if listens[0]["date"] != listens[1]["date"] else " the same day") + ".")
    else:
        made.append(f"Notes are the sealed files, {unedited}: first window opened {listens[0]['start']}, last note sealed "
                    f"{listens[0]['end']}.")
    made.append("Space plays and pauses; the arrow keys step a window; any time in the text jumps there.")

    dates = [ls["date"] for ls in listens]
    data = {
        "artist": st.get("artist", ""), "title": title, "dur": dur, "win": win, "nwin": nwin,
        "audio": "" if youtube else "song.m4a", "yt": youtube, "by": by, "note": note, "link": link,
        "date": dates[0] if len(set(dates)) == 1 else " and ".join(dates), "made": " ".join(made),
        "listens": listens,
    }
    page = PAGE.replace("__TITLE__", html.escape(f"{data['title']}, listened to in order"))
    page = page.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    for bad in {"/Users/", "/home/", os.path.expanduser("~"), work or "/Users/"}:
        assert bad not in page, f"a local path ({bad}) would be on the page"
    path = os.path.join(out, "index.html")
    open(path, "w", encoding="utf-8").write(page)
    for name, _ in redact:
        print(f"redact: '{name}' replaced {replaced.get(name, 0)} time(s) in the displayed text" +
              ("" if replaced.get(name) else " (nothing matched: check the spelling)"), file=sys.stderr)
    for ls, name, s in zip(listens, names, stats):
        if s:
            print(f"lyric check, {name} (title left out): longest verbatim run of the lyric anywhere in its text "
                  f"{s['anywhere']} words; inside quotation marks {s['quoted']} words; quoted phrases holding 3 or more "
                  f"lyric words: {s['phrases3']}; with the title left in the longest run is {s['with_title']} words",
                  file=sys.stderr)
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
header .how.wide { max-width: 1000px; }
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
#tabs { display: flex; flex: none; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
#tabs[hidden] { display: none; }
#tabs button { height: 30px; padding: 0 13px; font-size: 12px; color: var(--muted); white-space: nowrap; transition: color .2s, background .2s; }
#tabs button + button { border-left: 1px solid var(--line); }
#tabs button:hover { color: var(--text); }
#tabs button[aria-selected="true"] { background: #1c1a23; color: var(--text); box-shadow: inset 0 -2px 0 var(--cyan); }
#tabs button:focus-visible { outline-offset: -3px; }
#map { position: relative; flex: 1; height: 44px; border-radius: 4px; overflow: hidden; cursor: pointer; background: #000; }
#map .img { position: absolute; inset: 0; background-repeat: no-repeat; background-size: 100% 100%; }
#map .unheard { position: absolute; top: 0; bottom: 0; right: 0; background: rgba(11,10,14,.6); border-left: 2px solid var(--cyan); }
#map .tick { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(255,255,255,.14); }
#map .mlook { position: absolute; top: 0; height: 4px; min-width: 2px; background: var(--warm); opacity: .9; }
#seal { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); flex: none; user-select: none; cursor: pointer; }
#seal input { accent-color: var(--cyan); margin: 0; }

#score { position: relative; overflow: hidden; background: #000; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); cursor: pointer; }
#strip { position: absolute; left: 0; top: 0; display: flex; will-change: transform; }
.col { position: relative; flex: none; overflow: hidden; border-right: 1px solid rgba(255,255,255,.22); }
.col .head { height: 22px; padding: 0 8px; font-family: var(--mono); font-size: 11px; line-height: 22px; color: var(--muted); background: var(--panel); white-space: nowrap; }
.col.now .head { color: var(--text); }
.tile { background-repeat: no-repeat; background-size: 100% 100%; }
.tile.nt { box-shadow: inset 0 1px 0 rgba(255,255,255,.2); }
.col .veil { position: absolute; left: 0; right: 0; top: 22px; bottom: 0; display: grid; place-items: center; background: repeating-linear-gradient(135deg, #17161c 0 14px, #131218 14px 28px); color: var(--dim); font-family: var(--mono); font-size: 12px; opacity: 0; pointer-events: none; transition: opacity .35s; }
.col.unseen .veil { opacity: 1; }
#looks { position: absolute; left: 0; top: 22px; width: 0; height: 0; }
.lk { position: absolute; top: 0; height: 11px; border: solid var(--warm); border-width: 2px 2px 0; border-radius: 2px 2px 0 0; opacity: .92; transition: opacity .35s; }
.lk b { position: absolute; left: -2px; top: -2px; will-change: transform; padding: 0 6px; font: 500 10px/15px var(--mono); white-space: nowrap; color: var(--warm); background: rgba(11,10,14,.86); border: 1px solid var(--warm); border-radius: 2px 0 3px 0; transition: background .2s, color .2s; }
.lk:hover b, .lk.now b { background: var(--warm); color: var(--bg); }
.lk.unseen { opacity: 0; pointer-events: none; }
.lk.gone b { opacity: 0; }
.lk:focus-visible { outline-offset: 3px; }
.labels { position: absolute; left: 0; top: 0; bottom: 0; width: 150px; pointer-events: none; background: linear-gradient(90deg, rgba(0,0,0,.72), rgba(0,0,0,0)); }
#labels { top: 22px; }
.labels div { position: absolute; left: 10px; white-space: nowrap; font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: rgba(255,255,255,.82); text-shadow: 0 1px 3px #000; }
.labels div span { text-transform: none; letter-spacing: 0; color: var(--cyan); margin-left: 6px; }
.labels .hz, .labels .nn { left: 10px; font-family: var(--mono); font-size: 10px; letter-spacing: 0; text-transform: none; color: rgba(255,255,255,.5); transform: translateY(-50%); }
#head, #lhead { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--text); box-shadow: 0 0 0 1px rgba(0,0,0,.6), 0 0 12px rgba(255,255,255,.35); pointer-events: none; }
#head i, #lhead i { position: absolute; bottom: 0; left: 50%; transform: translateX(-50%); background: var(--text); color: var(--bg); font: 500 11px/20px var(--mono); padding: 0 6px; border-radius: 4px 4px 0 0; font-style: normal; font-variant-numeric: tabular-nums; }

/* the lens: a close-up's own pictures and text, open only while the playhead is inside its span */
#lens { display: grid; grid-template-rows: 0fr; visibility: hidden; background: var(--panel); transition: grid-template-rows .5s cubic-bezier(.3,.1,.2,1), visibility 0s .5s; }
#lens.open { grid-template-rows: 1fr; visibility: visible; transition: grid-template-rows .5s cubic-bezier(.3,.1,.2,1); }
#lens > .in { min-height: 0; overflow: hidden; }
#lens .box { display: grid; grid-template-columns: minmax(0, 62fr) minmax(0, 38fr); border-bottom: 1px solid var(--line); }
#lpic { position: relative; overflow: hidden; background: #000; cursor: pointer; }
#lpic::after { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 2px; background: var(--warm); pointer-events: none; }
#limg { position: absolute; inset: 0; background-repeat: no-repeat; background-size: 100% 100%; }
#ltext { padding: 12px 28px 18px 26px; min-width: 0; max-height: var(--lh, 240px); overflow-y: auto; scrollbar-width: thin; scrollbar-color: #3a3745 transparent; }
#lkick { font: 600 11px/1.3 var(--sans); letter-spacing: .1em; text-transform: uppercase; color: var(--warm); }
#lkick span { font: 11.5px/1.3 var(--mono); letter-spacing: 0; text-transform: none; color: var(--muted); margin-left: 10px; }
#lmeta { font-family: var(--mono); font-size: 11px; color: var(--dim); display: flex; gap: 4px 16px; flex-wrap: wrap; margin-top: 6px; }
#ltext p { margin: 0; font-size: 13.5px; line-height: 1.5; color: #d3cdc3; text-wrap: pretty; }

#note { padding: 12px 28px 26px; max-width: 1500px; margin: 0 auto; }
#note .meta { font-family: var(--mono); font-size: 11.5px; color: var(--dim); display: flex; gap: 18px; flex-wrap: wrap; }
#note .meta b { color: var(--muted); font-weight: 500; }
#note .grid { display: grid; grid-template-columns: minmax(300px, 1.1fr) minmax(0, 2fr); gap: 40px; margin-top: 12px; }
#note .grid.again { grid-template-columns: minmax(300px, 1.35fr) minmax(0, 2fr); }
.prior { font-size: 13px; line-height: 1.45; color: var(--muted); border-left: 2px solid var(--line); padding-left: 12px; margin-bottom: 12px; text-wrap: pretty; }
.prior.said { margin: 14px 0 0; }
.prior.said[hidden] { display: none; }
.prior span, .expect span, .cols h3, #ltext h3 { display: block; font: 600 10.5px/1.2 var(--sans); letter-spacing: .09em; text-transform: uppercase; color: var(--dim); margin: 0 0 5px; }
#ltext h3 { margin-top: 14px; }
.feel { font-family: var(--serif); font-weight: 400; font-size: clamp(19px, 1.5vw, 23px); line-height: 1.32; margin: 0; letter-spacing: -0.005em; text-wrap: pretty; }
#note > .feel { max-width: 1120px; font-size: clamp(18px, 1.4vw, 21px); margin: 0 0 12px; }
.cols { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 26px; align-content: start; }
.cols.n2, .cols.n4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.cols p { margin: 0; font-size: 14px; line-height: 1.52; color: #d3cdc3; text-wrap: pretty; }
.expect { margin-top: 14px; padding: 10px 14px; background: var(--panel); border-radius: 6px; font-size: 13px; line-height: 1.5; color: #d3cdc3; text-wrap: pretty; }
.expect span { color: var(--warm); }
.expect.none { display: none; }
button.t { font-family: var(--mono); font-size: .9em; color: var(--cyan); border-bottom: 1px dotted rgba(58,216,238,.5); border-radius: 2px; padding: 0 1px; transition: background .2s, color .2s; }
button.t:hover { background: rgba(58,216,238,.16); }
button.t.now { background: var(--cyan); color: var(--bg); border-bottom-color: transparent; }
button.t.ref { font-family: inherit; font-size: inherit; color: var(--warm); border-bottom-color: rgba(244,162,89,.55); }
button.t.ref:hover { background: rgba(244,162,89,.16); }
button.t.ref.now { background: var(--warm); color: var(--bg); }
#note.swap .grid, #note.swap > .feel { animation: swap .45s ease-out; }
@keyframes swap { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }

#after { border-top: 1px solid var(--line); background: var(--panel); padding: 44px 28px 80px; }
#after article { max-width: 680px; margin: 0 auto; font-family: var(--serif); font-size: 18px; line-height: 1.62; }
#after article + article { margin-top: 64px; padding-top: 44px; border-top: 1px solid var(--line); }
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
  #transport { flex-wrap: wrap; } #map { flex-basis: 100%; order: 9; }
  #note .grid, #note .grid.again, .cols, .cols.n2, .cols.n4, #lens .box { grid-template-columns: 1fr; gap: 16px; } #seal span { display: none; }
  #lens .box { gap: 0; } #ltext { padding: 12px 16px 16px; max-height: none; }
}
@media (prefers-reduced-motion: reduce) {
  #note.swap .grid, #note.swap > .feel { animation: none; } .col .veil, .lk, #lens, #lens.open { transition: none; }
}
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
  <div id="tabs" role="tablist" aria-label="Which listen" hidden></div>
  <div id="map" title="Click to jump"><div class="img"></div><div class="unheard"></div></div>
  <label id="seal" title="Each window stayed hidden until the note for the one before it was locked"><input type="checkbox" checked> <span>hide what I hadn't seen yet</span></label>
</div>
<div id="score"><div id="strip"></div><div class="labels" id="labels"></div><div id="head"><i>0:00</i></div></div>
<section id="lens" aria-hidden="true"><div class="in"><div class="box">
  <div id="lpic" title="Click to jump inside the close-up"><div id="limg"></div><div class="labels" id="llabels"></div><div id="lhead"><i>0:00.00</i></div></div>
  <div id="ltext">
    <div id="lkick"></div>
    <div id="lmeta"></div>
    <h3>What I was looking for</h3><p id="lwhy"></p>
    <h3>What I saw</h3><p id="lsaw"></p>
  </div>
</div></div></section>
<section id="note" aria-live="off">
  <div class="meta" id="meta"></div>
  <div class="grid" id="grid">
    <div>
      <div class="prior"><span>Going in, I'd guessed</span><div id="prior"></div></div>
      <h2 class="feel" id="feel"></h2>
      <div class="prior said" id="saidbox" hidden><span>What I said the first time</span><div id="said"></div></div>
      <div class="expect" id="expectbox"><span>Then, before the next window opened, I guessed</span><div id="expect"></div></div>
    </div>
    <div class="cols" id="cols"></div>
  </div>
</section>
<section id="after"></section>

<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
const fmt = t => Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2, '0');
const fmtd = (t, n) => { const m = Math.floor(t / 60), s = t - m * 60; return m + ':' + (s < 10 ? '0' : '') + s.toFixed(n); };
const edge = t => Number.isInteger(t) ? fmt(t) : fmtd(t, 1);
const pad = (k, n) => String(k).padStart(n, '0');
const LS = D.listens, W = LS[0].wins, two = LS.length > 1;
const query = new URLSearchParams(location.search);
let cur = Math.max(0, Math.min(LS.length - 1, (parseInt(query.get('listen')) || 1) - 1));   // which listen is showing
const numeral = n => ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'][n] || String(n);

$('h1').innerHTML = D.title + ' <em>listened to in order</em>';
$('sub').textContent = D.artist + ' · ' + D.nwin + ' windows of ' + D.win + ' s · ' + D.by + ' · ' + D.date;
$('how').textContent = "I can't take in sound. A tool measured this recording into pictures, " + D.win +
  " seconds at a time; I read one window, wrote a note, and the note was locked before the next window opened. " +
  (LS[0].took ? "The whole listen took " + LS[0].took + ". " : "") + (D.note ? D.note + " " : "") +
  (two ? "Then I listened a second time, with a picture of the band's notes" + (LS[1].looks.length ? " and " +
    numeral(LS[1].looks.length) + " close-up" + (LS[1].looks.length > 1 ? "s" : "") + " of moments I chose" : "") + ". " : "") +
  (D.yt ? "Press play: the music comes from YouTube, and the pictures pass under the line with what I wrote beneath them."
        : "Press play: the pictures pass under the line with what I wrote beneath them.");
$('how').classList.toggle('wide', two);
if (D.link) { $('ctalink').href = D.link; $('cta').hidden = false; }
const lastAfter = LS.reduce((m, l, j) => l.after ? j : m, 0);
LS.forEach((l, j) => {
  if (!l.after) return;
  const a = el('article'); a.dataset.listen = j;
  const k = el('div', 'kicker'); k.textContent = l.kicker; a.appendChild(k);
  const b = el('div'); b.innerHTML = l.after; a.appendChild(b); $('after').appendChild(a);
  if (j === lastAfter) {
    const m = el('div', 'made'); m.textContent = D.made;
    if (D.link) { const x = el('a'); x.href = D.link; x.target = '_blank'; x.rel = 'noopener';
      x.textContent = 'The tool is free to use: have your own Claude listen to a song.'; m.append(' ', x); }
    a.appendChild(m);
  }
});
if (two) {
  $('tabs').hidden = false;
  LS.forEach((l, j) => { const b = el('button'); b.setAttribute('role', 'tab'); b.textContent = l.name;
    b.addEventListener('click', e => { setListen(j); if (e.detail) b.blur(); }); $('tabs').appendChild(b); });
}

// ---- the strip: one column per window; an instrument tile and (in a listen with the notes picture) a notes tile.
// In such a listen the instrument tile is shown as two crops of itself, the voice tracks squeezed and the stems not.
const strip = $('strip'), score = $('score'), labels = $('labels'), sealBox = $('seal').querySelector('input');
const cols = W.map(n => {
  const c = el('div', 'col'), h = el('div', 'head');
  h.textContent = 'window ' + pad(n.k, 3) + '  ' + n.span; c.appendChild(h);
  c.tile = el('div', 'tile');
  c.stile = el('div', 'tile');
  c.tile.style.backgroundImage = c.stile.style.backgroundImage = 'url("' + LS[0].inst.dir + '/' + pad(n.k, 3) + '.jpg")';
  c.stile.style.backgroundPosition = 'bottom'; c.appendChild(c.tile); c.appendChild(c.stile);
  c.ntile = el('div', 'tile nt'); c.appendChild(c.ntile);
  const v = el('div', 'veil'); v.textContent = 'not opened yet'; c.appendChild(v);
  strip.appendChild(c); return c;
});
const looksBox = el('div'); looksBox.id = 'looks'; strip.appendChild(looksBox);
const hideOn = () => sealBox.checked;
const lookOpen = (lk, k) => !(hideOn() && k < lk.win);   // a close-up stays hidden until the window it was made in is open

let marks = [];   // the left overlay: track names and axis ticks, placed by layout()
function overlay(box, set, part, out) {
  set.tracks.forEach(tr => {
    const d = el('div'); d.textContent = tr.label;
    if (tr.hint) { const s = el('span'); s.textContent = tr.hint; d.appendChild(s); }
    box.appendChild(d); out.push({ el: d, part, f: tr.top, h: tr.h });
  });
  set.ticks.forEach(([txt, f, cls], i) => {
    const d = el('div', cls); d.textContent = txt; box.appendChild(d);
    out.push({ el: d, part, f, i, cls, tr: set.tracks.find(tr => f >= tr.top && f < tr.top + tr.h) });
  });
}
function place(m, y) {   // one overlay mark; y maps a fraction of its tile's height to pixels in the overlay
  if (!m.tr) { const th = y(m.f + m.h) - y(m.f); m.el.style.top = (y(m.f) + (th < 18 ? 1 : 5)) + 'px'; return; }
  m.el.style.top = y(m.f) + 'px';
  const into = y(m.f) - y(m.tr.top), th = y(m.tr.top + m.tr.h) - y(m.tr.top);   // a tick: not under the track's name, not on its floor
  m.el.style.display = (into < 22 || th - into < 6 || (m.cls === 'hz' && th < 110 && m.i % 2 === 0)) ? 'none' : '';
}

let shown = -1, lens = -2, times = [];
function setListen(j) {
  cur = j; const l = LS[cur];
  [...$('tabs').children].forEach((b, i) => b.setAttribute('aria-selected', i === cur ? 'true' : 'false'));
  cols.forEach((c, i) => {
    c.title = l.wins[i].feelPlain; c.ntile.style.display = l.notes ? '' : 'none';
    if (l.notes && !c.ntile.style.backgroundImage) c.ntile.style.backgroundImage = 'url("' + l.notes.dir + '/' + pad(i, 3) + '.jpg")';
  });
  labels.innerHTML = ''; marks = [];
  overlay(labels, l.inst, 0, marks); if (l.notes) overlay(labels, l.notes, 1, marks);
  looksBox.innerHTML = '';
  l.looks.forEach(lk => {
    const b = el('button', 'lk'); b.innerHTML = '<b>close-up ' + pad(lk.n, 2) + '</b>'; lk.chip = b.firstChild;
    b.title = 'Close-up ' + pad(lk.n, 2) + ', ' + edge(lk.t0) + '-' + edge(lk.t1) + ': go to it';
    b.addEventListener('click', e => { e.stopPropagation(); seekLook(lk.n); if (e.detail) b.blur(); });
    looksBox.appendChild(b); lk.el = b;
  });
  shown = -1; lens = -2;
  layout();
}

let winW = 800, pps = 53, headX = 300, H = 400, lensH = 236, gutter = 184;
function layout() {
  const vw = score.clientWidth, l = LS[cur];
  winW = Math.round(Math.max(520, Math.min(1150, vw * 0.62)));
  pps = winW / D.win; headX = Math.round(vw * 0.36);
  const vh = innerHeight, cl = (lo, x, hi) => Math.round(Math.max(lo, Math.min(hi, x))), sp = l.inst.split, tight = D.yt ? 0.93 : 1;
  H = cl(250, vh * (D.yt ? 0.33 : 0.36), 440);
  // with a notes track: the voice tracks are squeezed, the stems keep their lanes, the notes get the rest
  const voiceH = l.notes ? cl(66, vh * 0.093 * tight, 150) : Math.round(H * sp), stemH = l.notes ? cl(92, vh * 0.1145, 170) : H - voiceH,
    noteH = l.notes ? cl(120, vh * 0.162 * tight, 260) : 0, instH = voiceH + stemH;
  H = instH + noteH;
  cols.forEach((c, i) => { c.style.width = Math.round((W[i].t1 - W[i].t0) * pps) + 'px';
    c.tile.style.width = c.stile.style.width = c.ntile.style.width = winW + 'px';
    c.tile.style.height = voiceH + 'px'; c.tile.style.backgroundSize = '100% ' + (voiceH / sp) + 'px';
    c.stile.style.height = stemH + 'px'; c.stile.style.backgroundSize = '100% ' + (stemH / (1 - sp)) + 'px';
    c.ntile.style.height = noteH + 'px'; });
  const yi = f => f < sp ? f / sp * voiceH : voiceH + (f - sp) / (1 - sp) * stemH, yn = f => instH + f * noteH;
  marks.forEach(m => place(m, m.part ? yn : yi));
  l.looks.forEach(lk => { lk.el.style.left = (lk.t0 * pps) + 'px'; lk.el.style.width = ((lk.t1 - lk.t0) * pps) + 'px'; });
  score.style.height = (H + 22) + 'px'; $('head').style.left = headX + 'px';
  labels.style.width = Math.min(150, headX) + 'px';
  gutter = vw > 700 ? 184 : Math.min(150, headX);   // the fixed labels' width: a bracket's chip keeps clear of it
  const m = $('map'), mw = m.clientWidth;
  m.querySelector('.img').style.backgroundImage = 'url("' + LS[0].inst.dir + '/map.jpg")';
  m.querySelectorAll('.tick, .mlook').forEach(t => t.remove());
  for (let k = 1; k < D.nwin; k++) { const t = el('div', 'tick'); t.style.left = (k * D.win / D.dur * mw) + 'px'; m.appendChild(t); }
  l.looks.forEach(lk => { const t = el('div', 'mlook'); t.style.left = (lk.t0 / D.dur * 100) + '%';
    t.style.width = ((lk.t1 - lk.t0) / D.dur * 100) + '%'; t.title = 'close-up ' + pad(lk.n, 2); m.appendChild(t); });
  lensH = cl(200, vh * 0.26, 260);   // a close-up's lead panel and piano roll; a timbre panel adds to it
  if (lens >= 0) showLens(lens);
  draw(true);
}

// ---- everything on screen is a function of the audio clock (and of which listen is chosen)
function gather() {
  times = [...document.querySelectorAll('#note button.t, #lens button.t')];
  times.forEach(b => { b._c = b.dataset.look ? null : +b.dataset.c; b._w = String(b.dataset.t).includes('.') ? 0.8 : 1.6; });
}
function showNote(k) {
  const l = LS[cur], n = l.wins[k]; shown = k;
  $('meta').innerHTML = '<span><b>window ' + pad(k, 3) + ' of ' + pad(D.nwin - 1, 3) + '</b> · ' + n.span + '</span>' +
    (n.opened ? '<span>opened ' + n.opened + '</span>' : '') +
    (n.sealed ? '<span>note sealed ' + n.sealed + (k < D.nwin - 1 ? ', before window ' + pad(k + 1, 3) + ' opened' : '') + '</span>' : '') +
    (n.sha ? '<span>sha256 ' + n.sha + '…</span>' : '') +
    (l.kind === 'second' ? '<span>shown this time: ' + (l.full ? 'all three pictures' : 'the notes picture') + ', the words, my first note</span>' : '');
  $('prior').innerHTML = k === 0 ? 'Nothing yet. This is the first window.' : (l.wins[k - 1].expect || 'No guess written.');
  const feel = $('feel');   // a second listen: the headline runs across the top, above the seal line
  if (l.kind === 'second') { if (feel.parentNode !== $('note')) $('note').insertBefore(feel, $('meta')); }
  else if (feel.parentNode === $('note')) $('prior').parentNode.after(feel);
  feel.innerHTML = n.feel; $('expect').innerHTML = n.expect;
  $('expectbox').classList.toggle('none', !n.expect);
  $('saidbox').hidden = !n.first; $('said').innerHTML = n.first;
  $('grid').classList.toggle('again', l.kind === 'second');
  $('cols').className = 'cols n' + l.heads.length;
  $('cols').innerHTML = l.heads.map((h, i) => n.cols[i] ? '<div><h3>' + h + '</h3><p>' + n.cols[i] + '</p></div>' : '').join('');
  gather();
  const e = $('note'); e.classList.remove('swap'); void e.offsetWidth; e.classList.add('swap');
}
function showLens(i) {
  const box = $('lens'); lens = i;
  box.classList.toggle('open', i >= 0); box.setAttribute('aria-hidden', i >= 0 ? 'false' : 'true');
  LS[cur].looks.forEach((lk, j) => lk.el.classList.toggle('now', j === i));
  if (i < 0) return;
  const lk = LS[cur].looks[i], ph = Math.round(lensH * lk.tall), lm = [];
  $('lpic').style.height = ph + 'px'; box.style.setProperty('--lh', ph + 'px'); $('ltext').scrollTop = 0;
  $('limg').style.backgroundImage = 'url("' + lk.img + '")';
  $('llabels').innerHTML = ''; overlay($('llabels'), lk, 0, lm); lm.forEach(m => place(m, f => f * ph));
  $('lkick').innerHTML = 'Close-up ' + pad(lk.n, 2) + ' <span>' + edge(lk.t0) + '-' + edge(lk.t1) + ' · ' + lk.zoom + '× a window\'s time detail</span>';
  $('lmeta').innerHTML = (lk.asked ? '<span>asked for ' + lk.asked + ', before the picture existed</span>' : '') +
    (lk.sealed ? '<span>note sealed ' + lk.sealed + '</span>' : '') + (lk.sha ? '<span>sha256 ' + lk.sha + '…</span>' : '');
  $('lwhy').innerHTML = lk.why; $('lsaw').innerHTML = lk.saw;
  gather();
}
function draw(force) {
  const t = Math.min(window.__t ?? clock.now(), D.dur), k =   // window.__t: a frame renderer's clock
   Math.min(Math.floor(t / D.win), D.nwin - 1), l = LS[cur];
  strip.style.transform = 'translate3d(' + (headX - t * pps) + 'px,0,0)';
  $('head').firstElementChild.textContent = fmt(t);
  $('clock').innerHTML = '<b>' + fmt(t) + '</b> / ' + fmt(D.dur);
  $('map').querySelector('.unheard').style.left = (t / D.dur * 100) + '%';
  if (k !== shown || force) {
    const hide = hideOn();
    cols.forEach((c, i) => { c.classList.toggle('unseen', hide && i > k); c.classList.toggle('now', i === k); });
    l.looks.forEach(lk => lk.el.classList.toggle('unseen', !lookOpen(lk, k)));
    if (k !== shown) showNote(k);
  }
  for (const lk of l.looks) {   // a bracket's chip slides along its bracket to stay clear of the fixed labels
    const x = headX + (lk.t0 - t) * pps, w = (lk.t1 - lk.t0) * pps, cw = lk.cw || (lk.cw = lk.chip.offsetWidth), off = Math.max(0, gutter - x);
    lk.el.classList.toggle('gone', off > w - cw);
    lk.chip.style.transform = off && off <= w - cw ? 'translateX(' + off + 'px)' : '';
  }
  const li = l.looks.findIndex(lk => t >= lk.t0 && t < lk.t1 && lookOpen(lk, k));
  if (li !== lens) showLens(li);
  if (li >= 0) { const lk = l.looks[li];
    $('lhead').style.left = ((t - lk.t0) / (lk.t1 - lk.t0) * 100) + '%'; $('lhead').firstElementChild.textContent = fmtd(t, 2); }
  for (const b of times) b.classList.toggle('now', b._c === null ? (li >= 0 && l.looks[li].n === +b.dataset.look) : Math.abs(t - b._c) < b._w);
}
function loop() { draw(); if (!clock.paused()) requestAnimationFrame(loop); }
function seek(t) { clock.seek(Math.max(0, Math.min(D.dur - 0.05, t))); draw(); }
function seekLook(n) {   // to the start of a close-up, or with the veil on to the first moment it existed
  const lk = LS[cur].looks.find(x => x.n === n);
  if (lk) seek(Math.max(lk.t0, hideOn() ? lk.win * D.win : 0) + 0.02);
}
function toggle() { clock.paused() ? clock.play() : clock.pause(); }
function onPlay() { $('playicon').setAttribute('d', 'M4 3h4v14H4zM12 3h4v14h-4z'); $('play').setAttribute('aria-label', 'Pause'); loop(); }
function onPause() { $('playicon').setAttribute('d', 'M5 3l12 7-12 7z'); $('play').setAttribute('aria-label', 'Play'); draw(); }
const startAt = parseFloat(query.get('t')) || 0;

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
    const i = d.info, was = state, at = now();
    if (typeof i.playbackRate === 'number') rate = i.playbackRate;
    if (typeof i.playerState === 'number') state = i.playerState;
    if (typeof i.currentTime === 'number') {
      // while playing, trust our own interpolation unless the player has really moved (a seek, a stall)
      const diff = i.currentTime - at;
      base = (state === 1 && was === 1 && Math.abs(diff) < 0.3) ? at + diff * 0.15 : i.currentTime;
    } else base = at;
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
$('lpic').addEventListener('click', e => { const lk = LS[cur].looks[lens]; if (!lk) return;
  const r = $('lpic').getBoundingClientRect(); seek(lk.t0 + (e.clientX - r.left) / r.width * (lk.t1 - lk.t0)); });
sealBox.addEventListener('change', () => draw(true));
document.addEventListener('click', e => {
  const b = e.target.closest('button.t'); if (!b) return;
  const art = b.closest('#after article');
  if (b.dataset.look) { if (art && +art.dataset.listen !== cur) setListen(+art.dataset.listen); seekLook(+b.dataset.look); }
  else seek(+b.dataset.t);
  if (e.detail) b.blur(); if (art) scrollTo({ top: 0, behavior: 'smooth' });
});
document.addEventListener('keydown', e => {
  if (e.target.matches('input') || (e.code === 'Space' && e.target.closest('button'))) return;
  const k = Math.floor(clock.now() / D.win);
  if (e.code === 'Space') { e.preventDefault(); toggle(); }
  else if (e.code === 'ArrowRight') { e.preventDefault(); seek((k + 1) * D.win); }
  else if (e.code === 'ArrowLeft') { e.preventDefault(); seek((clock.now() - k * D.win > 1.5 ? k : k - 1) * D.win); }
});
addEventListener('resize', layout);
setListen(cur);
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="a finished run folder, e.g. runs/2026-11-06-somebody-live-v4")
    ap.add_argument("--second", default="", help="a finished second listen of the same song (see.py again): the page shows both")
    ap.add_argument("--pictures", default="", help="take NNN-voice.png / NNN-band.png (and geometry.json) from this folder "
                    "instead of RUN/windows, e.g. a cache re-rendered after a fix in the tool")
    ap.add_argument("--redrawn", default="", help="with --pictures: the footer's sentence about the redrawn strips "
                    "(default: a timing fix in the tool, up to about half a second earlier)")
    ap.add_argument("--omit-second-after", action="store_true", help="with --second: leave the second listen's after-note off the page")
    ap.add_argument("--redact", action="append", default=[], metavar="NAME=REPLACEMENT", help="replace a whole-word name in "
                    "all displayed note text (repeatable); the sealed files are not touched and the footer says so")
    ap.add_argument("--out", help="output folder (default RUN/score, or RUN/share with --youtube)")
    ap.add_argument("--link", default="", help="a URL for a 'get the tool' link on the page")
    ap.add_argument("--youtube", default="", help="YouTube video id: embed it as the sound source and write no audio")
    ap.add_argument("--audio", help="audio file (default: song.wav in the run's cache)")
    ap.add_argument("--by", default="Claude", help="who listened, for the byline")
    ap.add_argument("--note", default="", help="a sentence for the intro, e.g. what the listener already knew of the song")
    a = ap.parse_args()
    pairs = [tuple(r.split("=", 1)) for r in a.redact]
    if any(len(p) != 2 or not p[0] for p in pairs):
        ap.error("--redact takes NAME=REPLACEMENT")
    run = os.path.abspath(a.run)
    print(build(run, os.path.abspath(a.out) if a.out else os.path.join(run, "share" if a.youtube else "score"),
                a.audio, a.by, a.note, a.youtube, a.link, os.path.abspath(a.second) if a.second else "",
                os.path.abspath(a.pictures) if a.pictures else "", a.redrawn, pairs, a.omit_second_after))


if __name__ == "__main__":
    main()
