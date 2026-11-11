"""Tests for the timbre picture of see.py (`look --timbre`, and the timbre reference card): that the harmonics of a
known synthetic tone land in the right channels and on the right pixel rows; that two tones on one pitch with
different spectral envelopes differ where they were made to; what the narrow filters cost (rise time, noise level);
that the picture shares the time axis of the other close-up pictures; that the look's text, json, seal.log, budget,
tamper and look-ahead checks cover the new file; and that a look without --timbre is what it was. Synthetic audio
only, no models, no lyric text.

    cd listening && ./venv3/bin/python v3/tests/test_timbre.py
"""
import copy, json, os, shutil, stat, subprocess, sys, tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import see  # noqa: E402
import test_periodicity as tp  # noqa: E402   (its synthetic cache and pixel helpers; importing it runs nothing)

C = see.C
SR = 22050
SEE = os.path.join(os.path.dirname(HERE), "see.py")
WHY = ("--why", "which harmonics carry the held note")
results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"   [{info}]" if info != "" else ""))


def cli(*a):
    r = subprocess.run([sys.executable, SEE, *a], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def cams(f):
    """ERB-rate of f Hz (Glasberg & Moore), written out here, not taken from see.py."""
    return 21.4 * np.log10(1.0 + 0.00437 * np.asarray(f, float))


def chan(f):
    """The channel of the timbre filterbank nearest to f Hz: TIMBRE_N channels equally spaced in ERB-rate from
    TIMBRE_FMIN to COCH_FMAX."""
    step = (cams(C["COCH_FMAX"]) - cams(C["TIMBRE_FMIN"])) / (C["TIMBRE_N"] - 1)
    return int(round(float((cams(f) - cams(C["TIMBRE_FMIN"])) / step)))


def px_row(P, f):
    """The pixel row of f Hz in a timbre panel: linear in ERB-rate between hz_lo at y1 and hz_hi at y0."""
    return P["y1"] - (cams(f) - cams(P["hz_lo"])) / (cams(P["hz_hi"]) - cams(P["hz_lo"])) * (P["y1"] - P["y0"])


def tone(f0, amps, dur=1.0, t0=0.0, total=None):
    """A harmonic tone: harmonic h at amplitude amps[h - 1], 30 ms attack and release, from t0 in `total` seconds."""
    total = total or dur
    y = np.zeros(int(total * SR))
    t = np.arange(int(dur * SR)) / SR
    x = sum(a * np.sin(2 * np.pi * (h + 1) * f0 * t) for h, a in enumerate(amps))
    x = x * np.minimum(1, t / 0.03) * np.minimum(1, (dur - t) / 0.03)
    y[int(t0 * SR):int(t0 * SR) + len(x)] = x
    return y.astype(np.float32)


def profile(y, bw=None):
    """Mean level (dB, power-averaged) per channel over the middle half of y, through the timbre filterbank (or
    the same bank with filters bw ERB wide)."""
    if bw is None:
        M, cf, k = see.timbre_data(y, SR, 0.005)
    else:
        cf, k = see.timbre_bank()
        M = see.cochleagram(y, SR, cf, 0.005, bw=bw)
    n = M.shape[1]
    return 10 * np.log10((10 ** (M[:, n // 4:3 * n // 4].astype(float) / 10)).mean(1)), cf, k


def peaks(p, lo_db):
    """Channels that are local maxima of a profile and above lo_db."""
    return [i for i in range(1, len(p) - 1) if p[i] >= p[i - 1] and p[i] > p[i + 1] and p[i] > lo_db]


def rise_ms(f, bw):
    """10-to-90% rise time (amplitude) of the filter at f Hz after a sine at f starts abruptly, ms."""
    t = np.arange(SR) / SR
    y = np.zeros(SR)
    y[SR // 2:] = np.sin(2 * np.pi * f * t[:SR // 2])
    a = 10 ** (see.cochleagram(y, SR, np.array([float(f)]), 1 / SR, align=False, bw=bw)[0].astype(float) / 20)
    a /= a[-200:].mean()
    return 1000.0 * (int(np.argmax(a > 0.9)) - int(np.argmax(a > 0.1))) / SR


def run_folder(root, name, W, scale=None):
    D = os.path.join(root, name)
    os.makedirs(os.path.join(D, "notes"))
    st = dict(work=W, nwin=2, artist="A", title="T", revealed=2, duration=30.0, layout="voice+band+harmony", window=15.0,
              labels=["feel", "voice", "band", "words"], optional=["expect"], looks=3, looks_used=0)
    if scale:
        st["scale"] = scale
    json.dump(st, open(os.path.join(D, "state.json"), "w"))
    return D


def wlook(D, n, text):
    p = os.path.join(D, "looks", f"{n:02d}.md")
    if os.path.exists(p):
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    open(p, "w").write(text)


def row_near(br, P, t, t0, span, f, half=4):
    """(pixel row of the brightest row within +-half px of f's expected row at time t, is it a ridge: brighter than
    the rows `half` + 1 px above and below)."""
    cx = int(round(tp.expected_col(P, t, t0, span)))
    prof = br[:, cx - 3:cx + 4].sum(1)
    r = int(round(px_row(P, f) - 0.5))
    seg = prof[r - half:r + half + 1]
    i = int(np.argmax(seg)) + r - half
    return i + 0.5, prof[i] > prof[i - half - 1] and prof[i] > prof[i + half + 1]


def main():
    root = tempfile.mkdtemp(prefix="see-timbre-")
    try:
        # ------------------------------------------------------------ the measure itself
        cf, k = see.timbre_bank()
        check("the filterbank: TIMBRE_N channels equally spaced in ERB-rate from TIMBRE_FMIN to 8 kHz, those under "
              "TIMBRE_SPLIT counted for the tall panel", len(cf) == C["TIMBRE_N"] and abs(cf[0] - C["TIMBRE_FMIN"]) < 1e-6
              and abs(cf[-1] - C["COCH_FMAX"]) < 1e-6 and cf[k - 1] < C["TIMBRE_SPLIT"] <= cf[k]
              and all(chan(cf[i]) == i for i in (0, 50, 186, 279)), f"{len(cf)} channels, {k} below {C['TIMBRE_SPLIT']:g} Hz")
        f0, nh = 220.0, 11
        p, _, _ = profile(tone(f0, [1.0 / h for h in range(1, nh + 1)]))
        exp = [chan(h * f0) for h in range(1, nh + 1)]
        got = [i for i in peaks(p, p.max() - 40) if i < k]
        close = lambda a_, b_: len(a_) == len(b_) and all(abs(x - y_) <= 1 for x, y_ in zip(a_, b_))   # noqa: E731
        check("A3 (220 Hz) with 11 harmonics at 1/h: the tall panel has exactly 11 ridges, on the channels of 220, "
              "440 ... 2420 Hz (each within one channel: a harmonic can lie half-way between two)", close(got, exp),
              f"expected channels {exp}, measured {got}")
        lev = [float(p[c] - p[exp[0]]) for c in exp]
        want = [20 * np.log10(1.0 / h) for h in range(1, nh + 1)]
        err = max(abs(a - b) for a, b in zip(lev, want))
        check("...each ridge at its harmonic's own level (20*log10(1/h) under the first, within 1 dB: unity gain at "
              "the centre, at most 0.5 dB lost between two channels)", err <= 1.0,
              f"expected {[round(x, 1) for x in want]}, measured {[round(x, 1) for x in lev]}, largest error {err:.2f} dB")
        dips = [float(min(p[exp[h]], p[exp[h + 1]]) - p[exp[h]:exp[h + 1] + 1].min()) for h in range(nh - 1)]
        check("...with a valley between every two neighbours: at least 9 dB up to 2.4 kHz at this pitch",
              min(dips) >= 9.0, f"valleys {[round(d, 1) for d in dips]} dB")
        pe, _, _ = profile(tone(f0, [1.0 / h for h in range(1, nh + 1)]), bw=1.019)
        dipe = [float(min(pe[exp[h]], pe[exp[h + 1]]) - pe[exp[h]:exp[h + 1] + 1].min()) for h in range(nh - 1)]
        check("the same tone through ear-width filters (1.019 ERB, the voice picture's): from the 7th harmonic up "
              "(1.5 kHz) no valley reaches 3 dB, which is why this picture exists", max(dipe[6:]) < 3.0 and dipe[0] > 9.0,
              f"valleys {[round(d, 1) for d in dipe]} dB")
        for f_lo, top_hz, need in ((150.0, 1600.0, 9.0), (100.0, 1000.0, 9.0)):
            q, _, _ = profile(tone(f_lo, [1.0] * int(4000 / f_lo)))
            h = int(round(top_hz / f_lo))
            a_, b_ = chan(h * f_lo), chan((h + 1) * f_lo)
            d = float(min(q[a_], q[b_]) - q[a_:b_ + 1].min())
            h2 = int(round(2500.0 / f_lo))
            a2, b2 = chan(h2 * f_lo), chan((h2 + 1) * f_lo)
            d2 = float(max(q[a2], q[b2]) - q[a2:b2 + 1].min())
            check(f"a {f_lo:g} Hz voice (equal harmonics): stripes still part near {top_hz:g} Hz (valley at least "
                  f"{need:g} dB) and have all but merged at 2.5 kHz (under 5 dB), as the caption says",
                  d >= need and d2 < 5.0, f"valley {d:.1f} dB near {top_hz:g} Hz, {d2:.1f} dB at 2.5 kHz")
        # two envelopes on one pitch: harmonic 3 strong in one, harmonic 10 in the other, the rest alike
        A = [0.1] * nh; A[2] = 1.0
        B = [0.1] * nh; B[9] = 1.0
        pa, _, _ = profile(tone(f0, A))
        pb, _, _ = profile(tone(f0, B))
        d3, d10, d1 = (float(pa[exp[h]] - pb[exp[h]]) for h in (2, 9, 0))
        check("two tones on A3 with different envelopes (3rd harmonic 20 dB up in one, 10th in the other): the "
              "brightest channel is 660 Hz's in the first and 2200 Hz's in the second",
              int(np.argmax(pa)) == exp[2] and int(np.argmax(pb)) == exp[9],
              f"expected channels {exp[2]} and {exp[9]}, measured {int(np.argmax(pa))} and {int(np.argmax(pb))}")
        check("...they differ by +20 dB at 660 Hz and -20 dB at 2200 Hz (within 1 dB), and not at the first harmonic "
              "(within 0.5 dB)", abs(d3 - 20) <= 1.0 and abs(d10 + 20) <= 1.0 and abs(d1) <= 0.5,
              f"expected +20.0, -20.0, 0.0 dB; measured {d3:+.1f}, {d10:+.1f}, {d1:+.1f}")
        check("...and both have the same eleven ridges (same pitch, same stripes)",
              close([i for i in peaks(pa, pa.max() - 40) if i < k], exp)
              and [i for i in peaks(pa, pa.max() - 40) if i < k] == [i for i in peaks(pb, pb.max() - 40) if i < k],
              f"{[i for i in peaks(pa, pa.max() - 40) if i < k]}")
        # what the narrow filters cost
        r = {(f, bw): rise_ms(f, bw) for f in (200, 1000) for bw in (C["TIMBRE_BW"], 1.019)}
        check("the cost in time, as captioned: a sudden start rises (10-90%) in about 50 ms at 200 Hz and 17 ms at "
              "1 kHz here, about 17 and 6 ms through the voice picture's filters (each within 10%)",
              all(abs(r[key] / w - 1) <= 0.1 for key, w in (((200, C["TIMBRE_BW"]), 50.0), ((1000, C["TIMBRE_BW"]), 17.4),
                                                               ((200, 1.019), 16.6), ((1000, 1.019), 5.8))),
              ", ".join(f"{f} Hz at {bw:g} ERB: {v:.1f} ms" for (f, bw), v in r.items()))
        noise = np.random.default_rng(5).standard_normal(4 * SR).astype(np.float32) * 0.1
        pn, _, _ = profile(noise)
        pw, _, _ = profile(noise, bw=1.019)
        dn = float((pw - pn)[20:260].mean())
        check("the cost in level: white noise reads 10*log10(1.019 / 0.34) = 4.8 dB lower than through the voice "
              "picture's filters (within 0.5 dB), while a harmonic alone in its filter keeps its level",
              abs(dn - 10 * np.log10(1.019 / C["TIMBRE_BW"])) <= 0.5 and abs(float(p[exp[0]] - pe[exp[0]])) <= 0.5,
              f"noise {dn:.2f} dB lower; first harmonic {float(p[exp[0]] - pe[exp[0]]):+.2f} dB")
        yb = tone(880.0, [1.0], dur=0.2, t0=2.0, total=4.0)
        Mb, _, _ = see.timbre_data(yb, SR, 0.002)
        row = 10 ** (Mb[chan(880.0)].astype(float) / 10)
        on = np.where(row > row.max() / 2)[0]
        cb = (on[0] + on[-1] + 1) / 2 * (int(round(0.002 * SR)) / SR)
        check("a 0.2 s burst at 880 Hz centred on 2.100 s: its channel's half-power span is centred there within "
              "10 ms (the group delay, 15 ms at this row, is taken out)", abs(cb - 2.1) <= 0.010,
              f"expected 2.100 s, measured {cb:.3f} s")

        # ------------------------------------------------------------ a look on a synthetic cache, with and without
        W = os.path.join(root, "cache"); os.makedirs(W)
        tc = tp.synthetic_cache(W)          # A3 + E4 at 16-21 s, a 60 ms blip of A3 centred on 21.53 s, a click at
        see.stage_harmony(None, W)          # 22.3 s, C4 at 24-28 s (ten harmonics at 1/h)
        see.stage_periodicity(None, W)
        cache_before = {f: tp.sha(os.path.join(dp, f)) for dp, _, fs in os.walk(W) for f in fs}
        DA, DB = run_folder(root, "plain", W), run_folder(root, "timbre", W)
        ts, te = 20.5, 24.5
        ra, oa = cli("look", DA, "20.5", "24.5", *WHY)
        rb, ob = cli("look", DB, "20.5", "24.5", *WHY, "--timbre")
        la, lb = os.path.join(DA, "looks"), os.path.join(DB, "looks")
        old = ["01-band.png", "01-harmony.png", "01-voice.png", "01.json", "01.txt"]
        check("look without --timbre: the five files it always wrote, no timbre picture",
              ra == 0 and sorted(os.listdir(la)) == old and "01-timbre" not in oa, oa + str(sorted(os.listdir(la))))
        check("look --timbre: the same five and 01-timbre.png, named in the output with the others",
              rb == 0 and sorted(os.listdir(lb)) == sorted(old + ["01-timbre.png"]) and "01-timbre.png" in ob
              and ob.index("01-harmony.png") < ob.index("01-timbre.png"), ob + str(sorted(os.listdir(lb))))
        same = [f for f in ("01-voice.png", "01-band.png", "01-harmony.png") if tp.sha(os.path.join(la, f)) == tp.sha(os.path.join(lb, f))]
        check("the voice, band and harmony close-ups are byte-identical with and without --timbre", len(same) == 3, same)
        ja, jb = json.load(open(os.path.join(la, "01.json"))), json.load(open(os.path.join(lb, "01.json")))
        jb2 = copy.deepcopy(jb)
        T = jb2["pictures"].pop("timbre", None)
        check("01.json without --timbre has no timbre entry anywhere; with it, the only difference is "
              "pictures.timbre", "timbre" not in json.dumps(ja) and T is not None and jb2 == ja, sorted(jb["pictures"]))
        ta, tb_ = open(os.path.join(la, "01.txt")).read().splitlines(), open(os.path.join(lb, "01.txt")).read().splitlines()
        check("01.txt: the pictures line lists 01-timbre.png after the others; every other line is the same",
              tb_[1] == ta[1] + ", 01-timbre.png" and ta[:1] + ta[2:] == tb_[:1] + tb_[2:], tb_[1])
        sa, sb = (json.load(open(os.path.join(d, "state.json"))) for d in (DA, DB))
        logb = open(os.path.join(DB, "seal.log")).read()
        check("the budget: a look with --timbre costs one look, like any other (1 used of 3, 'Close-ups left: 2 of 3')",
              sa["looks_used"] == sb["looks_used"] == 1 and "Close-ups left: 2 of 3" in ob and "Close-ups left: 2 of 3" in oa, "")
        check("seal.log: the why line comes first and is the same as ever; the rendered line lists 01-timbre.png",
              "look 01  0:20.50-0:24.50  why: which harmonics carry the held note\n" in logb
              and "look 01 rendered: 01-voice.png, 01-band.png, 01-harmony.png, 01-timbre.png, 01.txt" in logb
              and logb.index("why:") < logb.index("rendered"), logb)
        check("the cache is untouched by both looks (every file byte-identical)",
              cache_before == {f: tp.sha(os.path.join(dp, f)) for dp, _, fs in os.walk(W) for f in fs}, "")
        # geometry and pixels
        png = os.path.join(lb, "01-timbre.png")
        TP = {q["id"]: q for q in T["panels"]}
        LV = {q["id"]: q for q in jb["pictures"]["voice"]["panels"]}
        size = see.png_size(png)
        check("01.json records the picture: its size is the file's, 1568 wide and under the 1568 px cap in height; "
              "panels timbretop, timbre, words; the filter width and frame step it was made with",
              size == (T["w"], T["h"]) and T["w"] == 1568 and T["h"] <= 1568 and list(TP) == ["timbretop", "timbre", "words"]
              and T["filter_erb"] == C["TIMBRE_BW"] and abs(T["frame_ms"] - jb["steps_ms"]["lead"]) < 1e-3,
              f"{size}, {list(TP)}, {T['filter_erb']} ERB, {T['frame_ms']} ms")
        xs = {(q["x0"], q["x1"]) for q in T["panels"]} | {(q["x0"], q["x1"]) for pic in ("voice", "band", "harmony")
                                                            for q in jb["pictures"][pic]["panels"] if q.get("time")}
        check("time axis: all three timbre panels have the x0, x1 of every timed panel of the other close-up pictures "
              "(117.6..1544.48)", xs == {(117.6, 1544.48)} and all(q.get("time") for q in T["panels"]), sorted(xs))
        err = tp.box_errors(png, T["panels"])
        check("every timbre panel box in 01.json is where the PNG has it (each edge within 1.5 px)",
              max(err.values()) <= 1.5, err)
        P, PT = TP["timbre"], TP["timbretop"]
        check("the panels' frequency edges: the tall panel from just under TIMBRE_FMIN to TIMBRE_SPLIT, the strip "
              "from there to just over 8 kHz, and each labelled frequency's row is where the ERB-rate axis puts it "
              "(within 0.6 px)", P["hz_lo"] < C["TIMBRE_FMIN"] < P["hz_hi"] and abs(P["hz_hi"] - C["TIMBRE_SPLIT"]) < 40
              and abs(PT["hz_lo"] - P["hz_hi"]) < 0.02 and PT["hz_hi"] > C["COCH_FMAX"]
              and all(abs(v - px_row(Q, float(f))) <= 0.6 for Q in (P, PT) for f, v in Q["hz"].items())
              and sorted(map(int, P["hz"])) == [100, 200, 300, 400, 600, 800, 1200, 1600, 2400],
              f"tall {P['hz_lo']}..{P['hz_hi']} Hz, strip {PT['hz_lo']}..{PT['hz_hi']} Hz")
        _, br = tp.brightness(png)
        _, brv = tp.brightness(os.path.join(lb, "01-voice.png"))
        fc4 = tp.hz(60)
        rows = [row_near(br, P, 24.35, ts, te - ts, h * fc4) for h in range(1, 9)]
        worst = max(abs(r_ - px_row(P, h * fc4)) for h, (r_, _) in enumerate(rows, 1))
        check("PNG, 24.35 s (one voice on C4, 261.6 Hz): harmonics 1 to 8 are eight ridges, each within 2 px of the "
              "row the panel box gives for its frequency", worst <= 2.0 and all(ok for _, ok in rows),
              "expected " + ", ".join(f"{px_row(P, h * fc4):.1f}" for h in range(1, 9)) + "; measured "
              + ", ".join(f"{r_:.1f}" for r_, _ in rows))
        # the blip (60 ms of A3 centred on 21.53 s), read on its harmonics near 1 kHz, against the voice picture
        tb = 21.53
        band = dict(P, y0=px_row(P, 1400.0), y1=px_row(P, 800.0))
        ce = tp.expected_col(P, tb, ts, te - ts)
        c_t, w_t = tp.column_peak(br, band, tb, ts, te - ts, half=60)
        bandv = dict(LV["lead"], y0=LV["lead"]["y0"] + 0.45 * (LV["lead"]["y1"] - LV["lead"]["y0"]),
                     y1=LV["lead"]["y0"] + 0.62 * (LV["lead"]["y1"] - LV["lead"]["y0"]))
        c_v, w_v = tp.column_peak(brv, bandv, tb, ts, te - ts, half=60)
        tol = 0.017 * (P["x1"] - P["x0"]) / (te - ts)       # the 17 ms rise at 1 kHz, in pixels of a 4 s close-up
        check(f"the blip at 21.53 s: in the timbre picture its column is where the shared axis puts it (within the "
              f"filters' 17 ms at 1 kHz = {tol:.1f} px) and within that of its column in 01-voice.png",
              abs(c_t - ce) <= tol and abs(c_t - c_v) <= tol, f"expected {ce:.1f}, timbre {c_t:.1f} ({w_t} px wide), "
              f"voice {c_v:.1f} ({w_v} px wide)")
        check("...and it is wider here than in the voice picture (the softer onsets the caption warns of)", w_t > w_v,
              f"{w_t} px against {w_v} px")
        cc = tp.expected_col(PT, tc, ts, te - ts)
        c_c, _ = tp.column_peak(br, PT, tc, ts, te - ts, half=20)
        check("the click at 22.30 s: its column in the strip above 2.5 kHz is where the shared axis puts it (+-2 px)",
              abs(c_c - cc) <= 2.0, f"expected {cc:.1f}, measured {c_c:.1f}")
        im = tp.brightness(png)[0]
        cyan = (im[:, :, 0] < 0.25) & (im[:, :, 1] > 0.75) & (im[:, :, 2] > 0.85)
        ys_, xs_ = np.where(cyan[int(P["y0"]):int(P["y1"]), int(tp.expected_col(P, 24.3, ts, 4.0)):int(P["x1"]) - 3])
        check("the lead pitch line is drawn on the first harmonic's row (C4, +-2 px) and nowhere in the strip above",
              len(ys_) > 50 and abs(np.median(ys_) + int(P["y0"]) + 0.5 - px_row(P, fc4)) <= 2.0
              and not cyan[int(PT["y0"]):int(PT["y1"])].any(),
              f"expected row {px_row(P, fc4):.1f}, measured {np.median(ys_) + int(P['y0']) + 0.5 if len(ys_) else None}")

        # ------------------------------------------------------------ the seal, tamper and look-ahead with the new file
        r1, o1 = cli("look", DB, "16", "20", *WHY)
        check("the next look waits for looks/01.md of the timbre look, as for any look", r1 != 0 and "SEALED" in o1
              and "looks/01.md" in o1, o1)
        wlook(DB, 1, "saw: eight stripes over the held note, the lowest brightest\n")
        r2, o2 = cli("look", DB, "16", "20", *WHY)
        logb = open(os.path.join(DB, "seal.log")).read()
        check("...then seals it (read-only, sha256 logged) and makes look 02 without a timbre picture",
              r2 == 0 and "look 01 saw sealed  sha256 " + tp.sha(os.path.join(lb, "01.md")) in logb
              and not os.access(os.path.join(lb, "01.md"), os.W_OK) and "02-timbre.png" not in os.listdir(lb)
              and "02-voice.png" in os.listdir(lb), o2)
        wlook(DB, 2, "saw: two voices a fifth apart\n")
        wlook(DB, 1, "saw: something else entirely, rewritten later\n")
        r3, o3 = cli("look", DB, "24", "28", *WHY, "--timbre")
        r4, o4 = cli("status", DB)
        check("a sealed note of a timbre look changed: look --timbre refuses (TAMPERED), nothing rendered or spent, "
              "status reports it", r3 != 0 and "TAMPERED" in o3 and "looks/01.md" in o3 and "MISMATCH" in o4
              and not any(f.startswith("03") for f in os.listdir(lb))
              and json.load(open(os.path.join(DB, "state.json")))["looks_used"] == 2, o3 + o4)
        wlook(DB, 1, "saw: eight stripes over the held note, the lowest brightest\n")
        shutil.copyfile(png, os.path.join(lb, "03-timbre.png"))
        r5, o5 = cli("look", DB, "24", "28", *WHY, "--timbre")
        check("a 03-timbre.png that `look` never made: refused (LOOKED AHEAD), named, and logged",
              r5 != 0 and "LOOKED AHEAD" in o5 and "03-timbre.png" in o5
              and "never made: 03-timbre.png" in open(os.path.join(DB, "seal.log")).read(), o5)
        os.remove(os.path.join(lb, "03-timbre.png"))
        r6, o6 = cli("look", DB, "24", "28", *WHY, "--timbre")
        r7, o7 = cli("look", DB, "24", "28", *WHY, "--timbre")
        check("the third look (--timbre again) works and spends the last close-up; a fourth is refused",
              r6 == 0 and "03-timbre.png" in os.listdir(lb) and "Close-ups left: 0 of 3" in o6 and r7 != 0
              and "spent" in o7, o6 + o7)
        # a cache that has no lead stem (only what the harmony close-up needs)
        W2 = os.path.join(root, "cache-h"); os.makedirs(os.path.join(W2, "band"))
        for f in ("accomp.wav", "harmony.npz", "meta.json", os.path.join("band", "bass.wav")):
            shutil.copyfile(os.path.join(W, f), os.path.join(W2, f))
        DH = run_folder(root, "harmony-only", W2)
        r8, o8 = cli("look", DH, "20.5", "24.5", *WHY, "--timbre")
        r9, o9 = cli("look", DH, "20.5", "24.5", *WHY)
        check("--timbre on a cache without the lead stem: refused before anything is logged or spent; the same look "
              "without it works", r8 != 0 and "REFUSED" in o8 and "--timbre" in o8 and r9 == 0
              and open(os.path.join(DH, "seal.log")).read().count("look 01  ") == 1
              and sorted(os.listdir(os.path.join(DH, "looks"))) == ["01-harmony.png", "01.json", "01.txt"], o8 + o9)

        # ------------------------------------------------------------ the yellow band, and an 'ear' run
        W3 = os.path.join(root, "cache-y"); shutil.copytree(W, W3)
        import soundfile as sf
        F = dict(np.load(os.path.join(W3, "features.npz")))
        F["use_full"] = (F["ft"] >= 16.0) & (F["ft"] < 18.0)          # the whole vocal is shown from 16 to 18 s
        np.savez_compressed(os.path.join(W3, "features.npz"), **F)
        yv, _ = sf.read(os.path.join(W3, "vocals.wav"))
        tt = np.arange(len(yv)) / SR
        sf.write(os.path.join(W3, "vocals.wav"), (yv + 0.1 * np.sin(2 * np.pi * 5000.0 * tt)).astype(np.float32), SR)
        out = os.path.join(root, "y"); os.makedirs(out)
        C["WIN"], C["SCALE"] = 15.0, "db"
        files, info = see.look_render(W3, dict(artist="A", title="T"), 1, 16.0, 20.0, 30.0, out, see.look_support(W3), timbre=True)
        Ty = {q["id"]: q for q in info["pictures"]["timbre"]["panels"]}
        imy, bry = tp.brightness(os.path.join(out, "01-timbre.png"))
        yel = (imy[:, :, 0] > 0.9) & (imy[:, :, 1] > 0.75) & (imy[:, :, 2] < 0.2)
        cols = np.where(yel.any(0))[0]
        rws = np.where(yel.any(1))[0]
        x16, x18 = (tp.expected_col(Ty["timbre"], t, 16.0, 4.0) for t in (16.0, 18.0))
        r5k = int(px_row(Ty["timbretop"], 5000.0))
        lvl = lambda t: float(bry[r5k - 1:r5k + 2, int(tp.expected_col(Ty["timbre"], t, 16.0, 4.0)) - 3:][:, :7].mean())  # noqa: E731
        check("under a yellow-band span (the whole vocal shown from 16 to 18 s): the band is drawn along the top of "
              "the strip over those columns only (+-2 px)", len(cols) > 0 and abs(cols[0] - x16) <= 2.0
              and abs(cols[-1] + 1 - x18) <= 2.0 and rws[0] >= int(Ty["timbretop"]["y0"]) - 1
              and rws[-1] < Ty["timbretop"]["y0"] + 0.2 * (Ty["timbretop"]["y1"] - Ty["timbretop"]["y0"]),
              f"columns {cols[0] if len(cols) else None}..{cols[-1] if len(cols) else None} (expected {x16:.1f}..{x18:.1f})")
        check("...and what is drawn there is the whole vocal: a 5 kHz tone that is only in vocals.wav shows at 17 s "
              "and not at 19 s", lvl(17.0) > 3 * max(lvl(19.0), 0.01), f"brightness {lvl(17.0):.2f} at 17 s, {lvl(19.0):.2f} at 19 s")
        DE = run_folder(root, "ear", W, scale="ear")
        re_, oe = cli("look", DE, "20.5", "24.5", *WHY, "--timbre")
        ime = tp.brightness(os.path.join(DE, "looks", "01-timbre.png"))[0] if re_ == 0 else None
        TE = {q["id"]: q for q in json.load(open(os.path.join(DE, "looks", "01.json")))["pictures"]["timbre"]["panels"]} \
            if re_ == 0 else TP
        box = lambda a_, Q: a_[int(Q["timbretop"]["y0"]) + 1:int(Q["timbre"]["y1"]) - 1, int(P["x0"]) + 1:int(P["x1"]) - 1]  # noqa: E731
        check("on an 'ear' run the timbre picture stays in dB: its panels are pixel-identical to the dB run's (its "
              "caption says so, one line longer), while the voice close-up differs", re_ == 0
              and np.array_equal(box(ime, TE), box(im, TP)) and TE["timbre"]["y0"] > TP["timbre"]["y0"]
              and tp.sha(os.path.join(DE, "looks", "01-voice.png")) != tp.sha(os.path.join(lb, "01-voice.png")), oe)

        # ------------------------------------------------------------ the timbre reference card
        gr = see.refcard(os.path.join(root, "ref.png"))
        G = gr["timbre"]
        rp = os.path.join(root, "ref-timbre.png")
        RP = {q["id"]: q for q in G["panels"]}
        check("refcard writes a second card, OUT-timbre.png, under the 1568 px cap, on the same margins; the first "
              "card keeps its size", os.path.exists(rp) and see.png_size(rp) == (G["w"], G["h"]) and G["h"] <= 1568
              and (RP["timbre"]["x0"], RP["timbre"]["x1"]) == (117.6, 1544.48) and gr["h"] <= 1568
              and see.png_size(os.path.join(root, "ref.png")) == (gr["w"], gr["h"]), f"{G['w']} x {G['h']}")
        ev = G["events"]
        check("...with the teaching events: one pitch (A3) on two envelopes, breathy, with vibrato, then the first "
              "envelope an octave up and an octave down, and a click",
              [e["midi"] for e in ev] == [57, 57, 57, 57, 69, 45] and ev[0]["formants"] != ev[1]["formants"]
              and ev[2]["breath"] > 0 and ev[3]["vibrato"] > 0 and ev[0]["formants"] == ev[4]["formants"] == ev[5]["formants"]
              and G["click"] > ev[-1]["end"], [e["name"] for e in ev])
        _, brr = tp.brightness(rp)
        R = RP["timbre"]
        f3 = tp.hz(57)

        def stripe(e, h):
            """Brightness of harmonic h of event e at its middle (the ridge within 3 px of its row)."""
            cx = int(round(tp.expected_col(R, (e["start"] + e["end"]) / 2, 0.0, 15.0)))
            r_ = int(round(px_row(R, h * tp.hz(e["midi"])) - 0.5))
            return float(brr[r_ - 3:r_ + 4, cx - 4:cx + 5].mean(1).max())

        def strongest(e, hs):
            return max(hs, key=lambda h: stripe(e, h))
        amp = lambda e, h: (0.05 + sum(1 / (1 + ((h * tp.hz(e["midi"]) - F_) / 130.0) ** 2) for F_ in e["formants"])) / h  # noqa: E731
        lo_, hi_ = range(2, 6), range(6, 12)
        want = [max(r_, key=lambda h: amp(e, h)) for e in ev[:2] for r_ in (lo_, hi_)]
        got_ = [strongest(e, r_) for e in ev[:2] for r_ in (lo_, hi_)]
        check("card, events 1 and 2 (same pitch, two envelopes): the brightest stripe among harmonics 2-5 and among "
              "6-11 is the one the synthesis made strongest: the 3rd (660 Hz) and the 6th in the 'ah', the 2nd and "
              "the 10th (2200 Hz) in the 'ee'", want == [3, 6, 2, 10] and got_ == want, f"expected {want}, measured {got_}")
        d_exp = [20 * np.log10(amp(ev[0], h) / amp(ev[1], h)) for h in (3, 10)]
        d_got = [stripe(ev[0], h) - stripe(ev[1], h) for h in (3, 10)]
        check("...and the 3rd stripe is brighter in the 'ah', the 10th in the 'ee' (made %+.0f and %+.0f dB apart)"
              % tuple(d_exp), d_got[0] > 0.3 and d_got[1] < -0.3, f"brightness differences {d_got[0]:+.2f}, {d_got[1]:+.2f}")
        cx = lambda e: int(round(tp.expected_col(R, (e["start"] + e["end"]) / 2, 0.0, 15.0)))  # noqa: E731
        gap = lambda e, h: float(brr[int(round(px_row(R, h * f3))) - 1:, cx(e) - 20:cx(e) + 21][:3].mean())  # noqa: E731
        check("card, event 3 (breathy): the gap between the 1st and 2nd stripes, black in event 1, holds noise (over "
              "ten times as bright), and the 10th stripe is weaker than in event 1",
              gap(ev[2], 1.5) > 10 * gap(ev[0], 1.5) and stripe(ev[2], 10) < stripe(ev[0], 10),
              f"gap brightness {gap(ev[2], 1.5):.3f} against {gap(ev[0], 1.5):.3f}; 10th stripe {stripe(ev[2], 10):.2f} "
              f"against {stripe(ev[0], 10):.2f}")
        r5_ = int(round(px_row(R, 5 * f3) - 0.5))
        seg = brr[r5_ - 12:r5_ + 13, int(tp.expected_col(R, ev[3]["start"] + 0.3, 0, 15)):int(tp.expected_col(R, ev[3]["end"] - 0.3, 0, 15))]
        ridge = seg.argmax(0).astype(float)
        seg1 = brr[r5_ - 12:r5_ + 13, int(tp.expected_col(R, ev[0]["start"] + 0.3, 0, 15)):int(tp.expected_col(R, ev[0]["end"] - 0.3, 0, 15))]
        dev_exp = (px_row(R, 5 * f3 * 2 ** (-0.5 / 12)) - px_row(R, 5 * f3 * 2 ** (0.5 / 12))) / 2
        check("card, event 4 (vibrato of +-0.5 semitone): the 5th stripe swings by what the axis gives for that "
              "(+-1.5 px), and stays put in event 1", abs((ridge.max() - ridge.min()) / 2 - dev_exp) <= 1.5
              and np.ptp(seg1.argmax(0)) <= 1,
              f"expected +-{dev_exp:.1f} px, measured +-{(ridge.max() - ridge.min()) / 2:.1f} px; event 1: {np.ptp(seg1.argmax(0))} px")
        s5 = [stripe(ev[4], h) for h in (1, 2, 3)]
        d5 = [float(brr[int(round(px_row(R, f))) - 1:, cx(ev[4]) - 4:cx(ev[4]) + 5][:3].mean()) for f in (660.0, 1100.0)]
        check("card, event 5 (the same envelope an octave up): its stripes are the rows of 440, 880, 1320 Hz, and "
              "the rows of the lower octave's odd harmonics between them (660, 1100 Hz) are dark (under a third as "
              "bright)", min(s5) > 3 * max(d5), f"stripes {[round(x, 2) for x in s5]}, between {[round(x, 2) for x in d5]}")
        errc = tp.box_errors(rp, G["panels"])
        check("card: every panel box is where the PNG has it (within 1.5 px)", max(errc.values()) <= 1.5, errc)
    finally:
        C["SCALE"] = "db"
        shutil.rmtree(root, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
