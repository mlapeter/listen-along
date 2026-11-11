"""Tests for the periodicity panel and the 'ear' intensity scale of see.py: which rows and columns carry known
synthetic voices, in the measured data and in the rendered voice picture; that the panel shares the time axis of the
other panels; that every panel box in geometry.json is where the PNG has it, for all three pictures; that a cache
without the panel still renders the old voice picture. No real audio, no models, no lyric text.

    cd listening && ./venv3/bin/python v3/tests/test_periodicity.py
"""
import hashlib, json, os, shutil, sys, tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import see  # noqa: E402

C = see.C
SR = C["PER_SR"]
HOP = int(round(C["PER_HOP"] * SR))
LO, HI = C["PER_LO"], C["PER_HI"]
results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"   [{info}]" if info != "" else ""))


def hz(m):
    """Equal temperament, A4 = 440 Hz (written out here, not taken from see.py)."""
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def voice(m, dur, t0=0.0, total=None, amp=0.3, nh=10):
    """A harmonic tone on MIDI note m (nh harmonics at 1/h) from t0 for dur seconds in `total` seconds of silence."""
    total = total or dur
    y = np.zeros(int(total * SR))
    t = np.arange(int(dur * SR)) / SR
    x = sum(np.sin(2 * np.pi * h * hz(m) * t) / h for h in range(1, nh + 1) if h * hz(m) < SR / 2.2)
    x = x * amp * np.minimum(1, t / 0.03) * np.minimum(1, (dur - t) / 0.03)
    i = int(t0 * SR)
    y[i:i + len(x)] = x
    return y.astype(np.float32)


def row_of(m):
    """Index of the row centred on MIDI note m in periodicity()'s output (3 rows per semitone from LO - 1)."""
    return 3 * (m - LO + 1) + 1


def maxima(col, floor=0.15):
    """Rows that are local maxima of one frame, at least `floor` of the frame's largest value."""
    return [i for i in range(1, len(col) - 1) if col[i] >= col[i - 1] and col[i] > col[i + 1] and col[i] > floor * col.max()]


def brightness(png):
    import matplotlib.image as mpimg
    im = mpimg.imread(png)[:, :, :3].astype(float)
    return im, im.sum(2)


def expected_row(panel, m):
    return panel["y0"] + (panel["midi_hi"] + 0.5 - m) / (panel["midi_hi"] - panel["midi_lo"] + 1) * (panel["y1"] - panel["y0"])


def expected_col(panel, t, t0, win):
    return panel["x0"] + (t - t0) / win * (panel["x1"] - panel["x0"])


def column_peak(br, P, t_exp, t0, win, half=40):
    """Centre (px) of the brightest run of columns of panel P near time t_exp: the middle of the columns above half
    the peak of the panel's column sums (median removed)."""
    c = int(round(expected_col(P, t_exp, t0, win)))
    prof = br[int(np.ceil(P["y0"])) + 2:int(P["y1"]) - 2, c - half:c + half + 1].sum(0)
    prof = prof - np.median(prof)
    on = np.where(prof > prof.max() / 2)[0]
    return (on[0] + on[-1] + 1) / 2 + c - half, len(on)


def row_peaks(br, P, t, t0, win):
    """Pixel rows (centres) of the local brightness maxima of panel P's column at time t (7 columns summed)."""
    cx = int(round(expected_col(P, t, t0, win)))
    y0i, y1i = int(np.ceil(P["y0"])) + 1, int(P["y1"]) - 1
    prof = br[y0i:y1i, cx - 3:cx + 4].sum(1)
    prof = np.convolve(prof, np.ones(3) / 3, mode="same")
    return [i + y0i + 0.5 for i in range(2, len(prof) - 2) if prof[i] >= prof[i - 1] and prof[i] > prof[i + 1]
            and prof[i] > 0.35 * prof.max()], prof, y0i


def measured_box(br, P):
    """A panel's box as the PNG has it: for each edge, the first pixel that isn't white when walking in from 3 px
    outside the expected edge, the median over several scan lines (so a tick mark or a label on one line is ignored).
    Returns (x0, x1, y0, y1) in the geometry file's convention (pixel edges, not centres)."""
    W = lambda v: v > 2.6      # noqa: E731   white-ish (the word lanes' #fafafa counts as white, a frame line doesn't)
    fr = (0.13, 0.31, 0.5, 0.69, 0.87)
    ys = [int(P["y0"] + f * (P["y1"] - P["y0"])) for f in fr]
    xs = [int(P["x0"] + f * (P["x1"] - P["x0"])) for f in fr]

    def walk(line, start, step):
        i = start
        while 0 <= i < len(line) and W(line[i]):
            i += step
        return i
    x0 = np.median([walk(br[y], int(round(P["x0"])) - 3, 1) for y in ys])
    x1 = np.median([walk(br[y], int(round(P["x1"])) + 3, -1) + 1 for y in ys])
    y0 = np.median([walk(br[:, x], int(round(P["y0"])) - 3, 1) for x in xs])
    y1 = np.median([walk(br[:, x], int(round(P["y1"])) + 3, -1) + 1 for x in xs])
    return x0, x1, y0, y1


def box_errors(png, panels, skip=("header", "held")):
    """{panel id: largest edge error in px} between the geometry's boxes and the PNG."""
    _, br = brightness(png)
    out = {}
    for P in panels:
        if P["id"] in skip:
            continue
        m = measured_box(br, P)
        out[P["id"]] = round(float(max(abs(a - b) for a, b in zip(m, (P["x0"], P["x1"], P["y0"], P["y1"])))), 2)
    return out


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def synthetic_cache(W):
    """A 30 s 'song' with every file the render reads, made by the real stages from synthetic stems (the model
    stages' outputs are written by hand): two voices a fifth apart (A3 + E4) at 16-21 s, a 60 ms blip of a voice
    centred on 21.53 s, a click at 22.3 s, one voice on C4 at 24-28 s (carrying the one fixture word, 'la'); a
    'backing' tone; five band stems; a stereo mix."""
    import soundfile as sf
    T = 30.0
    tc = 22.3
    lead = voice(57, 5.0, 16.0, T) + voice(64, 5.0, 16.0, T) + voice(60, 4.0, 24.0, T) + voice(57, 0.06, 21.5, T)
    nc = int(0.004 * SR)
    lead[int(tc * SR) - nc // 2:int(tc * SR) - nc // 2 + nc] += np.random.default_rng(1).uniform(-1, 1, nc).astype(np.float32)
    back = voice(64, 3.0, 2.0, T, amp=0.2)
    bass = voice(33, 5.0, 23.5, T, nh=4)
    stems = dict(drums=np.zeros_like(lead), bass=bass, guitar=voice(69, 5.0, 3.0, T, nh=5), piano=voice(72, 3.0, 9.0, T, nh=3),
                 other=np.zeros_like(lead))
    for i in (1.0, 2.0, 3.0, 17.0, 18.0, 19.0):
        stems["drums"][int(i * SR):int(i * SR) + nc] += np.random.default_rng(2).uniform(-1, 1, nc).astype(np.float32)
    os.makedirs(os.path.join(W, "band"))
    acc = sum(stems.values())
    sf.write(os.path.join(W, "lead.wav"), lead, SR)
    sf.write(os.path.join(W, "backing.wav"), back, SR)
    sf.write(os.path.join(W, "vocals.wav"), lead + back, SR)
    sf.write(os.path.join(W, "accomp.wav"), acc, SR)
    for n, y in stems.items():
        sf.write(os.path.join(W, "band", f"{n}.wav"), y, SR)
    sf.write(os.path.join(W, "song.wav"), np.stack([lead + back + acc * 0.7, lead + back + acc * 1.3], 1) * 0.5, SR)
    json.dump(dict(artist="A", title="T", duration=T), open(os.path.join(W, "meta.json"), "w"))
    json.dump(dict(model="test"), open(os.path.join(W, "lead.json"), "w"))
    word = dict(text="la", norm="la", adlib=False, line=0, s=24.2, e=27.8, approx=False, stem="lead", why="")
    json.dump(dict(report={}, lines=[dict(t=24.0, text="la")], words=[word]), open(os.path.join(W, "align.json"), "w"))
    json.dump(dict(report=dict(voices=1, changes=0), lines=[dict(line=0, s=24.2, e=27.8, voice="A")],
                   changes=[dict(t=24.2, line=0, voice="A", first=True)]), open(os.path.join(W, "voices.json"), "w"))
    see.stage_features(None, W)
    see.stage_sound(None, W)
    return tc


def main():
    root = tempfile.mkdtemp(prefix="see-per-")
    try:
        # ------------------------------------------------------------ the measure itself
        R = see.periodicity(voice(57, 1.0), SR, HOP)
        col = R[:, R.shape[1] // 2]
        check("periodicity shape: 3 rows per semitone, C2..C6 and one spare semitone at each end",
              R.shape[0] == 3 * (HI - LO + 3) == 153 and R.shape[1] == SR // HOP + 1, R.shape)
        check("one voice on A3: the brightest row is A3's centre row", int(np.argmax(col)) == row_of(57),
              f"expected row {row_of(57)}, measured {int(np.argmax(col))}")
        mx = maxima(col)
        check("one voice on A3: the only other ridges are its sub-multiples A2 (-12) and D2 (-19), each fainter than "
              "the one above (the documented ladder)", mx == [row_of(38), row_of(45), row_of(57)]
              and col[row_of(38)] < col[row_of(45)] < col[row_of(57)],
              f"expected rows {[row_of(38), row_of(45), row_of(57)]}, measured {mx}; strengths "
              f"{[round(float(col[i] / col.max()), 2) for i in mx]}")
        R = see.periodicity(voice(57, 1.0) + voice(64, 1.0), SR, HOP)
        col = R[:, R.shape[1] // 2]
        mx = maxima(col)
        check("two voices a fifth apart (A3 + E4): both notes' centre rows are ridges", row_of(57) in mx and row_of(64) in mx,
              f"expected rows {row_of(57)} and {row_of(64)} among {mx}")
        check("...and the row their ladders share (A2) is a ridge too, the brightest (the documented artefact)",
              row_of(45) in mx and int(np.argmax(col)) == row_of(45),
              f"A2 row {row_of(45)}, brightest {int(np.argmax(col))}; A3 {col[row_of(57)] / col.max():.2f}, "
              f"E4 {col[row_of(64)] / col.max():.2f} of it")
        R = see.periodicity(voice(64, 1.0) + voice(68, 1.0), SR, HOP)
        mx = maxima(R[:, R.shape[1] // 2])
        check("two voices a major third apart (E4 + G#4): two separate ridges on their rows", row_of(64) in mx and row_of(68) in mx,
              f"expected {row_of(64)}, {row_of(68)} among {mx}")
        R = see.periodicity(voice(57, 1.0) + voice(58, 1.0), SR, HOP)
        mid = R[:, 30:70].mean(1)
        pk = int(np.argmax(mid))
        check("two voices a semitone apart (A3 + A#3): one merged ridge between the two rows (the documented limit)",
              row_of(57) <= pk <= row_of(58) and sum(row_of(56) < i < row_of(59) for i in maxima(mid)) == 1,
              f"peak row {pk} (A3 {row_of(57)}, A#3 {row_of(58)})")
        tb = 5.0
        y = voice(57, 0.05, tb, 10.0)
        R = see.periodicity(y, SR, HOP)
        prof = R.sum(0)
        on = np.where(prof > prof.max() / 2)[0]
        cf = (on[0] + on[-1]) / 2
        check("a 50 ms burst centred on 5.025 s: its frames are centred there (frame j = j * 10 ms, +-1 frame)",
              abs(cf - (tb + 0.025) * SR / HOP) <= 1.0, f"expected frame {(tb + 0.025) * SR / HOP:.1f}, measured {cf:.1f}")
        tone_max = float(see.periodicity(voice(57, 1.0), SR, HOP).max())
        yc = np.zeros(15 * SR, np.float32)
        nc = int(0.004 * SR)
        tk = 7.3
        yc[int(tk * SR) - nc // 2:int(tk * SR) - nc // 2 + nc] = np.random.default_rng(1).uniform(-1, 1, nc)
        Rc = see.periodicity(yc, SR, HOP)
        prof = Rc.sum(0)
        on = np.where(prof > prof.max() / 2)[0]
        check("a click at 7.30 s: what little it leaves is centred on its frame (+-1 frame of 10 ms)",
              abs((on[0] + on[-1]) / 2 - tk * SR / HOP) <= 1.0, f"expected frame {tk * SR / HOP:.1f}, measured "
              f"{(on[0] + on[-1]) / 2:.1f} ({len(on)} frames wide)")
        check("silence gives exactly 0; a full-scale click (no period) stays under a quarter of a voice's ridge",
              float(see.periodicity(np.zeros(SR, np.float32), SR, HOP).max()) == 0.0 and Rc.max() < 0.25 * tone_max,
              f"click {Rc.max():.3f}, voice {tone_max:.3f}")

        # ------------------------------------------------------------ the 'ear' scale
        for hop_s in (0.01, 0.005, 0.0013):
            n = int(2.0 / hop_s)
            M = np.full((3, n), -120.0, np.float32)
            k0, k1 = int(0.5 / hop_s), int(1.5 / hop_s)
            M[0, k0:k1] = -20.0      # a sound at the song's top (top = -20 dB here)
            M[1, k0:k1] = -30.0      # the same 10 dB down
            E = see.ear_map(M, hop_s, -20.0)
            d10 = 10 ** (C["EAR_POWER"] * -1.0)
            check(f"ear scale at {hop_s * 1000:g} ms frames: an onset shows at the compressed level (1.0 at the top, "
                  f"{d10:.3f} ten dB under it), a held sound settles to (1 - EAR_ADAPT) of it, silence and the frames "
                  f"after the sound stops are 0",
                  abs(E[0, k0] - 1.0) < 1e-3 and abs(E[1, k0] - d10) < 1e-3 and abs(E[0, k1 - 1] - (1 - C["EAR_ADAPT"])) < 0.01
                  and E[2].max() < 1e-2 and E[0, k1:k1 + int(0.03 / hop_s) + 1].max() < 1e-2 and E[0, :k0].max() < 1e-2,
                  f"onset {E[0, k0]:.3f}/{E[1, k0]:.3f}, held {E[0, k1 - 1]:.3f}, after {E[0, k1]:.4f}")
            d = 10 ** (C["EAR_POWER"] * (M + 20.0) / 10)
            check("...and it never exceeds the compressed level (no energy is invented)", bool((E <= d + 1e-6).all()), "")
        i60 = int(0.5 / 0.01) + 6
        E = see.ear_map(M_ := np.where(np.arange(200)[None, :] >= 50, -20.0, -120.0).astype(np.float32), 0.01, -20.0)
        check("adaptation time: 60 ms after an onset the response is within 0.2 of settled, by 250 ms within 0.01",
              abs(E[0, 56] - 0.5) < 0.2 and abs(E[0, 75] - 0.5) < 0.01, f"{E[0, 56]:.3f}, {E[0, 75]:.3f}")

        # ------------------------------------------------------------ the pipeline
        W0 = os.path.join(root, "plan"); os.makedirs(os.path.join(W0, "band"))
        for st, o in see.OUTPUTS.items():
            if o and st != "periodicity":
                open(os.path.join(W0, o), "w").write("x")
        check("a cache made before the panel: prepare plans only periodicity and render", see.plan_stages(W0) ==
              ["periodicity", "render"], see.plan_stages(W0))
        check("periodicity reads only the vocal separation, and render reads it", see.DEPS["periodicity"] == ["separate"]
              and "periodicity" in see.DEPS["render"] and see.downstream("periodicity") == {"periodicity", "render"},
              see.DEPS["periodicity"])

        # ------------------------------------------------------------ a synthetic cache through the real stages
        W = os.path.join(root, "cache"); os.makedirs(W)
        tc = synthetic_cache(W)
        see.stage_harmony(None, W)
        WIN, t0 = 15.0, 15.0
        C["WIN"] = WIN
        # 1. without periodicity.npz: the voice picture as it was before the panel
        see.stage_render(None, W, W)
        wd = os.path.join(W, "windows")
        g_old = json.load(open(os.path.join(wd, "geometry.json")))["pictures"]
        check("a cache without periodicity.npz renders the old voice picture (1568 x 1150, no periodicity panel) and "
              "says so in geometry.json",
              see.png_size(os.path.join(wd, "001-voice.png")) == (1568, 1150) and g_old["voice"]["h"] == 1150
              and [p["id"] for p in g_old["voice"]["panels"]] == ["header", "lead", "backing", "words", "held"],
              [p["id"] for p in g_old["voice"]["panels"]])
        old = {f: sha(os.path.join(wd, f)) for f in sorted(os.listdir(wd))}
        im_old = brightness(os.path.join(wd, "001-voice.png"))[0]
        # 2. with it
        see.stage_periodicity(None, W)
        see.stage_render(None, W, W)
        new = {f: sha(os.path.join(wd, f)) for f in sorted(os.listdir(wd))}
        changed = sorted(f for f in new if new[f] != old.get(f))
        check("adding periodicity.npz changes only the voice pictures and geometry.json (band, harmony, text, "
              "render.json byte-identical)", changed == ["000-voice.png", "001-voice.png", "geometry.json"], changed)
        gj = json.load(open(os.path.join(wd, "geometry.json")))
        G = gj["pictures"]
        V = {p["id"]: p for p in G["voice"]["panels"]}
        im, br = brightness(os.path.join(wd, "001-voice.png"))
        grow = int(round(see.per_extra(True) * C["DPI"]))
        check("the voice picture grows by the panel only: 1568 x %d, panels header, lead, backing, periodicity, words, "
              "held" % (1150 + grow), im.shape[:2] == (1150 + grow, 1568) and (G["voice"]["w"], G["voice"]["h"]) ==
              (1568, 1150 + grow) and list(V) == ["header", "lead", "backing", "periodicity", "words", "held"]
              and V["backing"]["y1"] < V["periodicity"]["y0"] < V["periodicity"]["y1"] < V["words"]["y0"],
              f"{im.shape[:2]}, {list(V)}")
        cut = int(V["backing"]["y1"]) + 4
        low = int(V["words"]["y0"]) - 4
        check("...everything above it is pixel-identical to the old picture, and everything below it is the old "
              "picture moved down by the panel's height and gap", np.array_equal(im[:cut], im_old[:cut]) and
              np.array_equal(im[low:], im_old[low - grow:]),
              f"top {np.array_equal(im[:cut], im_old[:cut])}, bottom {np.array_equal(im[low:], im_old[low - grow:])}")
        xs = {(p["x0"], p["x1"]) for pic in G.values() for p in pic["panels"] if p.get("time")}
        check("time axis: every timed panel of the three pictures, the periodicity panel among them, has the same x0, "
              "x1 (117.6..1544.48)", len(xs) == 1 and xs == {(117.6, 1544.48)} and V["periodicity"].get("time") is True,
              sorted(xs))
        for pic in ("voice", "band", "harmony"):
            err = box_errors(os.path.join(wd, f"001-{pic}.png"), G[pic]["panels"])
            check(f"geometry.json, {pic} picture: every panel box is where the PNG has it (each edge within 1.5 px), and "
                  f"the picture's size is the file's", max(err.values()) <= 1.5 and
                  see.png_size(os.path.join(wd, f"001-{pic}.png")) == (G[pic]["w"], G[pic]["h"]), err)
        # the held-note area (one close-up in this window: the left cents panel starts at the box's top left corner)
        Hb = V["held"]
        xa, xb = int(Hb["x0"]) + 4, int(Hb["x0"]) + 300          # a stretch of the left close-up's frame lines

        def hline(y_exp):
            """The row within 8 px of y_exp that is a frame line (dark over most of xa..xb), as a pixel centre."""
            rows = range(int(y_exp) - 8, int(y_exp) + 9)
            frac = [(br[r, xa:xb] < 1.5).mean() for r in rows]
            return rows[int(np.argmax(frac))] + 0.5, max(frac)
        (ht, ft_), (hb, fb_) = hline(Hb["y0"]), hline(Hb["y1"])
        rowm = int(Hb["y0"] + 20)
        darkx = np.where(br[rowm, int(Hb["x0"]) - 6:int(Hb["x0"]) + 8] < 1.5)[0] + int(Hb["x0"]) - 6
        check("geometry.json, voice picture: the held-note area's top, bottom and left edges are the close-up's frame "
              "lines (within 1.5 px)", ft_ > 0.8 and fb_ > 0.8 and abs(ht - Hb["y0"]) <= 1.5 and abs(hb - Hb["y1"]) <= 1.5
              and len(darkx) > 0 and abs(darkx[0] + 0.5 - Hb["x0"]) <= 1.5,
              f"top {ht} ({Hb['y0']}), bottom {hb} ({Hb['y1']}), left {darkx[0] if len(darkx) else None} ({Hb['x0']})")
        # the rows of the two voices in the PNG
        P = V["periodicity"]
        pk, prof, y0i = row_peaks(br, P, 18.5, t0, WIN)
        near = lambda m: min(abs(r - expected_row(P, m)) for r in pk)   # noqa: E731
        check("PNG, 18.5 s (A3 + E4 together): a ridge on the A3 row and one on the E4 row (each within 2 px of the "
              "row the panel box gives)", near(57) <= 2.0 and near(64) <= 2.0,
              f"expected {expected_row(P, 57):.1f} and {expected_row(P, 64):.1f}, ridges at {[round(r, 1) for r in pk]}")
        check("...and on the shared sub-multiple A2", near(45) <= 2.0, f"expected {expected_row(P, 45):.1f}")
        pk, prof, y0i = row_peaks(br, P, 26.0, t0, WIN)
        top_row = int(np.argmax(prof)) + y0i + 0.5
        check("PNG, 26 s (one voice on C4): the brightest row is C4's, where geometry.json's notes put C4 (+-2 px)",
              abs(top_row - expected_row(P, 60)) <= 2.0 and abs(P["notes"]["C4"] - expected_row(P, 60)) <= 0.6,
              f"expected {expected_row(P, 60):.1f} (geometry {P['notes']['C4']}), measured {top_row:.1f}")
        check("geometry: the periodicity panel is C2..C6, every natural labelled, no sharp, C2 under C6",
              (P["midi_lo"], P["midi_hi"]) == (36, 84) and sorted(P["notes"]) == sorted(
                  see.note_name(m) for m in range(36, 85) if "#" not in see.note_name(m)) and P["notes"]["C2"] > P["notes"]["C6"],
              len(P["notes"]))
        # columns. A click has no period and leaves almost nothing in this panel (tested above on the data), so the
        # panel's column test is a 60 ms blip of a voice; the click is located in the lead cochleagram above it
        tb = 21.53
        be = expected_col(P, tb, t0, WIN)
        a3 = lambda Q: dict(Q, y0=expected_row(Q, 57) - 6, y1=expected_row(Q, 57) + 6)   # noqa: E731   the blip's own row
        cp, wp_ = column_peak(br, a3(P), tb, t0, WIN, half=30)
        cbl, _ = column_peak(br, V["lead"], tb, t0, WIN, half=30)
        check("a 60 ms blip centred on 21.53 s: its column in the periodicity panel is where the shared time axis puts "
              "21.53 s (+-1.5 px)", abs(cp - be) <= 1.5, f"expected {be:.1f}, measured {cp:.1f} ({wp_} px wide)")
        check("...and the lead cochleagram above it has the blip at the same column (+-1.5 px): the panel's time axis "
              "matches the others", abs(cbl - be) <= 1.5 and abs(cbl - cp) <= 1.5, f"lead {cbl:.1f}, periodicity {cp:.1f}")
        ce = expected_col(P, tc, t0, WIN)
        Lp_ = V["lead"]
        top_ch = dict(Lp_, y1=Lp_["y0"] + 0.4 * (Lp_["y1"] - Lp_["y0"]), y0=Lp_["y0"] + 14)   # channels above ~1 kHz ring least
        cl, wl = column_peak(br, top_ch, tc, t0, WIN, half=20)
        check("click at 22.30 s: its column in the lead cochleagram is where the shared time axis puts 22.30 s "
              "(+-1.5 px; before the frame-step fix it was 4.8 px late here)", abs(cl - ce) <= 1.5,
              f"expected {ce:.1f}, measured {cl:.1f}")
        Ss = np.load(os.path.join(W, "sound.npz"))
        check("sound.npz stores the cochleagrams' true frame steps (110 and 220 samples at 22050 Hz), and the lead has "
              "as many frames as the song has such steps", abs(float(Ss["lead_hop"]) - 110 / 22050) < 1e-12
              and abs(float(Ss["stem_hop"]) - 220 / 22050) < 1e-12 and Ss["lead"].shape[1] == int(30.0 * 22050) // 110,
              f"{float(Ss['lead_hop'])}, {float(Ss['stem_hop'])}, {Ss['lead'].shape[1]}")
        edge = expected_col(P, 16.0, t0, WIN)
        colsum = br[int(P["y0"]) + 2:int(P["y1"]) - 2].sum(0)
        half_on = np.where(colsum[int(edge) - 30:int(edge) + 30] - colsum[int(edge) - 30] >
                           0.5 * (colsum[int(edge) + 29] - colsum[int(edge) - 30]))[0][0] + int(edge) - 30
        check("the two voices' onset at 16.00 s is at its column (half-way point of the rise within 3 px: the 46 ms "
              "frame and the 30 ms attack blur it)", abs(half_on + 0.5 - (edge + 0.015 * (P["x1"] - P["x0"]) / WIN)) <= 3.0,
              f"expected {edge + 0.015 * (P['x1'] - P['x0']) / WIN:.1f}, measured {half_on + 0.5:.1f}")

        # ------------------------------------------------------------ a close-up of the same cache
        out = os.path.join(root, "looks"); os.makedirs(out)
        C["SCALE"] = "db"
        files, info = see.look_render(W, dict(artist="A", title="T"), 1, 20.5, 24.5, 30.0, out, see.look_support(W))
        LV = {p["id"]: p for p in info["pictures"]["voice"]["panels"]}
        check("a close-up gives all three pictures; its voice picture has the periodicity panel between backing and "
              "words on the same margins", files == ["01-voice.png", "01-band.png", "01-harmony.png", "01.txt"]
              and list(LV) == ["lead", "backing", "periodicity", "words"]
              and (LV["periodicity"]["x0"], LV["periodicity"]["x1"]) == (117.6, 1544.48)
              and see.png_size(os.path.join(out, "01-voice.png")) == (1568, int(round((C["LOOK_VOICE_H"] + see.per_extra(True)) * 100))),
              f"{files}, {list(LV)}")
        check("a 4 s close-up's periodicity step is 2.68 ms (a window: 9.98 ms): measured again, not stretched",
              abs(info["steps_ms"]["periodicity"] - 2.676) < 0.01 and abs(HOP / SR * 1000 - 9.977) < 0.01, info["steps_ms"])
        _, brl = brightness(os.path.join(out, "01-voice.png"))
        ce4 = expected_col(LV["periodicity"], tc, 20.5, 4.0)
        Ll = LV["lead"]
        cl4, _ = column_peak(brl, dict(Ll, y1=Ll["y0"] + 0.4 * (Ll["y1"] - Ll["y0"]), y0=Ll["y0"] + 14), tc, 20.5, 4.0)
        be4 = expected_col(LV["periodicity"], tb, 20.5, 4.0)
        c4, w4 = column_peak(brl, a3(LV["periodicity"]), tb, 20.5, 4.0, half=60)
        check("close-up 20.5-24.5 s: the blip is at its column on the 4 s axis in the periodicity panel, the click at "
              "its column in the lead panel (+-2 px)", abs(c4 - be4) <= 2.0 and abs(cl4 - ce4) <= 2.0,
              f"blip expected {be4:.1f}, measured {c4:.1f} ({w4} px wide; window {wp_} px); click expected {ce4:.1f}, "
              f"measured {cl4:.1f}")
        check("the blip is 3-4.5x as many pixels wide in the 4 s close-up as in the window (measured again, not "
              "stretched)", 3.0 <= w4 / max(1, wp_) <= 4.5, f"window {wp_} px, close-up {w4} px")
        err = {pic: box_errors(os.path.join(out, f"01-{pic}.png"), info["pictures"][pic]["panels"])
               for pic in ("voice", "band", "harmony")}
        check("the close-up's 01.json: every panel box of its three pictures is where the PNG has it (within 1.5 px)",
              max(v for e in err.values() for v in e.values()) <= 1.5, err)
        pk, prof, y0i = row_peaks(brl, LV["periodicity"], 24.3, 20.5, 4.0)
        check("close-up, 24.3 s (one voice on C4): the brightest row is C4's (+-2 px)",
              abs(int(np.argmax(prof)) + y0i + 0.5 - expected_row(LV["periodicity"], 60)) <= 2.0,
              f"expected {expected_row(LV['periodicity'], 60):.1f}, measured {int(np.argmax(prof)) + y0i + 0.5:.1f}")

        # ------------------------------------------------------------ the scale switch
        db_voice, db_band = sha(os.path.join(wd, "001-voice.png")), sha(os.path.join(wd, "001-band.png"))
        db_harm, db_txt = sha(os.path.join(wd, "001-harmony.png")), sha(os.path.join(wd, "001.txt"))
        rj_db = json.load(open(os.path.join(wd, "render.json")))
        C["SCALE"] = "ear"
        see.stage_render(None, W, W)
        rj = json.load(open(os.path.join(wd, "render.json")))
        ej = json.load(open(os.path.join(W, "ear.json")))
        check("SCALE = ear: the voice and band pictures change, the harmony picture and the text do not; render.json "
              "says scale ear (a db render has no scale key); the scale's whole-song top is kept in ear.json",
              sha(os.path.join(wd, "001-voice.png")) != db_voice and sha(os.path.join(wd, "001-band.png")) != db_band
              and sha(os.path.join(wd, "001-harmony.png")) == db_harm and sha(os.path.join(wd, "001.txt")) == db_txt
              and rj.get("scale") == "ear" and "scale" not in rj_db and 0 < ej["top"] <= 1.0, f"{rj}, {ej}")
        ime, bre = brightness(os.path.join(wd, "001-voice.png"))
        Lp = V["lead"]
        cx = lambda t: int(round(expected_col(Lp, t, t0, WIN)))   # noqa: E731
        band = slice(int(Lp["y0"]) + 30, int(Lp["y1"]) - 2)

        def onset_ratio(b):
            cols = b[band, cx(24.0):cx(24.0) + 12].sum(0)
            return float(cols.max() / b[band, cx(26.0) - 2:cx(26.0) + 2].sum(0).mean())
        check("ear scale: the onset of the C4 voice (its brightest column in the first 0.12 s) is brighter than its "
              "held part, which it is not on the dB scale (the onset stands out)", onset_ratio(bre) > 1.15
              and onset_ratio(br) < 1.1, f"onset / held brightness: ear {onset_ratio(bre):.2f}, dB {onset_ratio(br):.2f}")
        check("ear scale: the periodicity panel is the same picture (it has its own scale)",
              np.array_equal(ime[int(P["y0"]):int(P["y1"])], im[int(P["y0"]):int(P["y1"])]), "")
        C["SCALE"] = "db"
        see.stage_render(None, W, W)
        check("back to db: the voice and band pictures are byte-identical to the first db render",
              sha(os.path.join(wd, "001-voice.png")) == db_voice and sha(os.path.join(wd, "001-band.png")) == db_band, "")

        # ------------------------------------------------------------ the reference card's periodicity half
        gr = see.refcard(os.path.join(root, "ref.png"))
        U = gr["under"]
        RP = U["panels"][0]
        _, brr = brightness(os.path.join(root, "ref.png"))
        ev = {e["name"]: e for e in U["events"]}
        names = list(ev)
        check("refcard: a periodicity panel under the harmony card, same margins, with the four teaching events (one "
              "voice, a fifth, a semitone, vibrato) and an octave pair, a click and a hiss",
              RP["id"] == "periodicity" and (RP["x0"], RP["x1"]) == (117.6, 1544.48) and len(names) == 5
              and [e["midi"] for e in U["events"]] == [[57], [57, 64], [57, 58], [69], [57, 69]] and gr["h"] <= 1568,
              f"{names}, {gr['w']} x {gr['h']}")
        e1, e2 = U["events"][0], U["events"][1]
        below = lambda rows: [r for r in rows if r > expected_row(RP, 67)]   # noqa: E731   under the captions
        pk = below(row_peaks(brr, RP, (e1["start"] + e1["end"]) / 2, 0.0, 15.0)[0])
        check("refcard event 1 (one voice on A3): the top ridge is on the A3 row, the ladder's next on A2 (+-2 px)",
              abs(min(pk) - expected_row(RP, 57)) <= 2.0 and min(abs(r - expected_row(RP, 45)) for r in pk) <= 2.0,
              f"expected {expected_row(RP, 57):.1f} then {expected_row(RP, 45):.1f}, ridges {[round(r, 1) for r in pk]}")
        pk = below(row_peaks(brr, RP, (e2["start"] + e2["end"]) / 2, 0.0, 15.0)[0])
        check("refcard event 2 (A3 + E4): the top ridge is on the E4 row, then A3, then the shared A2 (+-2 px)",
              abs(min(pk) - expected_row(RP, 64)) <= 2.0 and all(min(abs(r - expected_row(RP, m)) for r in pk) <= 2.0 for m in (57, 45)),
              f"expected {expected_row(RP, 64):.1f}, {expected_row(RP, 57):.1f}, {expected_row(RP, 45):.1f}; ridges "
              f"{[round(r, 1) for r in pk]}")
        err = box_errors(os.path.join(root, "ref.png"), gr["panels"] + U["panels"])
        check("refcard: every panel box (harmony card and periodicity half) is where the PNG has it (within 1.5 px)",
              max(err.values()) <= 1.5, err)
    finally:
        C["SCALE"] = "db"
        shutil.rmtree(root, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
