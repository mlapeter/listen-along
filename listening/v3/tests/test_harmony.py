"""Tests for the harmony picture of see.py: which rows and columns carry the energy of known synthetic sounds, in the
measured data and in the rendered PNG, and that the labels, the geometry file and the time axis agree with them.
No real audio, no models.

    cd ~/random/listening && ./venv3/bin/python v3/tests/test_harmony.py
"""
import json, os, shutil, sys, tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import see  # noqa: E402

C = see.C
SR = C["HARM_SR"]
results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"   [{info}]" if info != "" else ""))


def hz(m):
    """Equal temperament, A4 = 440 Hz (written out here, not taken from see.py)."""
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def tone(freqs, dur, t0=0.0, total=None, amp=0.3):
    """Steady sines at freqs from t0 for dur seconds in `total` seconds of silence, 30 ms fades."""
    total = total or dur
    y = np.zeros(int(total * SR))
    t = np.arange(int(dur * SR)) / SR
    x = sum(np.sin(2 * np.pi * f * t) for f in freqs) * amp * np.minimum(1, t / 0.03) * np.minimum(1, (dur - t) / 0.03)
    i = int(t0 * SR)
    y[i:i + len(x)] = x
    return y.astype(np.float32)


def semis(cq, lo):
    """Mean power per semitone row (3 bins each) -> {midi: power}."""
    P = (10.0 ** (cq / 10.0)).mean(1).reshape(-1, 3).sum(1)
    return {lo + i: float(p) for i, p in enumerate(P)}


def top_rows(cq, lo, n):
    s = semis(cq, lo)
    return sorted(sorted(s, key=s.get, reverse=True)[:n])


def expected_row(panel, m):
    """Pixel row of MIDI note m from the panel's box and range alone (rows are equal; the top edge is hi + 0.5)."""
    return panel["y0"] + (panel["midi_hi"] + 0.5 - m) / (panel["midi_hi"] - panel["midi_lo"] + 1) * (panel["y1"] - panel["y0"])


def expected_col(panel, t, t0, win):
    return panel["x0"] + (t - t0) / win * (panel["x1"] - panel["x0"])


def click_col(br, N):
    """Centre (px, with fraction) of the click in the rows of G#5..B6 of a piano-roll panel N: the middle of the
    columns above half the peak."""
    prof = br[int(round(expected_row(N, 95))):int(round(expected_row(N, 80)))].sum(0)[int(N["x0"]) + 2:int(N["x1"]) - 2]
    prof = prof - np.median(prof)
    on = np.where(prof > prof.max() / 2)[0]
    return (on[0] + on[-1] + 1) / 2 + int(N["x0"]) + 2, len(on)


def sine_row(br, N, t, t0, win):
    cx = int(round(expected_col(N, t, t0, win)))
    y0i, y1i = int(np.ceil(N["y0"])) + 1, int(N["y1"]) - 1
    prof = br[y0i:y1i, cx - 3:cx + 4].sum(1)
    on = np.where(prof > prof.max() * 0.8)[0]      # the middle of the rows within 80% of the brightest
    return (on[0] + on[-1] + 1) / 2 + y0i


def brightness(png):
    import matplotlib.image as mpimg
    im = mpimg.imread(png)[:, :, :3].astype(float)
    return im, im.sum(2)


def main():
    root = tempfile.mkdtemp(prefix="see-harm-")
    LO, HI, BLO, BHI = C["HARM_LO"], C["HARM_HI"], C["BASS_LO"], C["BASS_HI"]
    try:
        # ---- names: an independent table
        want = {21: "A0", 24: "C1", 36: "C2", 45: "A2", 59: "B3", 60: "C4", 61: "C#4", 69: "A4", 72: "C5", 95: "B6"}
        got = {m: see.note_name(m) for m in want}
        check("note names: MIDI 60 is C4, 69 is A4, 36 is C2 ...", got == want, got)
        m261 = float(see.hz2midi(np.array([261.63]))[0])
        check("261.63 Hz reads as C4 (MIDI 60)", abs(m261 - 60) < 0.01 and see.note_name(round(m261)) == "C4", f"{m261:.3f}")

        # ---- a sine at A4: the row that carries it
        y = tone([hz(69)], 3.0)
        cq = see.harm_cq(y, SR, C["HARM_HOP"], LO, HI, 0.0)
        check("constant-Q shape: 3 rows per semitone, C2..B6", cq.shape[0] == 3 * (HI - LO + 1) == 180, cq.shape)
        row = int(np.argmax(cq[:, cq.shape[1] // 2]))
        check("sine A4: brightest row is A4's centre row", row == 3 * (69 - LO) + 1, f"expected {3 * (69 - LO) + 1}, measured {row}")
        check("sine A4: brightest semitone is A4", top_rows(cq, LO, 1) == [69], top_rows(cq, LO, 1))
        ch = see.harm_chroma(cq, LO)
        check("sine A4: pitch class A", int(np.argmax(ch.mean(1))) == 9, int(np.argmax(ch.mean(1))))
        ten = see.harm_tuning(y, SR)
        check("sine A4: tuning measured near 0 cents", abs(ten) <= 3, f"{ten:+.1f}")

        # ---- C major triad
        tri = [60, 64, 67]
        y = tone([hz(m) for m in tri], 3.0)
        cq = see.harm_cq(y, SR, C["HARM_HOP"], LO, HI, 0.0)
        check("C major triad: the three brightest semitones are C4, E4, G4", top_rows(cq, LO, 3) == tri, top_rows(cq, LO, 3))
        ch = see.harm_chroma(cq, LO).mean(1)
        check("C major triad: the three brightest pitch classes are C, E, G", sorted(np.argsort(ch)[-3:]) == [0, 4, 7],
              sorted(int(x) for x in np.argsort(ch)[-3:]))
        y = tone([hz(m) for m in (57, 60, 64)], 3.0)
        cqm = see.harm_cq(y, SR, C["HARM_HOP"], LO, HI, 0.0)
        check("A minor triad: A3, C4, E4", top_rows(cqm, LO, 3) == [57, 60, 64], top_rows(cqm, LO, 3))

        # ---- the triad 30 cents sharp
        y30 = tone([hz(m + 0.30) for m in tri], 3.0)
        cents = see.harm_tuning(y30, SR)
        check("detuned triad: tuning measured as +30 cents (+-3)", abs(cents - 30) <= 3, f"{cents:+.1f}")
        cq0 = see.harm_cq(y30, SR, C["HARM_HOP"], LO, HI, 0.0)
        r0 = [int(np.argmax(cq0[3 * (m - LO) - 1:3 * (m - LO) + 4, cq0.shape[1] // 2])) - 1 for m in tri]
        check("detuned triad on an untuned grid: each note peaks one row (a third of a semitone) above its centre row",
              r0 == [2, 2, 2], f"offsets from the note's flat row {r0}")
        cqt = see.harm_cq(y30, SR, C["HARM_HOP"], LO, HI, cents)
        rt = [int(np.argmax(cqt[3 * (m - LO) - 1:3 * (m - LO) + 4, cqt.shape[1] // 2])) - 1 for m in tri]
        check("detuned triad on the grid moved by the measured tuning: each note peaks on its centre row", rt == [1, 1, 1],
              f"offsets {rt}")
        check("detuned triad, tuned grid: brightest semitones C4, E4, G4", top_rows(cqt, LO, 3) == tri, top_rows(cqt, LO, 3))
        mm = see.hz2midi(np.array([hz(69.30)]), cents)[0]
        check("a 440 Hz * 2^(30/1200) pitch line sits on the A4 row of the tuned axis", abs(mm - 69) < 0.03, f"{mm:.3f}")

        # ---- bass: adjacent semitones back to back, and where the bass panel puts them
        yb = tone([hz(33)], 1.5, 0.0, 3.0) + tone([hz(34)], 1.5, 1.5, 3.0)
        bq = see.harm_cq(yb, SR, C["HARM_HOP"], BLO, BHI, 0.0, C["BASS_FSCALE"])
        n = bq.shape[1]
        a, b = top_rows(bq[:, int(n * 0.15):int(n * 0.35)], BLO, 1), top_rows(bq[:, int(n * 0.65):int(n * 0.85)], BLO, 1)
        check("bass panel: A1 then A#1 are told apart", a == [33] and b == [34], f"{a} then {b}")
        f, db = see.bass_pitch(yb, SR, C["HARM_HOP"])
        fa, fb = np.nanmedian(f[int(n * 0.15):int(n * 0.35)]), np.nanmedian(f[int(n * 0.65):int(n * 0.85)])
        check("bass pitch (pYIN): 55.0 Hz then 58.3 Hz within 1%", abs(fa / hz(33) - 1) < 0.01 and abs(fb / hz(34) - 1) < 0.01,
              f"{fa:.2f}, {fb:.2f}")

        # ---- the rendered picture: window 001 (15-30 s) of a 30 s 'song' with a sine A4, a bass A1 and a click
        WIN, t0, tc = C["WIN"], 15.0, 22.3
        ya = tone([hz(69)], 5.0, 16.0, 30.0)
        nc = int(0.004 * SR)
        ya[int(tc * SR) - nc // 2:int(tc * SR) - nc // 2 + nc] += np.random.default_rng(1).uniform(-1, 1, nc).astype(np.float32)
        ybass = tone([hz(33)], 5.0, 23.5, 30.0)
        D = see.harm_scales(see.harmony_data(ya + ybass, ybass, SR, C["HARM_HOP"], see.harm_tuning(ya, SR)))
        tl = np.arange(16.5, 20.5, 0.01)
        lines = dict(lead=(tl, np.full(len(tl), hz(76) * 2 ** (D["cents"] / 1200))),   # an E5 on the tuned axis
                     bass=(np.arange(len(D["bass_f0"])) * D["hop"], D["bass_f0"]))
        png = os.path.join(root, "001-harmony.png")
        g = see.draw_harmony(png, "window 001 of 001   0:15-0:30   test", t0, t0 + WIN, t0 + WIN, D, [], lines=lines)
        im, br = brightness(png)
        P = {p["id"]: p for p in g["panels"]}
        check("picture is 1568 px wide and at most 760 px high", im.shape[1] == 1568 and im.shape[0] <= 760 and
              (g["w"], g["h"]) == (im.shape[1], im.shape[0]), im.shape[:2])
        sg = see.static_geometry()
        xs = {(p["x0"], p["x1"]) for pic in sg.values() for p in pic["panels"] if p.get("time")} | \
             {(p["x0"], p["x1"]) for p in g["panels"] if p.get("time")}
        x0e, x1e = C["FIG_W2"] * C["DPI"] * C["MARG_L"], C["FIG_W2"] * C["DPI"] * C["MARG_R"]
        check("time axis: every timed panel of the voice, band and harmony pictures has the same x0, x1 (118..1544)",
              len(xs) == 1 and abs(P["notes"]["x0"] - x0e) < 0.01 and abs(P["notes"]["x1"] - x1e) < 0.01
              and round(x0e) == 118 and int(x1e) == 1544, f"{sorted(xs)}")
        # the axes box as drawn: the black panel background between the white margins, measured in the PNG
        N = P["notes"]
        rowmid = int((N["y0"] + N["y1"]) / 2)
        white = np.where(br[rowmid] > 2.9)[0]            # the nearest white pixel either side of the panel's middle
        xl, xr = white[white < 800].max() + 1, white[white > 800].min()
        check("piano roll box in the PNG starts and ends at the geometry's x0, x1 (+-2 px: the frame line is 1 px wide)",
              abs(xl - N["x0"]) <= 2 and abs(xr - N["x1"]) <= 2, f"measured {xl}..{xr}, geometry {N['x0']}..{N['x1']}")
        # the click's column: rows of C5 and up (short filters), away from the sine
        col, w15 = click_col(br, N)
        ce = expected_col(N, tc, t0, WIN)
        check("click at 22.30 s: its column in the PNG is where the shared time axis puts 22.30 s (+-1.5 px)",
              abs(col - ce) <= 1.5, f"expected {ce:.1f}, measured {col:.1f}")
        cb = sg["voice"]["panels"][1]
        check("...and the voice and band pictures put 22.30 s at the same column",
              abs(expected_col(cb, tc, t0, WIN) - ce) < 0.01 and abs(expected_col(sg["band"]["panels"][1], tc, t0, WIN) - ce) < 0.01,
              f"{expected_col(cb, tc, t0, WIN):.1f}")
        # the sine's row in the PNG, at 18.5 s
        y0i, y1i = int(np.ceil(N["y0"])) + 1, int(N["y1"]) - 1
        r = sine_row(br, N, 18.5, t0, WIN)
        re_ = expected_row(N, 69)
        check("sine A4: its pixel row in the PNG is the A4 row computed from the panel box (+-1.5 px)",
              abs(r - re_) <= 1.5, f"expected {re_:.1f}, measured {r:.1f}")
        check("geometry: the labelled A4 and C4 rows are those rows", abs(N["notes"]["A4"] - re_) <= 0.6 and
              abs(N["notes"]["C4"] - expected_row(N, 60)) <= 0.6, f"A4 {N['notes']['A4']}, C4 {N['notes']['C4']}")
        check("geometry: every C and natural of C2..B6 is labelled, no sharp is", sorted(N["notes"]) ==
              sorted(see.note_name(m) for m in range(LO, HI + 1) if "#" not in see.note_name(m)) and
              N["notes"]["C2"] > N["notes"]["C6"], len(N["notes"]))
        # the lead line (cyan) drawn at E5
        cyan = (im[:, :, 0] < 0.25) & (im[:, :, 1] > 0.8) & (im[:, :, 2] > 0.9)
        rr = np.where(cyan[y0i:y1i, int(expected_col(N, 18.5, t0, WIN))])[0]
        check("lead pitch line at E5 on the tuned axis is drawn on the E5 row (+-1.5 px)", len(rr) > 0 and
              abs(rr.mean() + y0i + 0.5 - expected_row(N, 76)) <= 1.5,
              f"expected {expected_row(N, 76):.1f}, measured {(rr.mean() + y0i + 0.5) if len(rr) else None}")
        # the bass panel: A1 at 26 s
        B = P["bassnotes"]
        cx = int(round(expected_col(B, 26.0, t0, WIN)))
        b0, b1 = int(np.ceil(B["y0"])) + 1, int(B["y1"]) - 1
        mag = im[b0:b1, cx - 3:cx + 4]
        notgreen = ~((mag[:, :, 1] > 0.8) & (mag[:, :, 0] < 0.7) & (mag[:, :, 2] < 0.4))   # leave the pitch line out
        r = int(np.argmax((mag.sum(2) * notgreen).sum(1))) + b0
        check("bass A1: its pixel row in the bass panel is the A1 row (+-2 px)", abs(r + 0.5 - expected_row(B, 33)) <= 2.0,
              f"expected {expected_row(B, 33):.1f}, measured {r + 0.5:.1f}")
        green = (im[:, :, 1] > 0.9) & (im[:, :, 0] < 0.6) & (im[:, :, 2] < 0.2)
        rr = np.where(green[b0:b1, cx])[0]
        check("bass pitch line (green) is drawn on the A1 row (+-1.5 px)", len(rr) > 0 and
              abs(rr.mean() + b0 + 0.5 - expected_row(B, 33)) <= 1.5,
              f"expected {expected_row(B, 33):.1f}, measured {(rr.mean() + b0 + 0.5) if len(rr) else None}")
        check("bass panel is C1..C4 with C1, C2, C3, C4 labelled", (B["midi_lo"], B["midi_hi"]) == (24, 60) and
              all(k in B["notes"] for k in ("C1", "C2", "C3", "C4")), (B["midi_lo"], B["midi_hi"]))
        # the pitch-class strip: A is the brightest row at 18.5 s, at the geometry's A row
        K = P["classes"]
        k0, k1 = int(np.ceil(K["y0"])), int(K["y1"])
        cx = int(round(expected_col(K, 18.5, t0, WIN)))
        r = int(np.argmax(br[k0:k1, cx - 3:cx + 4].sum(1))) + k0
        check("pitch-class strip: the sine lights the A row; C is the bottom row, B the top", abs(r + 0.5 - K["rows"]["A"]) <= 2.0
              and K["rows"]["C"] > K["rows"]["B"] and list(K["rows"]) == list(see.NOTE_NAMES),
              f"expected {K['rows']['A']:.1f}, measured {r + 0.5:.1f}")

        # ---- a long header is shrunk to fit, never cut off at the picture's edge
        long_head = ("window 016 of 016   4:00-4:07   A Rather Long Artist Name - A Song Title That Goes On For A While   "
                     "voices so far: 3   voice here: A, B, C")
        Dl = dict(D, cents=-49.0)
        see.draw_harmony(os.path.join(root, "long.png"), long_head, t0, t0 + WIN, t0 + WIN, Dl, [])
        _, brh = brightness(os.path.join(root, "long.png"))
        H0 = P["header"]
        ink = np.where((brh[int(H0["y0"]):int(H0["y1"]) + 1] < 1.5).any(0))[0]
        _, brs = brightness(png)
        ink_s = np.where((brs[int(H0["y0"]):int(H0["y1"]) + 1] < 1.5).any(0))[0]
        check("a 150-character header ends inside the picture (shrunk to fit, not cut off); a short one is not shrunk",
              ink[-1] <= 1568 - 6 and ink[-1] > 1400 and ink_s[-1] < 900, f"last ink column {ink[-1]} of 1568 (short header: {ink_s[-1]})")

        # ---- the pipeline: the harmony stage never pulls in fetch or a separation
        W = os.path.join(root, "work"); os.makedirs(os.path.join(W, "band"))
        for st, o in see.OUTPUTS.items():
            if o and st != "harmony":
                open(os.path.join(W, o), "w").write("x")
        check("an existing cache without harmony.npz: prepare plans only harmony and render",
              see.plan_stages(W) == ["harmony", "render"], see.plan_stages(W))
        open(os.path.join(W, "harmony.npz"), "w").write("x")
        check("a complete cache: prepare plans only render", see.plan_stages(W) == ["render"], see.plan_stages(W))
        check("harmony reads only the separations (never fetch directly), and render reads harmony",
              see.DEPS["harmony"] == ["separate", "bands"] and "harmony" in see.DEPS["render"]
              and see.STAGES.index("harmony") == see.STAGES.index("render") - 1, see.DEPS["harmony"])

        # ---- stage + window render + geometry file on a synthetic cache; a close-up at a finer step
        W = os.path.join(root, "cache"); os.makedirs(os.path.join(W, "band"))
        import soundfile as sf
        sf.write(os.path.join(W, "accomp.wav"), ya + ybass, SR)
        sf.write(os.path.join(W, "band", "bass.wav"), ybass, SR)
        json.dump(dict(artist="A", title="T", duration=30.0), open(os.path.join(W, "meta.json"), "w"))
        see.stage_harmony(None, W)
        geom = see.render_harmony(W, W)
        see.write_geometry(os.path.join(W, "windows"), 2, geom)
        gj = json.load(open(os.path.join(W, "windows", "geometry.json")))
        hp = gj["pictures"]["harmony"]
        check("render writes NNN-harmony.png for both windows and geometry.json (window, panel boxes, note rows)",
              sorted(os.listdir(os.path.join(W, "windows"))) == ["000-harmony.png", "001-harmony.png", "geometry.json"]
              and gj["window"] == 15.0 and [p["id"] for p in hp["panels"]] == ["header", "notes", "bassnotes", "classes", "words"]
              and all(k in p for p in hp["panels"] for k in ("x0", "x1", "y0", "y1")) and "A4" in hp["panels"][1]["notes"],
              os.listdir(os.path.join(W, "windows")))
        im2, br2 = brightness(os.path.join(W, "windows", "001-harmony.png"))
        N2 = hp["panels"][1]
        c2, r2 = click_col(br2, N2)[0], sine_row(br2, N2, 18.5, t0, WIN)
        check("the stage's own window 001 (through harmony.npz): same picture size, click column and A4 row",
              im2.shape == im.shape and abs(c2 - ce) <= 1.5 and abs(r2 - re_) <= 1.5 and N2 == json.loads(json.dumps(N)),
              f"click {c2:.1f} (expected {ce:.1f}), A4 row {r2:.1f} (expected {re_:.1f})")
        out = os.path.join(root, "looks"); os.makedirs(out)
        see.C["WIN"] = 15.0
        files, info = see.look_render(W, dict(artist="A", title="T"), 1, 20.5, 24.5, 30.0, out, see.look_support(W))
        check("a close-up on a cache with only the harmony inputs gives the harmony picture and the text",
              files == ["01-harmony.png", "01.txt"], files)
        check("a 4 s close-up's constant-Q step is 2.9 ms (a window: 11.6 ms)", abs(info["steps_ms"]["cq"] - 2.902) < 0.01
              and abs(C["HARM_HOP"] / SR * 1000 - 11.61) < 0.01, info["steps_ms"])
        iml, brl = brightness(os.path.join(out, "01-harmony.png"))
        Nl = info["pictures"]["harmony"]["panels"][1]
        col, w4 = click_col(brl, Nl)
        ce4 = expected_col(Nl, tc, 20.5, 4.0)
        check("close-up 20.5-24.5 s: the click at 22.30 s is at its column on the 4 s axis (+-1.5 px), same margins",
              abs(col - ce4) <= 1.5 and (Nl["x0"], Nl["x1"]) == (N["x0"], N["x1"]) and iml.shape[1] == 1568,
              f"expected {ce4:.1f}, measured {col:.1f}")
        check("the click is 3-4.5x as many pixels wide in the 4 s close-up as in the window (recomputed, not stretched)",
              3.0 <= w4 / max(1, w15) <= 4.5, f"window {w15} px, close-up {w4} px")
        # the refcard goes through the same code
        gr = see.refcard(os.path.join(root, "ref.png"))
        check("refcard renders (1568 wide), same panel boxes as a window picture",
              gr["w"] == 1568 and [(p["id"], p["x0"], p["x1"], p["y0"]) for p in gr["panels"]]
              == [(p["id"], p["x0"], p["x1"], p["y0"]) for p in g["panels"]], gr["h"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
