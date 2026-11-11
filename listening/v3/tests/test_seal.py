"""Tests for the sealed listening loop of see.py (next / status / after / look / again), on fake prepared runs.
No real audio, no models: a work dir with stand-in window files and a state.json like `prepare` writes; the close-up
and second-listen tests add a few seconds of synthetic sine waves so the harmony picture can really be rendered.

    cd listening && ./venv3/bin/python v3/tests/test_seal.py
"""
import hashlib, json, os, shutil, stat, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SEE = os.path.join(os.path.dirname(HERE), "see.py")
GOOD = "feel: steady and unsure\nvoice: low and close\nband: guitar alone, bass enters\nwords: a promise made\n"
GOOD_EXPECT = GOOD + "expect: the hook comes back\n"
LEGACY_GOOD = "feel: steady and unsure\nvoice: low and close\nwords: a promise made\nexpect: the hook comes back\n"
SECOND_GOOD = "feel: warmer this time\nharmony: the bass walks down\nchanged: the ending felt earned\n"
NEW = ["{k:03d}-voice.png", "{k:03d}-band.png", "{k:03d}.txt"]
NEW3 = ["{k:03d}-voice.png", "{k:03d}-band.png", "{k:03d}-harmony.png", "{k:03d}.txt"]
OLD = ["{k:03d}.png", "{k:03d}.txt"]
results = []


def see(*a):
    r = subprocess.run([sys.executable, SEE, *a], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"\n     {info}"))


def fake_run(root, nwin=3, legacy=False, name="run", three=False, audio=False, looks=None):
    """A prepared run: two pictures + text per window (layout voice+band, 15 s windows), or with legacy=True a run
    prepared before the band view (one picture per 10 s window, no layout marker in state.json), or with three=True
    a three-picture run. audio=True adds what the harmony picture is measured from, as synthetic sines (accomp.wav,
    band/bass.wav, meta.json), and for a three-picture run the harmony stage's output; looks = the close-up budget."""
    W, D = os.path.join(root, "work-" + name), os.path.join(root, name)
    os.makedirs(os.path.join(W, "windows")); os.makedirs(os.path.join(D, "notes"))
    for k in range(nwin):
        for f in (OLD if legacy else NEW3 if three else NEW):
            open(os.path.join(W, "windows", f.format(k=k)), "w").write(f"window {k}")
    json.dump(dict(constants={}), open(os.path.join(W, "summary.json"), "w"))
    st = dict(work=W, nwin=nwin, artist="A", title="T", revealed=0)
    if legacy:
        st.update(duration=nwin * 10.0 - 3)
    else:
        st.update(duration=nwin * 15.0 - 4, layout="voice+band+harmony" if three else "voice+band", window=15.0,
                  labels=["feel", "voice", "band", "words"], optional=["expect"])
    if looks is not None:
        st.update(looks=looks, looks_used=0)
    if audio:
        import numpy as np, soundfile as sf
        sys.path.insert(0, os.path.dirname(HERE))
        import see as S
        t = np.arange(int(st["duration"] * 22050)) / 22050
        os.makedirs(os.path.join(W, "band"))
        bass = 0.3 * np.sin(2 * np.pi * 55 * t)
        sf.write(os.path.join(W, "accomp.wav"), (bass + 0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32"), 22050)
        sf.write(os.path.join(W, "band", "bass.wav"), bass.astype("float32"), 22050)
        json.dump(dict(artist="A", title="T", duration=st["duration"]), open(os.path.join(W, "meta.json"), "w"))
        if three:
            S.stage_harmony(None, W)
    json.dump(st, open(os.path.join(D, "state.json"), "w"))
    return W, D


def tree(d):
    """sha256 of every file under d (to show a folder was only read)."""
    out = {}
    for dp, _, fn in os.walk(d):
        for f in fn:
            out[os.path.relpath(os.path.join(dp, f), d)] = hashlib.sha256(open(os.path.join(dp, f), "rb").read()).hexdigest()
    return out


def wlook(D, n, text):
    p = os.path.join(D, "looks", f"{n:02d}.md")
    if os.path.exists(p):
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    open(p, "w").write(text)


def slog(D):
    p = os.path.join(D, "seal.log")
    return open(p).read() if os.path.exists(p) else ""


def looks_dir(D):
    return sorted(os.listdir(os.path.join(D, "looks"))) if os.path.isdir(os.path.join(D, "looks")) else []


def look_tests(root):
    """Close-ups the listener chooses: span, reason, budget, the sealed 'saw:' note, tamper and look-ahead."""
    W, D = fake_run(root, nwin=2, name="lk", three=True, audio=True, looks=3)
    WHY = ("--why", "how the bass sits under the held note")
    rc, out = see("look", D, "0:02", "0:06", *WHY)
    check("look before anything is revealed: refused", rc != 0 and "REFUSED" in out and "revealed" in out, out)
    see("next", D)
    for a, b, word in (("0:02", "0:12", "spans"), ("0:05", "0:06", "spans"), ("0:10", "0:17", "never"),
                       ("12.5", "16", "never")):
        rc, out = see("look", D, a, b, *WHY)
        check(f"look {a}-{b}: refused ({'2 to 8 s' if word == 'spans' else 'not ahead of what is revealed'})",
              rc != 0 and "REFUSED" in out and word in out, out)
    rc, out = see("look", D, "0:04", "0:08")
    check("look without --why: refused", rc != 0 and "--why" in out, out)
    for why in ("todo", "look closer", "...", "tbd tbd tbd tbd"):
        rc, out = see("look", D, "0:04", "0:08", "--why", why)
        check(f"look --why '{why}': refused (three real words)", rc != 0 and "REFUSED" in out and "real words" in out, out)
    check("no refused look left a trace: no looks/ files, no look line in seal.log, nothing spent",
          looks_dir(D) == [] and " look " not in slog(D) and json.load(open(os.path.join(D, "state.json")))["looks_used"] == 0,
          slog(D))
    rc, out = see("look", D, "0:04", "0:08.5", *WHY)
    log = slog(D)
    check("a good look: harmony picture + text written (what this cache supports), named in the output",
          rc == 0 and looks_dir(D) == ["01-harmony.png", "01.json", "01.txt"] and "01-harmony.png" in out
          and "saw:" in out and os.path.getsize(os.path.join(D, "looks", "01-harmony.png")) > 20000, out + str(looks_dir(D)))
    check("seal.log has the look's number, span and why, and that line comes before the rendering line",
          "look 01  0:04.00-0:08.50  why: how the bass sits under the held note" in log
          and 0 < log.index("look 01  0:04") < log.index("look 01 rendered"), log)
    rc, out = see("status", D)
    check("status shows close-ups used and left, and the pending saw: note", "close-ups: 1 used, 2 left of 3" in out
          and "looks/01.md" in out, out)
    write(D, "000", GOOD)
    rc, out = see("next", D)
    check("next refuses until looks/01.md exists; nothing revealed", rc != 0 and "SEALED" in out and "looks/01.md" in out
          and not os.path.exists(os.path.join(D, "windows", "001.txt")), out)
    rc, out = see("look", D, "0:09", "0:13", *WHY)
    check("the next look refuses until looks/01.md exists", rc != 0 and "SEALED" in out and looks_dir(D)[-1] == "01.txt", out)
    for bad in ("saw: todo", "saw: bass", "the bass steps down here"):
        wlook(D, 1, bad + "\n")
        rc, out = see("look", D, "0:09", "0:13", *WHY)
        check(f"looks/01.md '{bad}': refused", rc != 0 and "SEALED" in out and "saw:" in out, out)
    wlook(D, 1, "**saw:** the bass holds one note, the high line wobbles\n")
    fake = "note 001 sealed  sha256 " + "a" * 64
    rc, out = see("look", D, "9", "13", "--why", f"whether the\n{fake}  really shows")
    n1 = os.path.join(D, "looks", "01.md")
    log = slog(D)
    check("second look (times as seconds): seals looks/01.md read-only with its sha256, renders 02",
          rc == 0 and not os.access(n1, os.W_OK) and "look 01 saw sealed  sha256 " + hashlib.sha256(open(n1, "rb").read()).hexdigest()
          in log and "02-harmony.png" in looks_dir(D) and "look 02  0:09.00-0:13.00  why: whether the note 001" in log, out + log)
    rc, out = see("status", D)
    check("a --why that quotes a seal line is one log line and seals nothing: status counts 1 seal, all match",
          "seal check: 1 sealed notes, all match" in out and f"\n{fake}" not in log, out)
    os.chmod(n1, stat.S_IRUSR | stat.S_IWUSR)
    open(n1, "a").write("saw: something else later\n")
    wlook(D, 2, "saw: nothing new there\n")
    r1, o1 = see("next", D); r2, o2 = see("look", D, "0:01", "0:04", *WHY); r3, o3 = see("status", D)
    check("a sealed look note changed: next and look refuse (TAMPERED), status reports it",
          r1 != 0 and "TAMPERED" in o1 and "looks/01.md" in o1 and r2 != 0 and "TAMPERED" in o2 and "MISMATCH" in o3, o1 + o2 + o3)
    wlook(D, 1, "**saw:** the bass holds one note, the high line wobbles\n")
    shutil.copyfile(os.path.join(D, "looks", "02-harmony.png"), os.path.join(D, "looks", "03-harmony.png"))
    r1, o1 = see("next", D); r2, o2 = see("look", D, "0:01", "0:04", *WHY)
    check("a close-up file `look` never made in looks/: next and look refuse (LOOKED AHEAD), logged",
          r1 != 0 and "LOOKED AHEAD" in o1 and "03-harmony.png" in o1 and r2 != 0 and "LOOKED AHEAD" in o2
          and "`look` never made" in slog(D), o1 + o2)
    os.remove(os.path.join(D, "looks", "03-harmony.png"))
    rc, out = see("next", D)
    check("next seals the look note and the window note, reveals 001 (three pictures + text)", rc == 0
          and "look 02 saw sealed" in slog(D) and "note 000 sealed" in slog(D)
          and all(os.path.exists(os.path.join(D, "windows", f.format(k=1))) for f in NEW3), out)
    rc, out = see("next", D)
    check("the quoted seal line did not seal note 001: next still asks for notes/001.md", rc != 0 and "SEALED" in out
          and "001.md" in out, out)
    rc, out = see("look", D, "0:20", "0:24", *WHY)
    write(D, "001", GOOD)
    r1, o1 = see("after", D)
    check("third look in the newly revealed window works; after refuses until looks/03.md exists",
          rc == 0 and "Close-ups left: 0 of 3" in out and r1 != 0 and "SEALED" in o1 and "looks/03.md" in o1, out + o1)
    rc, out = see("look", D, "0:20", "0:24", *WHY)
    check("the budget is spent: a fourth look is refused", rc != 0 and "REFUSED" in out and "spent" in out
          and "04-harmony.png" not in looks_dir(D), out)
    wlook(D, 3, "saw: the same two rows\n")
    write(D, "after", "The whole song stayed on one note and the close-ups showed the same thing three times over.")
    rc, out = see("after", D)
    check("after seals the last look note, the last window note and after.md", rc == 0 and "complete" in out
          and "look 03 saw sealed" in slog(D) and "note after sealed" in slog(D), out)
    rc, out = see("status", D)
    check("status: complete, 6 seals match (3 notes, 3 look notes), close-ups 3 used, 0 left", "listen is complete" in out
          and "6 sealed notes, all match" in out and "close-ups: 3 used, 0 left of 3" in out, out)
    before = tree(D)
    rc, out = see("look", D, "0:20", "0:24", *WHY)
    check("look on a finished listen: refused, the run folder is unchanged", rc != 0 and "REFUSED" in out
          and "complete" in out and tree(D) == before, out)
    # the reason is logged before anything is rendered: a cache whose rendering fails
    W2, D2 = fake_run(root, nwin=2, name="lkfail", three=True, audio=True, looks=2)
    open(os.path.join(W2, "harmony.npz"), "w").write("not an npz file")
    see("next", D2)
    rc, out = see("look", D2, "0:04", "0:08", *WHY)
    log = slog(D2)
    check("rendering fails: the why is already in seal.log, the failure is logged, nothing is left or spent",
          rc != 0 and "could not be rendered" in out and "look 01  0:04.00-0:08.00  why: how the bass" in log
          and log.index("why: how the bass") < log.index("could not be rendered") and looks_dir(D2) == []
          and json.load(open(os.path.join(D2, "state.json")))["looks_used"] == 0, out + log)
    # a cache with nothing a close-up can be made from (and no budget in state.json: a run prepared before looks)
    W3, D3 = fake_run(root, nwin=2, name="lknone")
    see("next", D3)
    rc, out = see("look", D3, "0:04", "0:08", *WHY)
    r2, o2 = see("status", D3)
    check("a cache that can't give a close-up: refused before anything is logged; status of such a run has no "
          "close-ups line", rc != 0 and "REFUSED" in out and "look" not in slog(D3) and "close-ups" not in o2, out + o2)


def again_tests(root, D_done, W_done):
    """A second listen: its own run folder over the same cache, the harmony picture + the first note per window."""
    W, D = fake_run(root, nwin=3, name="first", audio=True)     # a two-picture first listen, cache without harmony
    D2 = os.path.join(root, "second")
    rc, out = see("again", D, "--run", D2)
    check("again on an unfinished first listen: refused, nothing created", rc != 0 and "not yet" in out
          and not os.path.exists(D2), out)
    for k in range(3):
        see("next", D)
        write(D, f"{k:03d}", GOOD + f"expect: window {k} of the first listen\n")
    write(D, "after", "The whole song moved from one place to another, and the voice carried it through the end.")
    see("next", D)
    rc, out = see("after", D)
    b_run, b_cache = tree(D), tree(W)
    rc, out = see("again", D, "--run", D2, "--looks", "1")
    st = json.load(open(os.path.join(D2, "state.json"))) if os.path.exists(os.path.join(D2, "state.json")) else {}
    a_cache = tree(W)
    check("again on a finished listen: a new run (layout second, same work, 0 revealed, looks 1), whole-song numbers "
          "not printed", rc == 0 and st.get("layout") == "second" and st.get("work") == W and st.get("revealed") == 0
          and st.get("looks") == 1 and st.get("first") == D and st.get("labels") == ["feel", "harmony", "changed"]
          and "cents" not in out and "tuning" not in out, out)
    added = sorted(set(a_cache) - set(b_cache))
    check("...it added only harmony.npz, the harmony pictures and geometry.json to the cache; every old file is "
          "byte-identical", added == ["harmony.npz", "windows/000-harmony.png", "windows/001-harmony.png",
                                      "windows/002-harmony.png", "windows/geometry.json"]
          and all(a_cache[f] == h for f, h in b_cache.items()), added)
    check("...and the first run is untouched", tree(D) == b_run, "")
    gj = json.load(open(os.path.join(W, "windows", "geometry.json")))
    check("geometry.json lists the voice, band and harmony pictures with panel boxes", sorted(gj["pictures"]) ==
          ["band", "harmony", "voice"] and all("x0" in p and "y1" in p and "id" in p for v in gj["pictures"].values()
                                               for p in v["panels"]), list(gj["pictures"]))
    rc, out = see("next", D2)
    r1, o1 = see("again", D, "--run", D2); r2, o2 = see("again", D, "--run", os.path.join(D, "x"))
    check("again refuses a run folder with a listen in it, and one inside the first listen", r1 != 0 and "REFUSED" in o1
          and r2 != 0 and "outside" in o2 and tree(D) == b_run, o1 + o2)
    wins = sorted(os.listdir(os.path.join(D2, "windows")))
    f0 = os.path.join(D2, "windows", "000-first.md")
    check("second listen, next: reveals the harmony picture, the text and a read-only copy of the first note only",
          rc == 0 and wins == ["000-first.md", "000-harmony.png", "000.txt"] and not os.access(f0, os.W_OK)
          and open(f0).read() == open(os.path.join(D, "notes", "000.md")).read() and "000-first.md" in out
          and "harmony:" in out, out + str(wins))
    write(D2, "000", GOOD)
    rc, out = see("next", D2)
    check("second listen: a first-listen note is refused (harmony: and changed: are required, voice: is not)",
          rc != 0 and "no 'harmony:' line" in out and "no 'changed:' line" in out and "voice" not in out, out)
    rc, out = see("look", D2, "0:03", "0:07", "--why", "what the low row does here")
    check("look works in a second listen", rc == 0 and "01-harmony.png" in looks_dir(D2), out)
    write(D2, "000", SECOND_GOOD)
    rc, out = see("next", D2)
    check("...and its saw: note is required before the next window", rc != 0 and "looks/01.md" in out, out)
    wlook(D2, 1, "saw: one steady row\n")
    rc, out = see("next", D2)
    check("second listen: a feel/harmony/changed note seals, 001 revealed", rc == 0 and "note 000 sealed" in slog(D2)
          and os.path.exists(os.path.join(D2, "windows", "001-first.md")), out)
    for f, src in (("002-first.md", os.path.join(D, "notes", "002.md")), ("002-harmony.png", os.path.join(W, "windows", "002-harmony.png"))):
        shutil.copyfile(src, os.path.join(D2, "windows", f))
        write(D2, "001", SECOND_GOOD + "expect: more of it\n")
        rc, out = see("next", D2)
        check(f"second listen: copied-ahead {f} is refused (LOOKED AHEAD)", rc != 0 and "LOOKED AHEAD" in out and f in out, out)
        os.remove(os.path.join(D2, "windows", f))
    n0 = os.path.join(D2, "notes", "000.md")
    os.chmod(n0, stat.S_IRUSR | stat.S_IWUSR)
    open(n0, "a").write("changed: my mind\n")
    rc, out = see("next", D2)
    check("second listen: a changed sealed note is TAMPERED", rc != 0 and "TAMPERED" in out, out)
    write(D2, "000", SECOND_GOOD)
    see("next", D2)
    write(D2, "002", SECOND_GOOD)
    write(D2, "after", "Hearing it again with the notes under it, the song felt more settled than the first time.")
    r1, o1 = see("next", D2); rc, out = see("after", D2); r3, o3 = see("status", D2)
    check("second listen: after seals and completes; status says second listen, all match", rc == 0 and "complete" in out
          and "second listen" in o3 and "listen is complete" in o3 and "all match" in o3 and "close-ups: 1 used, 0 left of 1" in o3, out + o3)
    check("the first run is still untouched after the whole second listen", tree(D) == b_run, "")
    D3 = os.path.join(root, "second-full")
    rc, out = see("again", D, "--run", D3, "--full")
    c3 = tree(W)
    r2, o2 = see("next", D3)
    wins = sorted(os.listdir(os.path.join(D3, "windows")))
    check("again --full: the cache is not rendered again; next also reveals the voice and band pictures",
          rc == 0 and c3 == a_cache and r2 == 0 and wins == ["000-band.png", "000-first.md", "000-harmony.png",
                                                             "000-voice.png", "000.txt"], out + o2 + str(wins))
    D4 = os.path.join(root, "second-none")
    before = tree(W_done)
    rc, out = see("again", D_done, "--run", D4)
    check("again on a cache without accomp.wav / band/bass.wav: refused, it never fetches or separates, nothing changed",
          rc != 0 and "never fetches" in out and not os.path.exists(D4) and tree(W_done) == before, out)


def write(D, name, text):
    p = os.path.join(D, "notes", f"{name}.md")
    if os.path.exists(p):
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    open(p, "w").write(text)


def main():
    root = tempfile.mkdtemp(prefix="see-seal-")
    try:
        W, D = fake_run(root)
        rc, out = see("next", D)
        wins = sorted(os.listdir(os.path.join(D, "windows")))
        check("next reveals exactly window 000: both pictures and the text", rc == 0
              and wins == ["000-band.png", "000-voice.png", "000.txt"] and "000-voice.png" in out
              and "000-band.png" in out, out + str(wins))
        rc, out = see("next", D)
        check("no note: refused, nothing more revealed", rc != 0 and "SEALED" in out and "band:" in out
              and len(os.listdir(os.path.join(D, "windows"))) == 3, out)
        write(D, "000", "feel: TODO\nvoice: ...\nband: tbd tbd\nwords: x\n")
        rc, out = see("next", D)
        check("placeholder note: refused with reasons", rc != 0 and "no real content" in out, out)
        write(D, "000", "feel: fine words here\nvoice: low and close\n")
        rc, out = see("next", D)
        check("missing labels: refused (band, words), expect not asked for", rc != 0 and "no 'words:' line" in out
              and "no 'band:' line" in out and "expect:' line" not in out, out)
        write(D, "000", GOOD)
        rc, out = see("next", D)
        n0 = os.path.join(D, "notes", "000.md")
        log = open(os.path.join(D, "seal.log")).read()
        check("good note without expect: sealed read-only, sha256 logged, window 001 revealed (2 pictures + text)",
              rc == 0 and not os.access(n0, os.W_OK) and "note 000 sealed  sha256" in log
              and all(os.path.exists(os.path.join(D, "windows", f.format(k=1))) for f in NEW), out + log)
        # looking ahead: a copied future window (either picture) is refused and logged
        for f in ("002-band.png", "002-voice.png"):
            shutil.copyfile(os.path.join(W, "windows", f), os.path.join(D, "windows", f))
            write(D, "001", GOOD_EXPECT)
            rc, out = see("next", D)
            check(f"copied-ahead {f}: refused (LOOKED AHEAD), logged", rc != 0 and "LOOKED AHEAD" in out and f in out
                  and "unrevealed window" in open(os.path.join(D, "seal.log")).read(), out)
            os.remove(os.path.join(D, "windows", f))
        # tampering with a sealed note
        os.chmod(n0, stat.S_IRUSR | stat.S_IWUSR)
        open(n0, "a").write("band: changed my mind later\n")
        rc, out = see("next", D)
        check("tampered note: next refuses (TAMPERED)", rc != 0 and "TAMPERED" in out
              and not os.path.exists(os.path.join(D, "windows", "002.png")), out)
        rc, out = see("status", D)
        check("status reports the mismatch", "MISMATCH" in out, out)
        write(D, "000", GOOD)  # restore the sealed content exactly
        rc, out = see("next", D)
        check("restored note: next goes on to the last window; a note with expect: also seals", rc == 0
              and "last window" in out and "note 001 sealed" in open(os.path.join(D, "seal.log")).read(), out)
        rc, out = see("after", D)
        check("after before the last note: refused", rc != 0 and "SEALED" in out, out)
        write(D, "002", GOOD)
        rc, out = see("next", D)
        check("next past the end seals the last note and asks for after.md",
              rc != 0 and "after.md" in out and "note 002 sealed" in open(os.path.join(D, "seal.log")).read(), out)
        write(D, "after", "too short")
        rc, out = see("after", D)
        check("thin after.md: refused", rc != 0 and "real words" in out, out)
        write(D, "after", "The whole song moved from one place to another, and the voice carried it through the end.")
        rc, out = see("after", D)
        check("after seals after.md and copies summary.json", rc == 0 and "complete" in out
              and os.path.exists(os.path.join(D, "summary.json")) and "note after sealed" in
              open(os.path.join(D, "seal.log")).read(), out)
        rc, out = see("status", D)
        check("status: complete, all seals match", "listen is complete" in out and "all match" in out, out)
        look_tests(root)
        again_tests(root, D, W)
        # prepare refuses a run folder that already has a listen in it
        rc, out = see("prepare", "x.wav", "--artist", "A", "--title", "T", "--run", D, "--work", W)
        check("prepare refuses a started run folder", rc != 0 and "REFUSED" in out, out)
        rc, out = see("prepare", "x.wav", "--artist", "A", "--title", "T", "--run", root, "--work", W)
        check("prepare refuses a work dir inside the run folder", rc != 0 and "outside the run folder" in out, out)
        # a run prepared before the band view: one picture per window, four labels with expect: required
        LW, LD = fake_run(root, legacy=True, name="old")
        rc, out = see("next", LD)
        wins = sorted(os.listdir(os.path.join(LD, "windows")))
        check("older run: next reveals its single picture + text", rc == 0 and wins == ["000.png", "000.txt"], out)
        write(LD, "000", GOOD)
        rc, out = see("next", LD)
        check("older run: a note without expect: is refused (its four labels stay)", rc != 0
              and "no 'expect:' line" in out and "band" not in out, out)
        write(LD, "000", LEGACY_GOOD)
        rc, out = see("next", LD)
        check("older run: its four-label note seals, 001 revealed", rc == 0
              and os.path.exists(os.path.join(LD, "windows", "001.png")), out)
        shutil.copyfile(os.path.join(LW, "windows", "002.png"), os.path.join(LD, "windows", "002.png"))
        write(LD, "001", LEGACY_GOOD)
        rc, out = see("next", LD)
        check("older run: LOOKED AHEAD still works", rc != 0 and "LOOKED AHEAD" in out, out)
        rc, out = see("status", LD)
        check("older run: status works", rc == 0 and "one picture each" in out and "all match" in out, out)
        # the intensity scale (prepare --scale): a run and its cache must agree; no key anywhere means db
        SW, SD = fake_run(root, name="scale")
        rj = os.path.join(SW, "windows", "render.json")
        json.dump(dict(window=15.0, layout="voice+band", nwin=3, scale="ear"), open(rj, "w"))
        rc, out = see("next", SD)
        check("a run without a scale (db, like every run before the switch) over a cache re-rendered on the ear scale: "
              "next refuses, nothing revealed", rc != 0 and "ear scale" in out and "--scale db" in out
              and not os.listdir(os.path.join(SD, "windows")), out)
        json.dump(dict(window=15.0, layout="voice+band", nwin=3), open(rj, "w"))
        rc, out = see("next", SD)
        check("...and over a db cache (render.json without a scale key) it goes on as before", rc == 0
              and len(os.listdir(os.path.join(SD, "windows"))) == 3, out)
        st = json.load(open(os.path.join(SD, "state.json"))); st["scale"] = "ear"
        json.dump(st, open(os.path.join(SD, "state.json"), "w"))
        write(SD, "000", GOOD)
        rc, out = see("next", SD)
        check("an ear-scale run over a db cache: next refuses and says how to render it again", rc != 0
              and "--scale ear" in out and len(os.listdir(os.path.join(SD, "windows"))) == 3, out)
        json.dump(dict(window=15.0, layout="voice+band", nwin=3, scale="ear"), open(rj, "w"))
        rc, out = see("next", SD)
        check("an ear-scale run over an ear-scale cache: next reveals", rc == 0
              and len(os.listdir(os.path.join(SD, "windows"))) == 6, out)
        # legacy run folder (all windows, no state.json)
        L = os.path.join(root, "legacy"); os.makedirs(os.path.join(L, "windows"))
        rc, out = see("status", L)
        check("status on a legacy unsealed run says so", rc != 0 and "legacy" in out, out)
    finally:
        for dp, dn, fn in os.walk(root):
            for f in fn:
                os.chmod(os.path.join(dp, f), stat.S_IRUSR | stat.S_IWUSR)
        shutil.rmtree(root, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
