"""see.py (listening v3): a song as three pictures per 15 s window: the voice and the words; the band; the notes.

Sealed listening (the normal way to use it):
  see.py prepare SOURCE --artist A --title T --run DIR [--work W] [--looks N]
                       all heavy stages; windows rendered into W (a cache outside DIR); DIR gets only state.json, notes/
                       (--scale db|ear: how the cochleagrams map intensity to colour; stored in state.json)
  see.py next DIR      seal notes/NNN.md of the window just read, then reveal exactly one more window into DIR/windows/
  see.py look DIR START END --why "..."   a close-up of 2-8 s you choose inside what is revealed, recomputed at a finer
                       step into DIR/looks/ (a budget per run; write looks/NN.md with a 'saw:' line after each)
  see.py status DIR    where the listen is, close-ups used and left, and a check of every seal
  see.py after DIR     after the last window: seal notes/after.md (the whole-song note) and copy summary.json into DIR
  see.py again DIR --run DIR2 [--looks N] [--full]   a second listen of a finished one, over the same cache: per window
                       the harmony picture, the text and your own first note (--full: the first listen's pictures too)
  see.py refcard OUT.png   known synthetic notes through the harmony picture's own code, and known synthetic voices
                       through the periodicity panel's: how to read both

Stages of prepare (each cached in WORK, skipped if its output exists and nothing it reads from ran; --redo STAGE
reruns one and every stage that depends on it; --window SECONDS sets the window, default 15):
  fetch      audio (local file, URL or ytsearch1:...) -> WORK/song.wav; LRCLIB synced lyrics -> WORK/lyrics.lrc
  separate   Demucs htdemucs two-stem -> WORK/vocals.wav, WORK/accomp.wav
  bands      Demucs htdemucs_6s on the song -> WORK/band/{drums,bass,guitar,piano,other}.wav
  lead       karaoke model (audio-separator, mel-band roformer) on the vocal stem -> WORK/lead.wav, WORK/backing.wav
  transcribe faster-whisper word timestamps on the lead stem -> WORK/whisper.json (used only for timing)
  align      each line's plain and (ad-lib) parts forced-aligned apart, each on the stem it fits best (lead, whole
             vocal or backing); whisper as a second witness -> WORK/align.json
  voices     speaker embeddings of the whole vocal, averaged per line, lines clustered -> WORK/voices.json (labels A, B, C)
  features   per-frame pitch (lead stem), loudness/HNR/flatness, backing spectrogram and pitch, per-word z-scores
  sound      gammatone cochleagrams (lead, backing, five stems; one colour scale), stereo picture, song-so-far blocks
  periodicity  summary autocorrelogram of the whole vocal stem (every period present at once, on semitone rows)
             -> WORK/periodicity.npz (measured only: no peak is picked, no voice counted)
  harmony    constant-Q of accomp.wav and of the bass stem on semitone rows (36 per octave, on the song's own tuning),
             the pitch-class fold, the bass stem's pitch -> WORK/harmony.npz (measured only: nothing is named)
  render     WORK/windows/NNN-voice.png, NNN-band.png, NNN-harmony.png and NNN.txt (sealed: revealed one window at a
             time by `next`), render.json and geometry.json (every panel's pixel box)

Usage: see README.md in this folder.
"""
import argparse, hashlib, json, os, re, shutil, stat, subprocess, sys, time, warnings
from difflib import SequenceMatcher

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # listening/ for words.py (stdlib only)
import words as lrc  # noqa: E402

warnings.filterwarnings("ignore")

# ---- every threshold and scale lives here (copied into README) ----
C = dict(
    WIN=15.0,              # seconds per window (prepare --window SECONDS; stored in state.json)
    HOP=0.01,              # feature frame step, s
    F0_FLOOR=70.0, F0_CEIL=600.0,   # Praat pitch range, Hz
    PITCH_GATE_DB=35.0,    # pitch frames quieter than (95th percentile of vocal level - this) dB are dropped
    PITCH_HPF=150.0,       # Hz; high-pass on the vocal stem before pitch tracking (removes bass bleed)
    MIN_VOICED=3,          # voiced frames a word needs before any pitch/voice-quality measure
    Z_TAG=2.5,             # |robust z| at or above which a word gets a tag
    QUAL_MIN_DUR=0.15,     # s; a word needs this duration and QUAL_MIN_VOICED voiced frames for pitch-move,
    QUAL_MIN_VOICED=5,     #    breathier and rougher tags (short words give noisy measures)
    PITCH_MOVE_ST=2.0,     # and a rise/fall tag also needs at least this many semitones across the word
    MAX_TAGS=3,            # most tags shown per word (strongest first)
    WHISPER_MODEL="medium.en",
    LINE_PAD=1.0,          # s of slack around an LRC line when looking for its whisper words
    MATCH_SIM=0.72,        # min string similarity for a lyric word to take a whisper word's time
    OFFSET_APPLY=0.3,      # apply a global LRC offset if |median(whisper - LRC)| exceeds this, s
    LINE_DISAGREE=0.5,     # a line whose first aligned word is this far from its LRC stamp is reported
    APPROX_WORD_S=0.35,    # spacing for words placed with no anchor (whisper cross-check pass only)
    CTC_PAD=1.5,           # s of audio either side of an LRC line given to the forced aligner
    MONO_MAX_PUSH=0.5,     # a lead line's window starts after the previous lead line ends, but never later than its LRC stamp + this
    CTC_MIN=0.05,          # words whose mean CTC character probability is below this, with no whisper match, are approximate
    XCHECK_MAX=0.5,        # ...as are words where the aligner and whisper (when whisper heard the word) disagree by more, s
    VOICE_STEM="vocals.wav",  # stem the voices are told apart on (the whole vocal: the lead stem's artifacts split one voice into several)
    VOICE_RATE=4,          # speaker-embedding slices per second (each 1.6 s long)
    VOICE_DIST=0.27,       # cosine distance threshold for average-linkage clustering of the per-line embeddings
    VOICE_MIN_TOTAL=10.0,  # s; a cluster smaller than this is folded into the nearest larger one
    VOICE_MIN_RUN=2,       # consecutive lines a new label needs before a change is marked
    SPEC_FMIN=60.0, SPEC_FMAX=8000.0,
    SPEC_PCT=(5, 99.5),    # global percentiles of vocal dB for the colour scale (same in every window)
    ACC_FMAX=8000.0,
    LOW_BAND=150.0,        # Hz; accompaniment low-band energy line
    FIG_W=16.0, DPI=100,
    WORD_ROWS=5,           # staggered rows in the word lane
    KARAOKE_MODEL="mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",  # lead/backing split of the vocal stem
    BACK_ACTIVE_DB=30.0,   # backing frames louder than (95th percentile of lead level - this) dB count as audible backing
    BACK_FMAX=4000.0,      # top of the backing-vocal strip, Hz
    STEM_MARGIN=0.05,      # a line is aligned on the lead stem unless the full vocal or backing stem fits it better by this
    # ---- the band view and the cochleagrams (stages bands, sound; the two pictures per window)
    BAND_MODEL="htdemucs_6s",  # Demucs six-stem model on song.wav (CPU: MPS can't run it); its vocal stem is not used
    STEMS6=("drums", "bass", "guitar", "piano", "other"),
    COCH_FMIN=50.0, COCH_FMAX=8000.0,  # gammatone centre frequencies, ERB-spaced
    COCH_N_LEAD=96, COCH_HOP_LEAD=0.005,   # channels and frame step of the lead (and whole-vocal) cochleagram
    COCH_N_STEM=64, COCH_HOP_STEM=0.01,    # channels and frame step of the backing and the five band stems
    COCH_TOP_PCT=99.5,     # the one colour scale of every cochleagram: top = max over sources of this percentile, dB,
    COCH_RANGE_DB=60.0,    #   bottom = top - this (fixed per song, so quiet stays faint and silent stays empty)
    PAN_HOP=0.05, PAN_BINS=41, PAN_FMIN=40.0, PAN_RANGE_DB=35.0,  # stereo picture: STFT frame step, pan rows, floor
    MONO_SIDE=1e-4,        # song.wav counts as mono if energy(L-R) / energy(L+R) is below this
    SOFAR_BLOCK=0.5, SOFAR_N=40, SOFAR_RANGE_DB=50.0,  # song-so-far strip: block length s, channels, floor below top
    HELD_MIN=0.7,          # s; a measured lead word's longest continuous pitch run must be this long for a close-up
    HELD_MAX=2,            # close-ups per window (longest runs first)
    HELD_SPAN=(1.5, 2.5),  # s shown per close-up: the run + 0.3 s, at least / at most
    HELD_CYCLES=5,         # periods of the waveform excerpt
    FIG_W2=15.68,          # in at DPI: both pictures are 1568 px wide (the model's long-edge cap)
    VOICE_H=11.5, BAND_H=10.5,  # in: 1568 x 1150 and 1568 x 1050 px
    MARG_L=0.075, MARG_R=0.985,  # same left/right margins in every picture, so times line up
    # ---- the harmony picture (stage harmony; the third picture per window): constant-Q on semitone rows
    HARM_SR=22050,
    HARM_BPO=36,           # constant-Q bins per octave: 3 per semitone, so bends and vibrato show between the rows
    HARM_HOP=256,          # samples at HARM_SR per constant-Q frame of a window picture (11.6 ms)
    HARM_LO=36, HARM_HI=95,  # piano roll of the accompaniment: MIDI C2..B6
    BASS_LO=24, BASS_HI=60,  # bass panel: MIDI C1..C4
    BASS_FSCALE=0.5,       # the bass panel's filters are this fraction of the constant-Q length (see README: smear)
    HARM_TOP_PCT=99.5,     # colour scale of each constant-Q panel: top = this percentile over the whole song, dB,
    HARM_RANGE_DB=50.0,    #   bottom = top - this
    CHROMA_RANGE_DB=30.0,  # the pitch-class strip's range below its own whole-song top
    BASS_F0=(30.0, 400.0),  # pYIN range on the bass stem, Hz (tracked at HARM_SR / 4)
    BASS_GATE_DB=35.0,     # bass pitch frames quieter than (95th percentile of the bass stem's level - this) are dropped
    HARM_H=7.5,            # in: 1568 x 750 px
    HARM_WORD_ROWS=2, HARM_WORD_FS=9,
    # ---- close-ups the listener chooses (`look`)
    LOOKS=6,               # close-ups per run unless prepare/again --looks N says otherwise
    LOOK_MIN=2.0, LOOK_MAX=8.0,  # seconds a close-up may span
    LOOK_PAD=1.0,          # s of audio loaded either side of the span (never past what is revealed) to settle the filters
    LOOK_WHY_WORDS=3, LOOK_SAW_WORDS=2,  # real words --why and the saw: line need
    LOOK_VOICE_H=6.5, LOOK_BAND_H=8.3,   # in: the voice and band close-ups (no lyric block, no song-so-far strip)
    LOOK_WORD_ROWS=3,
    LOOK_PYIN_HOP=64,      # samples at 22050 Hz (2.9 ms): the lead pitch re-tracked on the excerpt
    # ---- how the cochleagrams map intensity to colour (prepare --scale; stored in state.json and render.json)
    SCALE="db",            # "db": envelope power in dB, COCH_RANGE_DB under the song's top. "ear": loudness as felt:
    EAR_POWER=0.3,         #   each channel's frame power (relative to the song's dB top) to this power (Stevens), then
    EAR_TAU=0.05,          #   adaptation: minus EAR_ADAPT x the channel's own causal average over the previous EAR_TAU
    EAR_ADAPT=0.5,         #   seconds (never below 0): an onset shows in full, a held sound settles to (1 - EAR_ADAPT)
    EAR_TOP_PCT=99.5,      #   colour scale 0 .. the loudest source's whole-song percentile of that response
    # ---- the periodicity panel (stage periodicity): summary autocorrelogram of the whole vocal stem on semitone rows
    PER_SR=22050,
    PER_HOP=0.01,          # s per frame in a window picture (frame j is centred on j * PER_HOP)
    PER_FRAME=0.046,       # s; Hann frame the autocorrelation is taken over (3 periods of C2)
    PER_N=24, PER_FMIN=80.0, PER_FMAX=4000.0,  # gammatone channels (ERB-spaced) whose timing is read
    PER_COMPRESS=0.3,      # each channel's half-wave rectified output to this power before the autocorrelation
    PER_CUT=50.0,          # Hz; slower fluctuation (each channel's mean level and its drift) is left out
    PER_LO=36, PER_HI=84,  # rows: MIDI C2..C6, 3 per semitone like the harmony picture
    PER_TOP_PCT=99.5,      # colour scale 0 .. this percentile over the whole song (linear)
    PER_H=2.25, PER_GAP=0.12,  # in: the panel's height in the voice picture and the gap under it
)
MODELS_DIR = os.path.join(HERE, "audio", "_models")   # audio-separator model cache (gitignored with audio/)


def say(*a):
    print(*a, flush=True)


def jdump(obj, p):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, p)


def jload(p):
    with open(p) as f:
        return json.load(f)


def mmss(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:05.2f}"


def mmss_short(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


# ------------------------------------------------------------------ fetch
def stage_fetch(a, W):
    wav = os.path.join(W, "song.wav")
    if not os.path.exists(wav):
        src = a.source
        if os.path.exists(src):
            subprocess.run([FFMPEG, "-y", "-v", "error", "-i", src, wav], check=True)
        else:
            ytdlp = os.path.join(os.path.dirname(HERE), "venv", "bin", "yt-dlp")
            tmpl = os.path.join(W, "dl.%(ext)s")
            subprocess.run([ytdlp, "-f", "bestaudio", "-x", "--audio-format", "wav",
                            "--ffmpeg-location", os.path.dirname(FFMPEG), "-o", tmpl,
                            "--print-to-file", "%(title)s | %(duration)s | %(webpage_url)s",
                            os.path.join(W, "source.txt"), src], check=True)
            os.replace(os.path.join(W, "dl.wav"), wav)
    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", wav],
                               capture_output=True, text=True, check=True).stdout.strip())
    if not os.path.exists(os.path.join(W, "lyrics.lrc")):
        rec = lrc.lrclib(a.artist, a.title, dur)
        if not rec:
            sys.exit("no lyrics found on LRCLIB (v3 needs synced lyrics)")
        rep = lrc.store(W, rec)
        say("lyrics:", rep)
        if rep["kind"] != "synced":
            sys.exit("LRCLIB has only plain lyrics for this song; v3 needs synced lyrics")
    meta = dict(source=a.source, artist=a.artist, title=a.title, duration=dur)
    jdump(meta, os.path.join(W, "meta.json"))
    say(f"audio {dur:.2f} s")


# ------------------------------------------------------------------ separate
def stage_separate(a, W):
    out = os.path.join(W, "sep")
    env = dict(os.environ, PATH=os.path.dirname(FFMPEG) + ":" + os.environ.get("PATH", ""))
    subprocess.run([sys.executable, "-m", "demucs", "-n", "htdemucs", "--two-stems=vocals", "-d", "cpu",
                    "-o", out, os.path.join(W, "song.wav")], check=True, env=env)
    d = os.path.join(out, "htdemucs", "song")
    os.replace(os.path.join(d, "vocals.wav"), os.path.join(W, "vocals.wav"))
    os.replace(os.path.join(d, "no_vocals.wav"), os.path.join(W, "accomp.wav"))
    shutil.rmtree(out, ignore_errors=True)


# ------------------------------------------------------------------ bands
def stage_bands(a, W):
    """Demucs six-stem model on the whole song: drums, bass, guitar, piano, other -> WORK/band/<name>.wav (its own
    vocal stem is dropped: the voice comes from the two-stem split and the karaoke model). CPU only (on MPS the model
    fails: 'Output channels > 65536'). A finished raw output in WORK/sep6 is reused."""
    out = os.path.join(W, "sep6")
    d = os.path.join(out, C["BAND_MODEL"], "song")
    if not all(os.path.exists(os.path.join(d, f"{n}.wav")) for n in C["STEMS6"]):
        env = dict(os.environ, PATH=os.path.dirname(FFMPEG) + ":" + os.environ.get("PATH", ""))
        subprocess.run([sys.executable, "-m", "demucs", "-n", C["BAND_MODEL"], "-d", "cpu", "-o", out,
                        os.path.join(W, "song.wav")], check=True, env=env)
    os.makedirs(os.path.join(W, "band"), exist_ok=True)
    for n in C["STEMS6"]:
        os.replace(os.path.join(d, f"{n}.wav"), os.path.join(W, "band", f"{n}.wav"))
    shutil.rmtree(out, ignore_errors=True)


# ------------------------------------------------------------------ lead
def stage_lead(a, W):
    """Split the Demucs vocal stem into lead and backing with a karaoke model (audio-separator). The model's
    'Vocals' output is the lead, its 'Instrumental' output (everything that isn't the lead) is the backing.
    With --no-split (or KARAOKE_MODEL None) lead.wav is a copy of vocals.wav and backing.wav is silence."""
    import soundfile as sf
    voc, sr = sf.read(os.path.join(W, "vocals.wav"), always_2d=True)
    if getattr(a, "no_split", False) or not C["KARAOKE_MODEL"]:
        sf.write(os.path.join(W, "backing.wav"), np.zeros_like(voc), sr)
        shutil.copyfile(os.path.join(W, "vocals.wav"), os.path.join(W, "lead.wav"))
        jdump(dict(model=None), os.path.join(W, "lead.json"))
        return
    out = os.path.join(W, "kara")
    os.makedirs(out, exist_ok=True)
    exe = os.path.join(os.path.dirname(sys.executable), "audio-separator")
    env = dict(os.environ, PATH=os.path.dirname(FFMPEG) + ":" + os.environ.get("PATH", ""))
    with open(os.path.join(out, "log.txt"), "w") as lg:
        subprocess.run([exe, os.path.join(W, "vocals.wav"), "-m", C["KARAOKE_MODEL"], "--model_file_dir", MODELS_DIR,
                        "--output_dir", out, "--output_format", "WAV"], check=True, env=env, stdout=lg, stderr=lg)
    import glob
    lead_f = glob.glob(os.path.join(out, "*(Vocals)*.wav"))[0]
    back_f = glob.glob(os.path.join(out, "*(Instrumental)*.wav"))[0]
    for src, dst in ((lead_f, "lead.wav"), (back_f, "backing.wav")):
        y, sr2 = sf.read(src, always_2d=True)
        if sr2 != sr:
            sys.exit(f"karaoke output at {sr2} Hz, vocal stem at {sr} Hz")
        y = y[:len(voc)] if len(y) >= len(voc) else np.pad(y, ((0, len(voc) - len(y)), (0, 0)))
        sf.write(os.path.join(W, dst), y, sr)
    shutil.rmtree(out, ignore_errors=True)
    L = load_mono(os.path.join(W, "lead.wav"), 16000); B = load_mono(os.path.join(W, "backing.wav"), 16000)
    share = float(np.sum(L ** 2) / (np.sum(L ** 2) + np.sum(B ** 2) + 1e-12))
    jdump(dict(model=C["KARAOKE_MODEL"], lead_energy_share=round(share, 3)), os.path.join(W, "lead.json"))
    say(f"lead/backing split: lead holds {share:.0%} of the vocal energy")


def has_split(W):
    """Was the vocal stem split into lead and backing (not --no-split, not an older work dir)?"""
    p = os.path.join(W, "lead.json")
    return os.path.exists(p) and bool(jload(p).get("model"))


def lead_wav(W):
    """The stem the lead voice is measured on: lead.wav after the split, vocals.wav for runs made without it."""
    p = os.path.join(W, "lead.wav")
    return p if os.path.exists(p) else os.path.join(W, "vocals.wav")


# ------------------------------------------------------------------ transcribe
def stage_transcribe(a, W):
    from faster_whisper import WhisperModel
    m = WhisperModel(C["WHISPER_MODEL"], device="cpu", compute_type="int8")
    y16 = load_mono(lead_wav(W), 16000).astype(np.float32)  # array in: avoids PyAV version clashes
    segs, info = m.transcribe(y16, language="en", word_timestamps=True,
                              vad_filter=True, condition_on_previous_text=False, beam_size=5)
    out = []
    for s in segs:
        for w in (s.words or []):
            out.append(dict(w=w.word.strip(), s=round(w.start, 3), e=round(w.end, 3), p=round(w.probability, 3)))
    jdump(dict(model=C["WHISPER_MODEL"], words=out), os.path.join(W, "whisper.json"))
    say(f"whisper: {len(out)} words")


# ------------------------------------------------------------------ align
def norm(tok):
    t = tok.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9']", "", t).replace("'", "")
    return t


VOWELS = re.compile(r"[aeiouy]+")


def syllables(n):
    if not n:
        return 1
    if n.isdigit():
        return max(1, len(n))
    k = len(VOWELS.findall(n))
    if n.endswith("e") and not n.endswith("le") and k > 1:
        k -= 1
    return max(1, k)


def tokenize_line(text):
    """[(display, norm, in_parens)] for one LRC line; punctuation-only tokens are dropped."""
    out, depth = [], 0
    for raw in text.split():
        opens = raw.count("(")
        closes = raw.count(")")
        inside = depth > 0 or opens > 0
        depth = max(0, depth + opens - closes)
        n = norm(raw)
        if n:
            out.append((raw, n, inside))
    return out


def sim(a, b):
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def match_line(lw, ww, min_sim):
    """Monotonic weighted alignment of lyric norms lw to whisper norms ww. Returns {i_lyric: j_whisper}."""
    n, m = len(lw), len(ww)
    if not n or not m:
        return {}
    S = np.zeros((n + 1, m + 1))
    B = np.zeros((n + 1, m + 1), dtype=np.int8)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, b = S[i - 1, j], 1
            if S[i, j - 1] > best:
                best, b = S[i, j - 1], 2
            s = sim(lw[i - 1], ww[j - 1])
            if s >= min_sim and S[i - 1, j - 1] + s > best:
                best, b = S[i - 1, j - 1] + s, 3
            S[i, j], B[i, j] = best, b
    i, j, res = n, m, {}
    while i > 0 and j > 0:
        if B[i, j] == 3:
            res[i - 1] = j - 1; i -= 1; j -= 1
        elif B[i, j] == 1:
            i -= 1
        else:
            j -= 1
    return res


def run_align(lines, ww, offset, dur):
    """One pass over all lines. Returns (words, line_stats)."""
    used_upto = -1
    words, stats = [], []
    for li, (t, text) in enumerate(lines):
        t0 = t + offset
        t1 = (lines[li + 1][0] + offset) if li + 1 < len(lines) else dur
        toks = tokenize_line(text)
        cand = [j for j, w in enumerate(ww) if j > used_upto and t0 - C["LINE_PAD"] <= w["s"] < t1 + C["LINE_PAD"]]
        mp = match_line([x[1] for x in toks], [norm(ww[j]["w"]) for j in cand], C["MATCH_SIM"])
        mp = {i: cand[j] for i, j in mp.items()}
        if mp:
            used_upto = max(used_upto, max(mp.values()))
        lw = []
        for i, (disp, n, par) in enumerate(toks):
            if i in mp:
                w = ww[mp[i]]
                lw.append(dict(text=disp, norm=n, adlib=par, line=li, s=w["s"], e=max(w["e"], w["s"] + 0.05),
                               approx=False, wp=w["p"]))
            else:
                lw.append(dict(text=disp, norm=n, adlib=par, line=li, s=None, e=None, approx=True, wp=None))
        # fill gaps between anchors
        idx = [i for i, w in enumerate(lw) if not w["approx"]]
        k = 0
        while k < len(lw):
            if not lw[k]["approx"]:
                k += 1; continue
            g0 = k
            while k < len(lw) and lw[k]["approx"]:
                k += 1
            g1 = k  # gap is [g0, g1)
            ng = g1 - g0
            left = lw[g0 - 1]["e"] if g0 > 0 else None
            right = lw[g1]["s"] if g1 < len(lw) else None
            if left is None and right is None:
                a_, b_ = t0, min(t1, t0 + ng * C["APPROX_WORD_S"])
            elif left is None:
                a_, b_ = max(t0 - C["LINE_PAD"], right - ng * C["APPROX_WORD_S"]), right
                a_ = min(a_, right - 0.05 * ng)
            elif right is None:
                a_, b_ = left, min(max(t1, left + 0.05 * ng), left + ng * C["APPROX_WORD_S"])
            else:
                a_, b_ = left, max(right, left + 0.05 * ng)
            step = (b_ - a_) / ng
            for q in range(ng):
                lw[g0 + q]["s"] = round(a_ + q * step, 3)
                lw[g0 + q]["e"] = round(a_ + (q + 1) * step, 3)
        first = lw[idx[0]]["s"] if idx else None
        stats.append(dict(line=li, t_lrc=round(t0, 3), n=len(lw), matched=len(idx),
                          first_aligned=first, disagree=(round(first - t0, 3) if first is not None and idx[0] == 0 else None)))
        words += lw
    return words, stats


NUM = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
      "seventeen eighteen nineteen twenty".split()


def spell(n, d):
    """Characters of a normalized word that the CTC aligner knows; numbers are spelled out."""
    if n.isdigit():
        n = NUM[int(n)] if int(n) < len(NUM) else "".join(NUM[int(c)] for c in n)
    return [c for c in n if c in d]


def ctc_lines(W, lines, offset, dur):
    """Forced alignment of each LRC line's own text (wav2vec2 MMS_FA, with a '*' token at both ends to absorb
    neighbouring speech), in a window of the LRC line +/- CTC_PAD. Each line is aligned on the lead stem, the full
    vocal stem and the backing stem, and keeps the one where its text fits best (highest mean word score; the lead
    wins ties within STEM_MARGIN): a call-and-response or shout-out line sung by a backing voice is found where it
    is. Returns per line (list of (start, end, score) per word, stem name)."""
    import torch, torchaudio
    import torchaudio.functional as AF
    b = torchaudio.pipelines.MMS_FA
    model = b.get_model(with_star=True).eval()
    d = b.get_dict(star="*")
    ys = {"lead": torch.from_numpy(load_mono(lead_wav(W), 16000)).float(),
          "vocals": torch.from_numpy(load_mono(os.path.join(W, "vocals.wav"), 16000)).float()}
    bp = os.path.join(W, "backing.wav")
    if os.path.exists(bp) and has_split(W):
        ys["backing"] = torch.from_numpy(load_mono(bp, 16000)).float()
    else:
        ys.pop("vocals")  # no split: the lead is the vocal stem

    def one(y, a_, b_, chars):
        flat = [d["*"]] + [d[c] for cs in chars for c in cs] + [d["*"]]
        seg = y[int(a_ * 16000):int(b_ * 16000)]
        with torch.inference_mode():
            em, _ = model(seg[None])
            em = torch.log_softmax(em, dim=-1)
        T = em.shape[1]
        if T < len(flat) + 2:
            return [None] * len(chars), -1.0
        ali, sc = AF.forced_align(em, torch.tensor([flat], dtype=torch.int32), blank=0)
        spans = AF.merge_tokens(ali[0], sc[0].exp())[1:-1]  # drop the two stars
        ratio = seg.shape[0] / T / 16000.0
        res, k = [], 0
        for cs in chars:
            if not cs:
                res.append(None); continue
            sp = spans[k:k + len(cs)]; k += len(cs)
            res.append((round(a_ + sp[0].start * ratio, 3), round(a_ + sp[-1].end * ratio, 3),
                        float(np.mean([x.score for x in sp]))))
        sc_ = [r[2] for r in res if r is not None]
        return res, (float(np.mean(sc_)) if sc_ else -1.0)

    out = []
    prev_end = -1e9  # end of the previous lead line: lead lines may not overlap each other
    for li, (t, text) in enumerate(lines):
        t0 = t + offset
        t1 = (lines[li + 1][0] + offset) if li + 1 < len(lines) else min(dur, t0 + 6.0)
        toks = tokenize_line(text)
        chars = [spell(n, d) for _, n, _ in toks]
        a_free = t0 - C["CTC_PAD"]
        a_lead = max(a_free, min(prev_end - 0.05, t0 + C["MONO_MAX_PUSH"]))
        # a line mixing lead words and (ad-lib) words: the two parts often overlap in time (a backing voice answering
        # the lead), which a single sequential alignment cannot represent, so each part is aligned on its own
        lead_i = [i for i, x in enumerate(toks) if not x[2]]
        adl_i = [i for i, x in enumerate(toks) if x[2]]
        res_all, stems, wstem = [None] * len(toks), [], [None] * len(toks)
        for idx, is_adlib in ((lead_i, False), (adl_i, True)):
            if not idx:
                continue
            best = None
            for name, y in ys.items():
                a_ = a_free if (name == "backing" or is_adlib) else a_lead
                a_, b_ = max(0.0, a_), min(dur, max(t1 + C["CTC_PAD"], a_ + 1.0))
                res, score = one(y, a_, b_, [chars[i] for i in idx])
                score += C["STEM_MARGIN"] if (name == "lead" and not is_adlib) else 0.0
                if best is None or score > best[1]:
                    best = (res, score, name)
            for i, r in zip(idx, best[0]):
                res_all[i] = r; wstem[i] = best[2]
            stems.append(best[2])
            if not is_adlib and best[2] != "backing":
                ends = [r[1] for r in best[0] if r is not None]
                if ends:
                    prev_end = max(ends)
        out.append((res_all, stems[0] if len(stems) == 1 else "+".join(stems), wstem))
    return out


def stage_align(a, W):
    meta = jload(os.path.join(W, "meta.json"))
    lines = lrc.parse_lrc(open(os.path.join(W, "lyrics.lrc")).read())
    ww = jload(os.path.join(W, "whisper.json"))["words"]
    dur = meta["duration"]
    # 1. whisper cross-check pass with wide slack: measures the global LRC offset from matched line-initial words
    keep = C["LINE_PAD"]
    C["LINE_PAD"] = 3.0
    _, st = run_align(lines, ww, 0.0, dur)
    C["LINE_PAD"] = keep
    d = [s["disagree"] for s in st if s["disagree"] is not None]
    med = float(np.median(d)) if d else 0.0
    offset = round(med, 3) if abs(med) > C["OFFSET_APPLY"] else 0.0
    if a.lyrics_offset is not None:
        offset = a.lyrics_offset
    wwords, st = run_align(lines, ww, offset, dur)
    # 2. CTC forced alignment of the real text; whisper times are only a cross-check
    ctc = ctc_lines(W, lines, offset, dur)
    words, k = [], 0
    diffs = []
    for li, (res, _, wstem) in enumerate(ctc):
        for r, stem in zip(res, wstem):
            w = dict(wwords[k]); k += 1
            w["stem"] = stem  # lead / vocals / backing: where this word's text fit best
            whisper_s = None if w["approx"] else w["s"]
            why = []
            if r is None:
                why.append("no alignable letters")
            else:
                w["s"], w["e"] = r[0], max(r[1], r[0] + 0.04)
                w["ctc"] = round(r[2], 3)
                if r[2] < C["CTC_MIN"] and whisper_s is None:  # a whisper match within XCHECK_MAX is a second witness
                    why.append(f"low aligner confidence {r[2]:.2f}, not confirmed by whisper")
            if whisper_s is not None and r is not None:
                dd = r[0] - whisper_s
                diffs.append(dd)
                if abs(dd) > C["XCHECK_MAX"]:
                    why.append(f"aligner and whisper disagree by {dd:+.2f} s")
            w["whisper_s"] = whisper_s
            w["approx"] = bool(why)
            w["why"] = "; ".join(why)
            words.append(w)
    assert k == len(wwords)
    n = len(words); m = sum(not w["approx"] for w in words)
    ad = np.abs(diffs) if diffs else np.array([np.nan])
    dis = [abs(s["disagree"]) for s in st if s["disagree"] is not None]
    rep = dict(offset_measured=round(med, 3), offset_applied=offset, lines=len(lines), words=n,
               exact=m, exact_rate=round(m / max(1, n), 3),
               whisper_matched=sum(w["whisper_s"] is not None for w in words),
               whisper_match_rate=round(sum(w["whisper_s"] is not None for w in words) / max(1, n), 3),
               ctc_vs_whisper_median_abs_s=round(float(np.median(ad)), 3),
               ctc_vs_whisper_over_0_5s=int((ad > 0.5).sum()), ctc_vs_whisper_over_0_25s=int((ad > 0.25).sum()),
               low_confidence=sum("low aligner" in w["why"] for w in words),
               lines_by_stem={n: sum(1 for x in ctc if x[1] == n) for n in sorted(set(x[1] for x in ctc))},
               words_on_backing=sum(st_ == "backing" for x in ctc for st_ in x[2]),
               lrc_lines_first_word_off_over_0_5s=sum(x > C["LINE_DISAGREE"] for x in dis))
    jdump(dict(report=rep, lines=[dict(t=t, text=x) for t, x in lines], line_stats=st, words=words),
          os.path.join(W, "align.json"))
    say("align:", rep)


# ------------------------------------------------------------------ audio helpers
def load_mono(p, sr):
    import librosa
    y, _ = librosa.load(p, sr=sr, mono=True)
    return y


# ------------------------------------------------------------------ voices
def stage_voices(a, W):
    """Speaker embeddings (Resemblyzer) on overlapping 1.6 s slices of the whole vocal stem, 4 per second. Each lyric
    line gets the mean embedding of the slices inside the span of its exact words, and the LINES are clustered
    (average linkage, cosine, VOICE_DIST): averaging over a line removes most of the slice-to-slice noise that
    instrument bleed and sung vowels cause. Clusters holding under VOICE_MIN_TOTAL seconds of lines are folded into
    the nearest larger one, and a change needs VOICE_MIN_RUN lines in a row. Labels are letters in order of first
    appearance, nothing more."""
    from resemblyzer import VoiceEncoder
    from scipy.cluster.hierarchy import linkage, fcluster
    al = jload(os.path.join(W, "align.json"))
    y = load_mono(os.path.join(W, C["VOICE_STEM"]), 16000).astype(np.float32)
    enc = VoiceEncoder("cpu", verbose=False)
    _, P, sl = enc.embed_utterance(y, return_partials=True, rate=C["VOICE_RATE"], min_coverage=0.5)
    ct = np.array([(x.start + x.stop) / 2 / 16000 for x in sl])
    by_line = {}
    for w in al["words"]:
        by_line.setdefault(w["line"], []).append(w)
    segs, E, dur_ = [], [], []
    for li in sorted(by_line):
        ws = by_line[li]
        ex = [w for w in ws if not w["approx"]] or ws
        s_, e_ = min(w["s"] for w in ex), max(w["e"] for w in ex)
        m = (ct >= s_) & (ct <= e_)
        if not m.any():
            m = np.abs(ct - (s_ + e_) / 2) == np.abs(ct - (s_ + e_) / 2).min()
        v = P[m].mean(0)
        E.append(v / np.linalg.norm(v)); dur_.append(max(0.5, e_ - s_))
        segs.append(dict(line=li, s=min(w["s"] for w in ws), e=max(w["e"] for w in ws)))
    E, dur_ = np.array(E), np.array(dur_)
    lab = fcluster(linkage(E, method="average", metric="cosine"), t=C["VOICE_DIST"], criterion="distance") \
        if len(E) > 1 else np.ones(len(E), int)
    sizes = {l: float(dur_[lab == l].sum()) for l in set(lab)}
    big = [l for l in sizes if sizes[l] >= C["VOICE_MIN_TOTAL"]] or [max(sizes, key=sizes.get)]
    cents = {l: E[lab == l].mean(0) for l in big}
    cents = {l: c / np.linalg.norm(c) for l, c in cents.items()}
    folded = 0
    for i in range(len(lab)):
        if lab[i] not in cents:
            lab[i] = max(cents, key=lambda l: float(np.dot(E[i], cents[l]))); folded += 1
    seq = [int(x) for x in lab]
    share = [float(np.dot(E[i], cents[lab[i]])) for i in range(len(lab))]  # similarity to its voice's centre
    sm, flips = list(seq), 0
    for i in range(1, len(sm)):
        if sm[i] != sm[i - 1]:
            run = 1
            while i + run < len(seq) and seq[i + run] == seq[i]:
                run += 1
            if run < C["VOICE_MIN_RUN"]:
                sm[i] = sm[i - 1]; flips += 1
    names, out = {}, []
    for i, g in enumerate(segs):
        if sm[i] not in names:
            names[sm[i]] = "ABCDEFGH"[len(names)]
        out.append(dict(line=g["line"], s=round(g["s"], 3), e=round(g["e"], 3), voice=names[sm[i]],
                        raw=seq[i], sim=round(share[i], 2)))
    changes, seen = [], set()
    for i, o in enumerate(out):
        if i == 0 or o["voice"] != out[i - 1]["voice"]:
            changes.append(dict(t=o["s"], line=o["line"], voice=o["voice"], first=(o["voice"] not in seen)))
            seen.add(o["voice"])
    rep = dict(slices=int(len(P)), lines=len(segs), clusters_raw=len(sizes), clusters_kept=len(big),
               lines_folded=folded, voices=len(names), changes=len(changes) - 1, single_line_flips_smoothed=flips)
    jdump(dict(report=rep, lines=out, changes=changes), os.path.join(W, "voices.json"))
    say("voices:", rep)


# ------------------------------------------------------------------ features
def measured(w):
    """A word gets measures, tags and a place in the baseline only if its time is exact and it was found on the lead
    (or the whole vocal) stem; a word whose text fit the backing stem best is a backing voice: shown, not measured."""
    return not w["approx"] and w.get("stem") != "backing"


def robust_z(x):
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    z = np.full_like(x, np.nan)
    if ok.sum() < 5:
        return z
    med = np.median(x[ok])
    mad = 1.4826 * np.median(np.abs(x[ok] - med))
    if mad <= 1e-9:
        mad = np.std(x[ok]) or 1.0
    z[ok] = (x[ok] - med) / mad
    return z


def stage_features(a, W):
    import librosa, parselmouth
    from scipy.signal import butter, sosfiltfilt
    sr = 22050
    hop = int(C["HOP"] * sr)  # 220 samples ~ 9.98 ms
    n_fft = 2048
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    keep = (freqs >= C["SPEC_FMIN"] * 0.9) & (freqs <= C["SPEC_FMAX"])
    bkeep = (freqs >= C["SPEC_FMIN"] * 0.9) & (freqs <= C["BACK_FMAX"])

    def hpf(x):
        # pitch is tracked on a high-passed copy: Demucs leaves bass/808 bleed below ~150 Hz in the vocal stem, which
        # the tracker otherwise locks onto; the voice's own fundamental is still found from its harmonics' periodicity
        return sosfiltfilt(butter(4, C["PITCH_HPF"], btype="highpass", fs=sr, output="sos"), x)

    def pyin(x, name):
        # pYIN (HMM-smoothed); it makes far fewer octave-down errors here than Praat's AC
        cache = os.path.join(W, f"pyin_{name}_{int(C['F0_FLOOR'])}_{int(C['F0_CEIL'])}_hp{int(C['PITCH_HPF'])}.npz")
        if os.path.exists(cache):
            return np.load(cache)["f0"]
        f, _, _ = librosa.pyin(x.astype(np.float32), fmin=C["F0_FLOOR"], fmax=C["F0_CEIL"], sr=sr,
                               frame_length=2048, hop_length=hop, center=True)
        np.savez_compressed(cache, f0=f)
        return f

    def level(y):
        return 20 * np.log10(librosa.feature.rms(y=y, frame_length=2048, hop_length=hop, center=True)[0] + 1e-6)

    def on_grid(ft, pt, f0):
        g = np.interp(ft, pt, np.nan_to_num(f0, nan=-1), left=-1, right=-1)
        g[~(np.interp(ft, pt, np.isfinite(f0).astype(float), left=0, right=0) > 0.5)] = np.nan
        return g

    def voice_frames(path, pitch=True):
        """Per-frame pitch (gated by level), HNR, level, flatness and spectrogram of one vocal stem."""
        name = os.path.splitext(os.path.basename(path))[0]
        y = load_mono(path, sr)
        db = level(y)
        ft = np.arange(len(db)) * hop / sr
        f0 = pyin(hpf(y), name) if pitch else np.full(len(db), np.nan)
        pt = np.arange(len(f0)) * hop / sr
        f0g = on_grid(ft, pt, f0)
        f0g[db[:len(f0g)] < np.percentile(db, 95) - C["PITCH_GATE_DB"]] = np.nan  # pYIN also "finds" pitch in near-silence
        hn = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr).to_harmonicity_cc(time_step=C["HOP"], minimum_pitch=75.0)
        hv = hn.values[0].copy(); hv[hv < -100] = np.nan
        hng = np.interp(ft, hn.xs(), np.nan_to_num(hv, nan=-999), left=-999, right=-999)
        hng[hng < -100] = np.nan
        flat = librosa.feature.spectral_flatness(y=y, n_fft=2048, hop_length=hop, center=True)[0]
        Sdb = librosa.power_to_db(np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop)) ** 2, ref=1.0)[keep]
        return y, dict(ft=ft, pt=pt, f0=f0g, hnr=hng, db=db, flat=flat, spec=Sdb)

    al = jload(os.path.join(W, "align.json"))
    y, L = voice_frames(lead_wav(W))
    ft, pt = L["ft"], L["pt"]
    lead95 = np.percentile(L["db"], 95)
    # The "main voice": the lead stem, except over line parts whose text fit the whole vocal stem better (the karaoke
    # model can move the main singer, or one of a doubled pair, into "backing"); there the whole vocal is used, for
    # the picture and for the measures, so each word is measured where it was found.
    use_full = np.zeros(len(ft), bool)
    if has_split(W):
        parts = {}
        for w in al["words"]:
            if w.get("stem") == "vocals":
                k_ = (w["line"], w["adlib"])
                parts[k_] = (min(parts.get(k_, (1e9, 0))[0], w["s"]), max(parts.get(k_, (0, -1e9))[1], w["e"]))
        for s_, e_ in parts.values():
            use_full[(ft >= s_ - 0.1) & (ft <= e_ + 0.1)] = True
    if use_full.any():
        _, V = voice_frames(os.path.join(W, "vocals.wav"), pitch=False)
        # pitch stays the lead stem's everywhere: on the whole vocal pYIN can lock onto a low instrument tone
        # leaking into the vocal stem (Trapeze Swinger: ~65-90 Hz), and a word must never mix two pitch sources
        for key in ("hnr", "db", "flat"):
            n_ = min(len(L[key]), len(V[key]), len(ft))
            L[key][:n_][use_full[:n_]] = V[key][:n_][use_full[:n_]]
        n_ = min(L["spec"].shape[1], V["spec"].shape[1], len(ft))
        L["spec"][:, :n_][:, use_full[:n_]] = V["spec"][:, :n_][:, use_full[:n_]]
    f0g, hng, db, flat, Sdb = L["f0"], L["hnr"], L["db"], L["flat"], L["spec"]
    # backing (everything in the vocal stem that the karaoke model did not call lead): spectrogram on the lead's
    # colour scale, its own pitch line, and where it is audible (level within BACK_ACTIVE_DB of the lead's peak)
    bpath = os.path.join(W, "backing.wav")
    yb = load_mono(bpath, sr) if os.path.exists(bpath) else np.zeros_like(y)
    yb = yb[:len(y)] if len(yb) >= len(y) else np.pad(yb, (0, len(y) - len(yb)))
    back_on = level(yb) >= lead95 - C["BACK_ACTIVE_DB"]
    if back_on.any():
        bf0g = on_grid(ft, pt, pyin(hpf(yb), "backing"))
        bf0g[~back_on[:len(bf0g)]] = np.nan
    else:
        bf0g = np.full_like(ft, np.nan)
    Sb = librosa.power_to_db(np.abs(librosa.stft(yb, n_fft=n_fft, hop_length=hop)) ** 2, ref=1.0)[bkeep]
    ya = load_mono(os.path.join(W, "accomp.wav"), sr)
    hop_a = hop * 2
    M = librosa.feature.melspectrogram(y=ya, sr=sr, n_fft=2048, hop_length=hop_a, n_mels=64, fmin=30, fmax=C["ACC_FMAX"])
    Mdb = librosa.power_to_db(M, ref=1.0)
    Sa = np.abs(librosa.stft(ya, n_fft=4096, hop_length=hop_a)) ** 2
    fa = librosa.fft_frequencies(sr=sr, n_fft=4096)
    low = 10 * np.log10(Sa[fa <= C["LOW_BAND"]].sum(0) + 1e-10)
    onset = librosa.onset.onset_strength(y=ya, sr=sr, hop_length=hop_a)
    np.savez_compressed(os.path.join(W, "features.npz"), ft=ft, f0=f0g, hnr=hng, db=db, flat=flat,
                        spec=Sdb.astype(np.float16), spec_f=freqs[keep], use_full=use_full,
                        bspec=Sb[:, ::2].astype(np.float16), bspec_f=freqs[bkeep], bspec_t=ft[::2][:Sb[:, ::2].shape[1]],
                        bf0=bf0g, back_on=back_on[:len(ft)],
                        acc=Mdb.astype(np.float16), acc_f=librosa.mel_frequencies(n_mels=64, fmin=30, fmax=C["ACC_FMAX"]),
                        acc_t=np.arange(Mdb.shape[1]) * hop_a / sr, low=low, onset=onset)
    # ---- per word
    vo = jload(os.path.join(W, "voices.json"))
    line_voice = {o["line"]: o["voice"] for o in vo["lines"]}
    ws = al["words"]
    rows = []
    for w in ws:
        i0, i1 = np.searchsorted(ft, w["s"]), max(np.searchsorted(ft, w["e"]), np.searchsorted(ft, w["s"]) + 1)
        f = f0g[i0:i1]  # always the lead stem's pitch
        v = np.isfinite(f)
        nv = int(v.sum())
        r = dict(voiced=nv, frames=int(i1 - i0))
        r["db"] = float(np.max(db[i0:i1])) if i1 > i0 else np.nan  # peak loudness of the word
        r["dur_syl"] = (w["e"] - w["s"]) / syllables(w["norm"])
        if nv >= C["MIN_VOICED"]:
            st = 12 * np.log2(f[v] / 100.0)
            tt = ft[i0:i1][v]
            r["st"] = float(np.median(st))
            if tt[-1] - tt[0] > 0.04:
                k = np.polyfit(tt, st, 1)[0]
                r["move"] = float(k * (tt[-1] - tt[0]))
            else:
                r["move"] = np.nan
            h = hng[i0:i1][v]
            r["hnr"] = float(np.nanmean(h)) if np.isfinite(h).any() else np.nan
            r["flat"] = float(np.mean(flat[i0:i1][v]))
        else:
            r["st"] = r["move"] = r["hnr"] = r["flat"] = np.nan
        r["voice"] = line_voice.get(w["line"], "A")
        rows.append(r)
    keys = ["db", "dur_syl", "st", "move", "hnr", "flat"]
    Z = {k: np.full(len(rows), np.nan) for k in keys}
    for vname in sorted(set(r["voice"] for r in rows)):
        idx = [i for i, r in enumerate(rows) if r["voice"] == vname and measured(ws[i])]
        if len(idx) < 30:  # too few exact words for a baseline of its own: use the whole song
            idx_b = [i for i, r in enumerate(rows) if measured(ws[i])]
        else:
            idx_b = idx
        tgt = [i for i, r in enumerate(rows) if r["voice"] == vname]
        for k in keys:
            base = np.array([rows[i][k] for i in idx_b], float)
            ok = np.isfinite(base)
            if ok.sum() < 5:
                continue
            med = np.median(base[ok]); mad = 1.4826 * np.median(np.abs(base[ok] - med)) or (np.std(base[ok]) or 1.0)
            for i in tgt:
                Z[k][i] = (rows[i][k] - med) / mad
    out = []
    for i, (w, r) in enumerate(zip(ws, rows)):
        z = {k: (None if not np.isfinite(Z[k][i]) else round(float(Z[k][i]), 2)) for k in keys}
        tags = []
        if measured(w):
            T = C["Z_TAG"]
            if z["db"] is not None and abs(z["db"]) >= T:
                tags.append(("louder" if z["db"] > 0 else "quieter", abs(z["db"])))
            if z["st"] is not None and abs(z["st"]) >= T:
                tags.append(("higher" if z["st"] > 0 else "lower", abs(z["st"])))
            if z["dur_syl"] is not None and abs(z["dur_syl"]) >= T:
                tags.append(("longer" if z["dur_syl"] > 0 else "faster", abs(z["dur_syl"])))
            qual = (w["e"] - w["s"]) >= C["QUAL_MIN_DUR"] and r["voiced"] >= C["QUAL_MIN_VOICED"]
            if qual and z["move"] is not None and abs(z["move"]) >= T and abs(r["move"]) >= C["PITCH_MOVE_ST"]:
                tags.append(("pitch rising" if r["move"] > 0 else "pitch falling", abs(z["move"])))
            br = qual and z["flat"] is not None and z["flat"] >= T
            ro = qual and z["hnr"] is not None and z["hnr"] <= -T
            if br:
                tags.append(("breathier", z["flat"]))
            elif ro:
                tags.append(("rougher", -z["hnr"]))
        tags.sort(key=lambda x: -x[1])
        tags = [t for t, _ in tags[:C["MAX_TAGS"]]]
        out.append(dict(w, voice=r["voice"], voiced=r["voiced"], z=z,
                        raw=dict(db=round(r["db"], 1) if np.isfinite(r["db"]) else None,
                                 st=round(r["st"], 1) if np.isfinite(r["st"]) else None,
                                 move=round(r["move"], 1) if np.isfinite(r["move"]) else None,
                                 hnr=round(r["hnr"], 1) if np.isfinite(r["hnr"]) else None),
                        tags=tags))
    vf = float(np.isfinite(f0g[db > np.percentile(db, 40)]).mean())
    rep = dict(words=len(out), tagged=sum(bool(o["tags"]) for o in out),
               words_with_pitch=sum(o["voiced"] >= C["MIN_VOICED"] for o in out),
               voiced_frac_active=round(vf, 3), main_from_whole_vocal=round(float(use_full.mean()), 3))
    jdump(dict(report=rep, words=out), os.path.join(W, "words.json"))
    say("features:", rep)


# ------------------------------------------------------------------ sound (cochleagrams, stereo, song so far)
def erb_rate(f):
    """Glasberg & Moore ERB-rate (Cams) of frequency f, Hz."""
    return 21.4 * np.log10(1.0 + 0.00437 * np.asarray(f, float))


def erb_space(fmin, fmax, n):
    e = np.linspace(erb_rate(fmin), erb_rate(fmax), n)
    return (10 ** (e / 21.4) - 1.0) / 0.00437


def cochleagram(y, sr, cfs, hop_s, align=True):
    """Gammatone filterbank envelopes, dB. Each channel is a 4th-order complex gammatone (the signal shifted down by
    the centre frequency, then four one-pole low-passes of bandwidth 1.019 ERB: unity gain at the centre), and the
    envelope power is averaged over non-overlapping frames of hop_s (frame j = [j*hop, (j+1)*hop)). The filters are
    causal; with align=True each channel is shifted earlier by its group delay (under 25 ms, more for low channels) so
    onsets line up across channels and with the word lines. Returns (n_ch, n_frames) float32, 10*log10(power)."""
    from scipy.signal import lfilter
    hop = int(round(hop_s * sr))
    nfr = len(y) // hop
    t = np.arange(len(y)) / sr
    out = np.empty((len(cfs), nfr), np.float32)
    for i, fc in enumerate(cfs):
        erb = 24.7 * (4.37 * fc / 1000.0 + 1.0)
        a_ = np.exp(-2 * np.pi * 1.019 * erb / sr)
        z = lfilter([(1 - a_) ** 4], np.poly([a_] * 4), y * np.exp(-2j * np.pi * fc * t))
        p = np.abs(z) ** 2
        if align:
            dly = int(round(4 * a_ / (1 - a_)))  # group delay at the centre frequency, samples
            p = np.concatenate([p[dly:], np.zeros(dly)])
        out[i] = 10 * np.log10(p[:nfr * hop].reshape(nfr, hop).mean(1) + 1e-12)
    return out


def stage_sound(a, W):
    """Everything the two pictures show besides the words: cochleagrams of the lead (the whole vocal under the
    yellow band), the backing and the five band stems on one colour scale; the stereo picture of song.wav; and the
    low-resolution song-so-far data (a cochleagram of the mix and the level of each stem, causal blocks)."""
    import librosa
    sr = 22050
    F = np.load(os.path.join(W, "features.npz"))
    ft = F["ft"]
    use_full = F["use_full"] if "use_full" in F.files else np.zeros(len(ft), bool)
    out = {}
    cf_l = erb_space(C["COCH_FMIN"], C["COCH_FMAX"], C["COCH_N_LEAD"])
    cf_s = erb_space(C["COCH_FMIN"], C["COCH_FMAX"], C["COCH_N_STEM"])
    # the frame steps as they really are: cochleagram() rounds them to whole samples (110 and 220 at 22050 Hz, 0.23%
    # under 5 and 10 ms). These, not the nominal steps, are stored and drawn: until 11-11 the nominal ones were, and
    # every cochleagram panel ran late by 0.23% of the time since the song's start (0.7 s at 5:00)
    hop_l, hop_s = (int(round(C[k] * sr)) / sr for k in ("COCH_HOP_LEAD", "COCH_HOP_STEM"))
    t = time.time()
    lead = cochleagram(load_mono(lead_wav(W), sr), sr, cf_l, C["COCH_HOP_LEAD"])
    if use_full.any():  # the main panel shows the whole vocal where the words were found there (see features)
        voc = cochleagram(load_mono(os.path.join(W, "vocals.wav"), sr), sr, cf_l, C["COCH_HOP_LEAD"])
        n_ = min(lead.shape[1], voc.shape[1])
        tl = (np.arange(n_) + 0.5) * hop_l
        m = use_full[np.clip(np.searchsorted(ft, tl), 0, len(ft) - 1)]
        lead[:, :n_][:, m] = voc[:, :n_][:, m]
    out["lead"] = lead
    bp = os.path.join(W, "backing.wav")
    out["backing"] = cochleagram(load_mono(bp, sr), sr, cf_s, C["COCH_HOP_STEM"]) if os.path.exists(bp) else None
    for n in C["STEMS6"]:
        out[n] = cochleagram(load_mono(os.path.join(W, "band", f"{n}.wav"), sr), sr, cf_s, C["COCH_HOP_STEM"])
    say(f"   cochleagrams: {time.time() - t:.0f} s")
    # one colour scale for every cochleagram: louder in the picture = louder in the song
    srcs = [v for v in out.values() if v is not None]
    top = max(float(np.percentile(v[:, ::3], C["COCH_TOP_PCT"])) for v in srcs)
    # stereo: per STFT bin, pan = (|R| - |L|) / (|R| + |L|), weighted by the bin's energy, summed into PAN_BINS rows
    ys, sr0 = librosa.load(os.path.join(W, "song.wav"), sr=sr, mono=False)
    if ys.ndim == 1 or ys.shape[0] == 1:
        mono, side = True, 0.0
    else:
        side = float(np.sum((ys[0] - ys[1]) ** 2) / (np.sum((ys[0] + ys[1]) ** 2) + 1e-12))
        mono = side < C["MONO_SIDE"]
    hop_p = int(C["PAN_HOP"] * sr)
    if not mono:
        Hdb = pan_image(ys[0], ys[1], sr, hop_p)
        out["pan"] = Hdb.astype(np.float32)
        out["pan_top"] = float(np.percentile(Hdb, 99.5))
    # song so far: causal blocks only (each block's numbers use audio inside the block, causal filters, no centring)
    B = C["SOFAR_BLOCK"]
    mix = ys.mean(0) if ys.ndim > 1 else ys
    cf_m = erb_space(C["COCH_FMIN"], C["COCH_FMAX"], C["SOFAR_N"])
    sof = cochleagram(mix, sr, cf_m, B, align=False)   # one frame per block: the mean power of exactly B seconds
    nb = sof.shape[1]
    lanes = {}
    hb = int(B * sr)
    for name, path in [("voice", os.path.join(W, "vocals.wav"))] + \
                      [(n, os.path.join(W, "band", f"{n}.wav")) for n in C["STEMS6"]]:
        y = load_mono(path, sr)
        nb2 = len(y) // hb
        lanes[name] = 10 * np.log10((y[:nb2 * hb] ** 2).reshape(nb2, hb).mean(1) + 1e-12)
    nbl = min(nb, *(len(v) for v in lanes.values()))
    lane_names = list(lanes)
    L = np.array([lanes[n][:nbl] for n in lane_names], np.float32)
    np.savez_compressed(
        os.path.join(W, "sound.npz"),
        lead=out["lead"].astype(np.float16), lead_cf=cf_l, lead_hop=hop_l,
        stem_cf=cf_s, stem_hop=hop_s, top=top, floor=top - C["COCH_RANGE_DB"],
        **({"backing": out["backing"].astype(np.float16)} if out["backing"] is not None else {}),
        **{f"stem_{n}": out[n].astype(np.float16) for n in C["STEMS6"]},
        mono=mono, side_ratio=side, pan_hop=hop_p / sr,
        **({"pan": out["pan"].astype(np.float16), "pan_top": out["pan_top"]} if not mono else {}),
        sofar=sof[:, :nbl].astype(np.float32), sofar_cf=cf_m, sofar_block=B, lanes=L, lane_names=np.array(lane_names),
        sofar_top=float(np.percentile(sof, 99.5)), lanes_top=float(np.percentile(L, 99.5)))
    say(f"sound: cochleagram scale {top - C['COCH_RANGE_DB']:.1f}..{top:.1f} dB; "
        f"{'mono' if mono else f'stereo (side/mid energy {side:.3f})'}")


# ------------------------------------------------------------------ the "ear" intensity scale (prepare --scale ear)
def ear_map(M, hop_s, top):
    """A dB cochleagram (channels x frames, frame step hop_s) as the 'ear' response, two plain steps per channel:
    1. loudness: d = (frame power / the song's top power) ** EAR_POWER (so d = 1 at the dB scale's top, 0.5 ten dB
       under it at 0.3: Stevens' power law);
    2. adaptation: out = d - EAR_ADAPT * (the channel's own average of d over the previous EAR_TAU seconds: a causal
       one-pole average of the frames before this one), never below 0. An onset meets no average yet and shows at
       its full d; a held sound settles to (1 - EAR_ADAPT) * d within about 2 * EAR_TAU; just after a sound stops the
       average is still up, so its tail is cut short. out never exceeds d: nothing is drawn where there is no energy.
    Returns float32, same shape."""
    from scipy.signal import lfilter
    d = (10.0 ** (C["EAR_POWER"] * (M.astype(np.float32) - top) / 10.0)).astype(np.float32)
    a_ = float(np.exp(-hop_s / C["EAR_TAU"]))
    slow = lfilter([1.0 - a_], [1.0, -a_], d, axis=1)
    out = d.copy()
    out[:, 1:] -= C["EAR_ADAPT"] * slow[:, :-1]
    return np.maximum(out, 0.0, out=out).astype(np.float32)


def ear_scale(W, S=None):
    """The top of the 'ear' colour scale for a cache: the largest, over the sources of sound.npz (lead, backing, the
    five stems), of the EAR_TOP_PCT percentile of ear_map() over the whole song. Kept in WORK/ear.json with what it
    was made from (the constants, sound.npz's dB top, frame step and size), so a close-up uses the same number as the windows;
    made again if any of those changed."""
    p = os.path.join(W, "ear.json")
    S = S if S is not None else np.load(os.path.join(W, "sound.npz"))
    top = float(S["top"])
    key = dict({k: C[k] for k in ("EAR_POWER", "EAR_TAU", "EAR_ADAPT", "EAR_TOP_PCT")}, sound_top=top,
               sound_bytes=os.path.getsize(os.path.join(W, "sound.npz")), lead_hop=float(S["lead_hop"]))
    if os.path.exists(p) and jload(p).get("made_from") == key:
        return float(jload(p)["top"])
    srcs = [("lead", float(S["lead_hop"]))] + [(n, float(S["stem_hop"])) for n in S.files
                                               if n == "backing" or n.startswith("stem_") and n[5:] in C["STEMS6"]]
    etop = max(float(np.percentile(ear_map(S[n], h, top)[:, ::3], C["EAR_TOP_PCT"])) for n, h in srcs)
    jdump(dict(top=etop, made_from=key), p)
    return etop


def coch_scale(W, S):
    """(lo, hi, to_colour) of every cochleagram panel under C['SCALE']: to_colour(M_dB, hop_s) gives what is drawn."""
    if C["SCALE"] == "ear":
        top = float(S["top"])
        return 0.0, ear_scale(W, S), (lambda M, hop_s: ear_map(M, hop_s, top))
    return float(S["floor"]), float(S["top"]), (lambda M, hop_s: M)


# ------------------------------------------------------------------ periodicity (timing-based pitch, whole vocal stem)
def periodicity(y, sr, hop, lo=None, hi=None):
    """Summary autocorrelogram (Licklider 1951; Meddis & Hewitt 1991, here without a hair-cell model) on semitone
    rows: how strongly the sound repeats itself after each period, for every period at once.
      1. PER_N gammatone channels, ERB-spaced PER_FMIN..PER_FMAX (the cochleagram's filters), each shifted earlier by
         its group delay;
      2. each channel's output half-wave rectified and raised to PER_COMPRESS (what is left is when the channel
         fires, hardly how loud it is);
      3. per frame (Hann, PER_FRAME long, centred on j*hop/sr) the autocorrelation of each channel, summed over the
         channels (computed as the cosine transform of the summed power spectra, which is the same thing, at the
         exact period of each row; fluctuation slower than PER_CUT Hz is left out); negative values are drawn as 0.
    Rows: 3 per semitone for MIDI lo-1..hi+1 on A4 = 440 Hz (row 3*(m - lo + 1) + 1 is centred on note m; the extra
    semitone at each end lets the picture be moved by the song's tuning). The value at lag 0 would be the mean
    compressed power of the frame, so loud is bright and silence is 0. Nothing is picked or counted: a sound of
    period T also repeats after 2T, 3T..., so every pitch has fainter rows 12, 19, 24, 28 semitones below it.
    Returns (3*(hi-lo+3), n_frames) float32."""
    from scipy.signal import lfilter
    from scipy.fft import rfft
    lo = C["PER_LO"] if lo is None else lo
    hi = C["PER_HI"] if hi is None else hi
    hop = int(hop)
    N = int(round(C["PER_FRAME"] * sr))
    nfft = 1 << int(np.ceil(np.log2(N + sr / midi_hz(lo - 2) + 2)))   # no wrap-around at the longest period
    nfr = len(y) // hop + 1
    t = np.arange(len(y)) / sr
    w = np.hanning(N).astype(np.float32)
    S = np.zeros((nfr, nfft // 2 + 1), np.float32)
    for fc in erb_space(C["PER_FMIN"], C["PER_FMAX"], C["PER_N"]):
        erb = 24.7 * (4.37 * fc / 1000.0 + 1.0)
        a_ = np.exp(-2 * np.pi * 1.019 * erb / sr)
        rot = np.exp(-2j * np.pi * fc * t)
        x = 2.0 * np.real(lfilter([(1 - a_) ** 4], np.poly([a_] * 4), y * rot) * np.conj(rot))  # the channel's waveform
        dly = int(round(4 * a_ / (1 - a_)))
        x = np.maximum(x[dly:], 0.0) ** C["PER_COMPRESS"]
        xp = np.concatenate([np.zeros(N // 2), x, np.zeros(N + dly + hop)]).astype(np.float32)
        V = np.lib.stride_tricks.sliding_window_view(xp, N)[::hop][:nfr]
        for j in range(0, nfr, 4096):
            S[j:j + 4096] += np.abs(rfft(V[j:j + 4096] * w, n=nfft, axis=1)) ** 2
    fk = np.arange(nfft // 2 + 1) * sr / nfft
    m = (lo - 1) - 1.0 / 3 + np.arange(3 * (hi - lo + 3)) / 3.0
    Cm = 2.0 * np.cos(2 * np.pi * np.outer(fk, 1.0 / midi_hz(m))).astype(np.float32)
    Cm[fk < C["PER_CUT"]] = 0.0
    return np.maximum((S @ Cm) / (nfft * float(np.sum(w ** 2))), 0.0).T.astype(np.float32)


def stage_periodicity(a, W):
    """The periodicity picture of the whole vocal stem (vocals.wav: lead and backing together), for every window
    frame, and its whole-song colour top. Reads vocals.wav, nothing else. Prints no whole-song number."""
    sr = C["PER_SR"]
    hop = int(round(C["PER_HOP"] * sr))
    R = periodicity(load_mono(os.path.join(W, "vocals.wav"), sr), sr, hop)
    top = float(np.percentile(R[:, ::3], C["PER_TOP_PCT"]))
    tmp = os.path.join(W, "periodicity.tmp.npz")
    np.savez_compressed(tmp, R=R.astype(np.float16), hop=hop / sr, top=top, lo=C["PER_LO"], hi=C["PER_HI"])
    os.replace(tmp, os.path.join(W, "periodicity.npz"))
    say(f"periodicity: {R.shape[1]} frames")


def load_periodicity(W):
    """periodicity.npz as a dict with the song's tuning (cents, from harmony.npz; 0 without one), or None if the
    cache has none (made before the panel existed): the voice picture is then drawn without the panel."""
    p = os.path.join(W, "periodicity.npz")
    if not os.path.exists(p):
        return None
    Z = np.load(p)
    D = {k: (Z[k] if Z[k].ndim else float(Z[k])) for k in Z.files}
    hp = os.path.join(W, "harmony.npz")
    D["cents"] = float(np.load(hp)["cents"]) if os.path.exists(hp) else 0.0
    return D


def draw_period(ax, R, hop, toff, t0, t1, tend, top, cents, ww=(), fs=5.6, note=""):
    """The periodicity panel: R from periodicity() (frame j centred on toff + j*hop) on the harmony picture's
    semitone axis (PER_LO..PER_HI, moved by the song's tuning `cents`), linear colour 0..top.
    Returns {label note name: MIDI number}."""
    lo, hi = C["PER_LO"], C["PER_HI"]
    draw_cq(ax, R, hop, toff, t0, t1, tend, 0.0, top, lo - 1.5 - cents / 100.0, hi + 1.5 - cents / 100.0)
    lab = semitone_axis(ax, lo, hi, fs)
    for w in ww:
        ax.axvline(w["s"], color="white", lw=0.5, alpha=0.22 if not w["approx"] else 0.1, zorder=3.4)
    ax.text(t0 + 0.004 * (t1 - t0), hi + 0.3, "periodicity of the whole vocal stem (lead + backing together): summary "
            "autocorrelogram on semitone rows" + note + "; every voice lights its note's row AND fainter rows 12, 19, "
            "24 semitones below it (the top row " + ("of a ladder is the note)" if not note else "= the note)"),
            color="white", fontsize=8.5 if not note else 7.9, va="top", ha="left", zorder=5)
    ax.set_ylabel("periods", fontsize=11, labelpad=30)
    return lab


# ------------------------------------------------------------------ harmony (the notes: constant-Q on semitone rows)
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(m):
    """MIDI number -> note name with octave (60 -> C4, 69 -> A4)."""
    return f"{NOTE_NAMES[int(m) % 12]}{int(m) // 12 - 1}"


def hz2midi(f, cents=0.0):
    """Hz -> fractional MIDI number on an axis whose A4 row is 440 Hz moved by `cents` (the song's measured tuning)."""
    f = np.asarray(f, float)
    m = np.full(f.shape, np.nan)
    ok = np.isfinite(f) & (f > 0)
    m[ok] = 69.0 + 12.0 * np.log2(f[ok] / 440.0) - cents / 100.0
    return m


def harm_tuning(y, sr):
    """The audio's own tuning: cents (-50..+50) by which its pitches sit off A4 = 440 Hz (librosa.estimate_tuning:
    spectral peaks, parabolic interpolation, the most common offset from the semitone grid in 1-cent steps)."""
    import librosa
    return 100.0 * float(librosa.estimate_tuning(y=y, sr=sr, n_fft=4096, resolution=0.01, bins_per_octave=12))


def harm_cq(y, sr, hop, lo, hi, cents=0.0, fscale=1.0):
    """Constant-Q magnitudes in dB, HARM_BPO bins per octave, rows for MIDI notes lo..hi on a grid moved by `cents`:
    with 3 bins per semitone, row 3*(m - lo) + 1 is centred on note m and the rows either side are a third of a
    semitone flat and sharp. Frame j is centred on j*hop/sr. Returns (3*(hi-lo+1), n_frames) float32."""
    import librosa
    bpo = C["HARM_BPO"]; per = bpo // 12
    fmin = 440.0 * 2.0 ** ((lo - 69 + cents / 100.0) / 12.0 - (per // 2) / bpo)
    Q = librosa.cqt(np.asarray(y, np.float32), sr=sr, hop_length=int(hop), fmin=fmin, n_bins=(hi - lo + 1) * per,
                    bins_per_octave=bpo, tuning=0.0, filter_scale=fscale)   # tuning=0.0: None would re-estimate it
    return (20.0 * np.log10(np.abs(Q) + 1e-7)).astype(np.float32)


def harm_chroma(cq, lo):
    """The constant-Q power folded over octaves: 12 rows C..B, dB."""
    per = C["HARM_BPO"] // 12
    P = (10.0 ** (cq.astype(np.float32) / 10.0)).reshape(-1, per, cq.shape[1]).sum(1)
    out = np.zeros((12, cq.shape[1]), np.float32)
    for i in range(P.shape[0]):
        out[(lo + i) % 12] += P[i]
    return 10.0 * np.log10(out + 1e-12)


def bass_pitch(yb, sr, hop):
    """pYIN on the bass stem (decimated by 4; hop in samples at sr, a multiple of 4). Returns (f0 Hz with NaN,
    level dB) per frame, frame j centred on j*hop/sr. Not gated here."""
    import librosa
    from scipy.signal import resample_poly
    y4 = resample_poly(np.asarray(yb, np.float64), 1, 4).astype(np.float32)
    f, _, _ = librosa.pyin(y4, fmin=C["BASS_F0"][0], fmax=C["BASS_F0"][1], sr=sr / 4.0, frame_length=1024,
                           hop_length=int(hop) // 4, center=True)
    db = 20 * np.log10(librosa.feature.rms(y=y4, frame_length=1024, hop_length=int(hop) // 4, center=True)[0] + 1e-6)
    n = min(len(f), len(db))
    return f[:n], db[:n]


def harmony_data(ya, yb, sr, hop, cents):
    """Everything the harmony picture measures, from the accompaniment ya and the bass stem yb (same length, same
    rate), on the grid moved by `cents`. Used for whole songs (stage harmony), close-ups (look) and the refcard."""
    cq = harm_cq(ya, sr, hop, C["HARM_LO"], C["HARM_HI"], cents)
    bcq = harm_cq(yb, sr, hop, C["BASS_LO"], C["BASS_HI"], cents, C["BASS_FSCALE"])
    f, db = bass_pitch(yb, sr, hop)
    return dict(cq=cq, bcq=bcq, chroma=harm_chroma(cq, C["HARM_LO"]), hop=hop / sr, cents=float(cents),
                bass_f0=f, bass_db=db)


def harm_scales(D):
    """Whole-song colour scales and the bass pitch gate of a harmony_data() result (in place)."""
    D["cq_top"] = float(np.percentile(D["cq"][:, ::3], C["HARM_TOP_PCT"]))
    D["bcq_top"] = float(np.percentile(D["bcq"][:, ::3], C["HARM_TOP_PCT"]))
    D["chroma_top"] = float(np.percentile(D["chroma"], C["HARM_TOP_PCT"]))
    D["bass_gate"] = float(np.percentile(D["bass_db"], 95) - C["BASS_GATE_DB"])
    return D


def stage_harmony(a, W):
    """Note-level energy of the band, measured only: constant-Q of accomp.wav (the mix minus the vocal) and of the
    bass stem, the pitch-class fold, the bass stem's pitch, on a semitone grid centred on the song's own tuning.
    Reads accomp.wav and band/bass.wav, nothing else. Prints no whole-song number (a listener may run this)."""
    sr = C["HARM_SR"]
    ya = load_mono(os.path.join(W, "accomp.wav"), sr)
    yb = load_mono(os.path.join(W, "band", "bass.wav"), sr)
    n = min(len(ya), len(yb))
    D = harm_scales(harmony_data(ya[:n], yb[:n], sr, C["HARM_HOP"], harm_tuning(ya, sr)))
    f = D["bass_f0"].copy()
    f[D["bass_db"] < D["bass_gate"]] = np.nan
    tmp = os.path.join(W, "harmony.tmp.npz")
    np.savez_compressed(tmp, cq=D["cq"].astype(np.float16), bcq=D["bcq"].astype(np.float16),
                        chroma=D["chroma"].astype(np.float16), hop=D["hop"], cents=D["cents"], cq_top=D["cq_top"],
                        bcq_top=D["bcq_top"], chroma_top=D["chroma_top"], bass_f0=f, bass_gate=D["bass_gate"])
    os.replace(tmp, os.path.join(W, "harmony.npz"))
    say(f"harmony: {D['cq'].shape[1]} constant-Q frames")


def load_harmony(W):
    Z = np.load(os.path.join(W, "harmony.npz"))
    return {k: (Z[k] if Z[k].ndim else float(Z[k])) for k in Z.files}


def px_box(fig, ax):
    """An axes' box in picture pixels: x0, x1 from the left edge, y0 (top), y1 (bottom) from the top edge."""
    b = ax.get_position()
    w, h = fig.get_size_inches() * fig.dpi
    return dict(x0=round(float(b.x0 * w), 2), x1=round(float(b.x1 * w), 2), y0=round(float((1 - b.y1) * h), 2),
                y1=round(float((1 - b.y0) * h), 2))


def px_row(fig, ax, y):
    """Pixel row (from the top edge) of data value y in an axes."""
    return round(float(fig.get_size_inches()[1] * fig.dpi - ax.transData.transform((ax.get_xlim()[0], y))[1]), 2)


def fit_header(fig, txt, fs_min=8.0):
    """Shrink a header line until it ends inside the picture (matplotlib would cut it off silently)."""
    r = fig.canvas.get_renderer()
    lim = fig.get_size_inches()[0] * fig.dpi - 8
    while txt.get_window_extent(r).x1 > lim and txt.get_fontsize() > fs_min:
        txt.set_fontsize(txt.get_fontsize() - 0.5)
    return txt


def semitone_axis(ax, lo, hi, fs):
    """Dress a panel whose y axis is the MIDI number (one row per semitone, lo..hi): sharps/flats rows faintly shaded,
    a thin line between every two rows and a stronger one under each C, every C labelled with its octave, the other
    naturals with their letter (F and B, a semitone from E and C, in a second column), on both edges.
    Returns {label note name: MIDI number}."""
    ax.set_ylim(lo - 0.5, hi + 0.5); ax.set_yticks([]); ax.minorticks_off()
    tr = ax.get_yaxis_transform()
    ax.hlines(np.arange(lo, hi + 2) - 0.5, 0, 1, transform=tr, color="white", lw=0.3, alpha=0.16, zorder=2.6)
    lab = {}
    for m in range(lo, hi + 1):
        pc = m % 12
        if "#" in NOTE_NAMES[pc]:
            ax.axhspan(m - 0.5, m + 0.5, color="#b9c7d6", alpha=0.13, lw=0, zorder=2.5)
            continue
        if pc == 0:
            ax.axhline(m - 0.5, color="white", lw=0.7, alpha=0.5, zorder=2.7)
        far = pc in (5, 11)
        txt = note_name(m) if pc == 0 else NOTE_NAMES[pc]
        kw = dict(transform=tr, va="center", clip_on=False, fontsize=fs + 1.5 if pc == 0 else fs,
                  weight="bold" if pc == 0 else "normal", color="black" if pc == 0 else "#444444")
        ax.text(-0.004 - 0.0085 * far, m, txt, ha="right", **kw)
        ax.text(1.003 + 0.0065 * far, m, txt, ha="left", **kw)
        lab[note_name(m)] = m
    return lab


def draw_cq(ax, M, hop, toff, t0, t1, tend, lo_db, hi_db, ylo, yhi):
    """A constant-Q (or pitch-class) image: frame j is centred on toff + j*hop; only frames centred inside
    [t0, tend] are drawn; rows fill ylo..yhi."""
    j0 = max(0, int(np.ceil((t0 - toff) / hop - 1e-9)))
    j1 = min(M.shape[1], int(np.floor((tend - toff) / hop + 1e-9)) + 1)
    if j1 > j0:
        ax.imshow(M[:, j0:j1].astype(np.float32), aspect="auto", origin="lower", cmap="magma", vmin=lo_db, vmax=hi_db,
                  extent=[toff + (j0 - 0.5) * hop, toff + (j1 - 0.5) * hop, ylo, yhi], interpolation="antialiased")
    ax.set_facecolor("black"); ax.set_xlim(t0, t1); ax.tick_params(labelbottom=False)
    if tend < t1:
        ax.axvspan(tend, t1, color="#bbbbbb", zorder=3)


def draw_words(axw, ww, t0, t1, tend, span, ax_px, NR, wfs, thin=False):
    """The word lane: each word at its onset on the first free of NR staggered rows, a bar for its duration. Black =
    timed exactly, red bold = tagged, blue = found in the backing voices, grey italic ~word = time estimated (left out
    when no row is free). thin=True (the harmony picture's lane): any word with no free row keeps only its onset
    tick, and there are no duration bars."""
    DPI = C["DPI"]
    axw.set_xlim(t0, t1); axw.set_ylim(0, NR + 0.4)
    axw.set_yticks([]); axw.set_facecolor("#fafafa")
    rows_end = [-1e9] * NR
    for w in sorted(ww, key=lambda x: x["s"]):
        label = ("~" if w["approx"] else "") + w["text"]
        back = w.get("stem") == "backing" and not w["approx"]
        wid = (0.6 * wfs * DPI / 72 * len(label) + 4) * span / ax_px  # text width in seconds
        r = min(range(NR), key=lambda q: (rows_end[q] > w["s"], q if rows_end[q] <= w["s"] else rows_end[q]))
        col = "#888888" if w["approx"] else ("#1565c0" if back else ("#b00020" if w.get("tags") else "black"))
        if rows_end[r] > w["s"] and (w["approx"] or thin):
            if thin and not w["approx"]:
                axw.plot([w["s"], w["s"]], [NR + 0.1, NR + 0.4], color=col, lw=0.7, alpha=0.5)
            continue
        rows_end[r] = w["s"] + wid
        yy = NR - 0.55 - r * 1.0
        axw.text(w["s"], yy, label, fontsize=wfs, color=col, va="center", ha="left", clip_on=True,
                 style="italic" if w["approx"] else "normal", weight="bold" if w.get("tags") else "normal")
        axw.plot([w["s"], w["s"]], [yy + 0.3, NR + 0.4], color=col, lw=0.7, alpha=0.5)
        if not w["approx"] and not thin:
            axw.plot([w["s"], w["e"]], [yy - 0.36, yy - 0.36], color=col, lw=2.0, alpha=0.5)
    if tend < t1:
        axw.axvspan(tend, t1, color="#bbbbbb")
        axw.text((tend + t1) / 2, NR / 2, "song ends", ha="center", va="center", fontsize=12 if not thin else 9)


def time_ticks(axw, t0, t1, look=False):
    """x ticks of the bottom panel: every second, m:ss every 5 s (windows); every 0.5 s with m:ss.s (close-ups)."""
    if look:
        xt = np.arange(np.ceil(t0 * 2 - 1e-6) / 2, t1 + 1e-6, 0.5)
        axw.set_xticks(xt); axw.set_xticklabels([f"{int(x // 60)}:{x % 60:04.1f}" for x in xt], fontsize=10)
    else:
        xt = np.arange(t0, t1 + 0.01, 1.0)
        axw.set_xticks(xt); axw.set_xticklabels([mmss_short(x) if i % 5 == 0 else f"+{i}s" for i, x in enumerate(xt)],
                                               fontsize=10)
    return xt


def harm_grid(H):
    """The harmony picture's rows (header, piano roll, bass, pitch classes, words), heights in inches."""
    from matplotlib import gridspec
    return gridspec.GridSpec(5, 1, height_ratios=[0.28, 3.75, 1.7, 0.62, 0.44], hspace=0.07, left=C["MARG_L"],
                             right=C["MARG_R"], top=1 - 0.08 / H, bottom=1 - (C["HARM_H"] - 0.26) / H)


def draw_harmony(path, head, t0, t1, tend, D, ww, toff=0.0, lines=None, look=False, annot=(), foot=None, extra_h=0.0,
                 under=None, foot_h=None):
    """One harmony picture. D: harmony data (cq, bcq, chroma, hop, cents, *_top); toff: time of its frame 0.
    lines: dict name -> (times, Hz) for 'lead', 'backing', 'bass' pitch, drawn on the semitone axes.
    annot: [(time, MIDI number, text)] captions inside the piano roll, foot: text under the picture (extra_h inches
    in all, the lowest foot_h of them for the text), under: a function (fig, H) that draws more panels between the
    picture and the foot and returns their geometry (only the refcard uses these).
    Returns the picture's geometry (pixel boxes, the pixel row of every labelled note)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lines = lines or {}
    FW, DPI, ML, MR = C["FIG_W2"], C["DPI"], C["MARG_L"], C["MARG_R"]
    H = C["HARM_H"] + extra_h
    plt.rcParams.update({"font.family": "DejaVu Sans"})
    fig = plt.figure(figsize=(FW, H), dpi=DPI)
    gs = harm_grid(H)
    cents = D["cents"]
    axh = fig.add_subplot(gs[0]); axh.axis("off")
    fit_header(fig, axh.text(0, 0.5, f"{head}   tuning {cents:+.0f} cents   [the notes]", fontsize=13.5, va="center",
                             ha="left", weight="bold"))
    span = t1 - t0
    xt = np.arange(t0, t1 + 0.01, 1.0) if not look else np.arange(np.ceil(t0 * 2 - 1e-6) / 2, t1 + 1e-6, 0.5)
    COL = dict(lead=("#00e5ff", 1.8, 0.95), backing=("#80deea", 1.1, 0.6), bass=("#76ff03", 1.5, 0.9))

    def pitch_lines(ax, lo, hi, names):
        for n in names:
            if n in lines:
                t, f = lines[n]
                m = hz2midi(f, cents)
                m[(m < lo - 0.5) | (m > hi + 0.5)] = np.nan
                ax.plot(t, m, color=COL[n][0], lw=COL[n][1], alpha=COL[n][2], zorder=4, solid_capstyle="butt")

    # 1. piano roll of the accompaniment
    lo, hi = C["HARM_LO"], C["HARM_HI"]
    ax = fig.add_subplot(gs[1])
    draw_cq(ax, D["cq"], D["hop"], toff, t0, t1, tend, D["cq_top"] - C["HARM_RANGE_DB"], D["cq_top"], lo - 0.5, hi + 0.5)
    lab = semitone_axis(ax, lo, hi, 6.3)
    pitch_lines(ax, lo, hi, ("bass", "backing", "lead"))
    for w in ww:
        ax.axvline(w["s"], color="white", lw=0.5, alpha=0.22 if not w["approx"] else 0.1, zorder=3.4)
    ax.text(t0 + 0.004 * span, hi + 0.2, f"the band without the voice (accomp), constant-Q, {C['HARM_BPO']} rows per "
            f"octave = 3 per semitone, one dB scale for the song; each played note also lights its overtones above it "
            f"(+12, +19, +24, +28 rows of semitones...)", color="white", fontsize=8.5, va="top", ha="left", zorder=5)
    ax.text(t0 + 0.004 * span, hi - 2.2, "lines: cyan = lead voice pitch, pale = backing pitch, green = bass-stem "
            "pitch (the voices themselves are not in this picture)", color="white", fontsize=8.5, va="top", ha="left",
            zorder=5)
    ax.set_ylabel("notes of the band", fontsize=11, labelpad=30)
    for (ta, ma, txt) in annot:
        ax.text(ta, ma, txt, color="white", fontsize=8.5, va="bottom", ha="left", zorder=6,
                bbox=dict(facecolor="black", alpha=0.6, edgecolor="none", pad=1.5))
    # 2. the bass stem alone
    blo, bhi = C["BASS_LO"], C["BASS_HI"]
    axb = fig.add_subplot(gs[2])
    draw_cq(axb, D["bcq"], D["hop"], toff, t0, t1, tend, D["bcq_top"] - C["HARM_RANGE_DB"], D["bcq_top"],
            blo - 0.5, bhi + 0.5)
    blab = semitone_axis(axb, blo, bhi, 5.6)
    pitch_lines(axb, blo, bhi, ("bass",))
    axb.text(t0 + 0.004 * span, bhi + 0.2, "bass stem alone (own dB scale); green = its pitch; rows this low smear in "
             "time", color="white", fontsize=8.5, va="top", ha="left", zorder=5)
    axb.set_ylabel("bass", fontsize=11, labelpad=30)
    # 3. pitch classes
    axc = fig.add_subplot(gs[3])
    draw_cq(axc, D["chroma"], D["hop"], toff, t0, t1, tend, D["chroma_top"] - C["CHROMA_RANGE_DB"], D["chroma_top"],
            -0.5, 11.5)
    axc.set_ylim(-0.5, 11.5); axc.set_yticks([]); axc.minorticks_off()
    trc = axc.get_yaxis_transform()
    axc.hlines(np.arange(0, 13) - 0.5, 0, 1, transform=trc, color="white", lw=0.3, alpha=0.2, zorder=2.6)
    rows = {}
    for pc, nm in enumerate(NOTE_NAMES):
        rows[nm] = pc
        if "#" in nm:
            continue
        far = pc == 5
        kw = dict(transform=trc, va="center", clip_on=False, fontsize=5.6, color="black")
        axc.text(-0.004 - 0.0085 * far, pc, nm, ha="right", **kw)
        axc.text(1.003 + 0.0065 * far, pc, nm, ha="left", **kw)
    axc.set_ylabel("classes", fontsize=10, labelpad=30)
    axc.text(0.996, 0.93, "the piano roll folded over octaves: 12 rows, C at the bottom to B at the top",
             transform=axc.transAxes, color="white", fontsize=7.5, va="top", ha="right", zorder=5)
    # 4. words
    axw = fig.add_subplot(gs[4])
    draw_words(axw, ww, t0, t1, tend, span, FW * DPI * (MR - ML), C["HARM_WORD_ROWS"], C["HARM_WORD_FS"], thin=True)
    axw.set_ylabel("words", fontsize=10, labelpad=30)
    time_ticks(axw, t0, t1, look)
    for axx in (ax, axb, axc, axw):
        for x in xt:
            if t0 < x < t1:
                axx.axvline(x, color="#4fc3f7" if axx is not axw else "#bbbbbb", lw=0.5, alpha=0.3,
                            zorder=0.5 if axx is axw else 3.5)
    if foot:
        fig.text(ML, ((extra_h if foot_h is None else foot_h) - 0.08) / H, foot, fontsize=10.5, va="top", ha="left",
                 family="DejaVu Sans", linespacing=1.4)
    more = under(fig, H) if under else None
    fig.canvas.draw()
    geom = dict(w=fig.canvas.get_width_height()[0], h=fig.canvas.get_width_height()[1], panels=[
        dict(id="header", **px_box(fig, axh)),
        dict(id="notes", **px_box(fig, ax), time=True, midi_lo=lo, midi_hi=hi,
             notes={n: px_row(fig, ax, m) for n, m in lab.items()}),
        dict(id="bassnotes", **px_box(fig, axb), time=True, midi_lo=blo, midi_hi=bhi,
             notes={n: px_row(fig, axb, m) for n, m in blab.items()}),
        dict(id="classes", **px_box(fig, axc), time=True, rows={n: px_row(fig, axc, m) for n, m in rows.items()}),
        dict(id="words", **px_box(fig, axw), time=True)])
    if more:
        geom["under"] = more
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return geom


# ------------------------------------------------------------------ refcard (synthetic audio through the same code)
def synth_pluck(f, dur, sr, nh=8):
    """A plucked-string-like note: nh harmonics at 1/h, the higher ones dying faster, 5 ms attack."""
    t = np.arange(int(dur * sr)) / sr
    y = sum(np.sin(2 * np.pi * h * f * t) / h * np.exp(-t * (1.2 + 0.5 * h)) for h in range(1, nh + 1) if h * f < sr / 2.2)
    return y * np.minimum(1.0, t / 0.005) * np.minimum(1.0, (dur - t) / 0.15)


def midi_hz(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def refcard_audio(sr):
    """15 s of synthetic 'accompaniment' and 'bass stem', the events as [(start, end, midi notes, detune cents,
    short name)], a synthetic voice's (times, Hz) and the click time."""
    n = int(15 * sr)
    ya, yb = np.zeros(n), np.zeros(n)
    ev = [(0.5, 2.4, (45,), 0, "A2 pluck"), (2.9, 4.8, (60, 64, 67), 0, "C major"), (5.3, 7.2, (57, 60, 64), 0, "A minor"),
          (7.7, 9.6, (60, 64, 67), 30, "C major +30c"), (10.1, 12.0, (33,), 0, "A1 bass")]
    for s, e, ms, dc, nm in ev:
        y = sum(synth_pluck(midi_hz(m + dc / 100.0), e - s, sr) for m in ms) * (0.5 if len(ms) > 1 else 0.8)
        i = int(s * sr)
        ya[i:i + len(y)] += y
        if "bass" in nm:
            yb[i:i + len(y)] += y
    click = 12.5
    nc = int(0.004 * sr)   # the click: 4 ms of noise, centred on its time
    ya[int(click * sr) - nc // 2:int(click * sr) - nc // 2 + nc] += np.random.default_rng(0).uniform(-1, 1, nc)
    # a sung-like note with vibrato (the lead voice is only ever a line in this picture, so it is not added to ya)
    tv = np.arange(int(1.6 * sr)) / sr
    fv = midi_hz(69) * 2.0 ** (0.4 * np.sin(2 * np.pi * 5.5 * tv) * np.minimum(1, tv / 0.6) / 12.0)
    ph = 2 * np.pi * np.cumsum(fv) / sr
    yv = sum(np.sin(h * ph) / h for h in range(1, 6)) * 0.3
    return ya.astype(np.float32), yb.astype(np.float32), ev, (13.1, yv.astype(np.float32)), click


def synth_sung(f, dur, sr, vib=0.0, nh=12):
    """A sung-like tone: nh harmonics of f falling as 1/h with a broad bump near 600 Hz, 30 ms attack; vib = the
    depth in semitones of a 5.5 Hz vibrato."""
    t = np.arange(int(dur * sr)) / sr
    ph = 2 * np.pi * np.cumsum(f * 2.0 ** (vib * np.sin(2 * np.pi * 5.5 * t) / 12.0)) / sr
    y = sum(np.sin(h * ph) / h * (1.0 / (1 + ((h * f - 600) / 900.0) ** 2) + 0.15) for h in range(1, nh + 1)
            if h * f < sr / 2.2)
    return (0.3 * y * np.minimum(1, t / 0.03) * np.minimum(1, (dur - t) / 0.05)).astype(np.float32)


def refcard_voices(sr):
    """15 s of synthetic 'vocal stem' for the periodicity panel: the events as [(start, end, [(midi, vibrato
    semitones)], short name)], then a click and a hiss (their times returned too)."""
    y = np.zeros(int(15 * sr), np.float32)
    ev = [(0.5, 2.3, [(57, 0)], "one voice, A3"), (2.8, 4.6, [(57, 0), (64, 0)], "A3 + E4 (a fifth)"),
          (5.1, 6.9, [(57, 0), (58, 0)], "A3 + A#3 (a semitone)"), (7.4, 9.2, [(69, 0.5)], "A4, vibrato"),
          (9.7, 11.5, [(57, 0), (69, 0)], "A3 + A4 (an octave)")]
    for s, e, ns, _ in ev:
        x = sum(synth_sung(midi_hz(m), e - s, sr, vib) for m, vib in ns)
        y[int(s * sr):int(s * sr) + len(x)] += x
    click, hiss = 12.3, (13.0, 14.5)
    rng = np.random.default_rng(0)
    nc = int(0.004 * sr)
    y[int(click * sr) - nc // 2:int(click * sr) - nc // 2 + nc] += rng.uniform(-1, 1, nc).astype(np.float32)
    nh_ = int((hiss[1] - hiss[0]) * sr)
    th = np.arange(nh_) / sr
    y[int(hiss[0] * sr):int(hiss[0] * sr) + nh_] += (0.15 * rng.uniform(-1, 1, nh_) * np.minimum(1, th / 0.05)
                                                     * np.minimum(1, (th[-1] - th) / 0.05)).astype(np.float32)
    return y, ev, click, hiss


def refcard_period(fig, H, top_in):
    """The periodicity half of the reference card: synthetic voices through periodicity() and draw_period(), in a
    panel the size it has in a voice picture, top_in inches below the picture's top. Returns its geometry."""
    from matplotlib import gridspec
    sr = C["PER_SR"]
    hop = int(round(C["PER_HOP"] * sr))
    y, ev, click, hiss = refcard_voices(sr)
    R = periodicity(y, sr, hop)
    top = float(np.percentile(R[:, ::3], C["PER_TOP_PCT"]))
    ML, MR = C["MARG_L"], C["MARG_R"]
    fig.text(ML, 1 - (top_in + 0.05) / H, "THE PERIODICITY PANEL of the voice picture, on synthetic voices   "
             "0:00-0:15   (not the same sounds as above)", fontsize=13.5, va="top", ha="left", weight="bold")
    gs = gridspec.GridSpec(2, 1, height_ratios=[C["PER_H"], 0.44], hspace=0.03, left=ML, right=MR,
                           top=1 - (top_in + 0.36) / H, bottom=1 - (top_in + 0.36 + C["PER_H"] + 0.51) / H)
    ax = fig.add_subplot(gs[0])
    draw_period(ax, R, hop / sr, 0.0, 0.0, 15.0, 15.0, top, 0.0)
    for ta, txt in ((0.5, "1. one voice on A3: its row, and\nfainter rows below it: A2 (12\nsemitones down), D2 (19 down)"),
                    (2.8, "2. two voices a fifth apart,\nA3 + E4: both rows, and A2 (where\ntheir ladders meet) as bright as\n"
                          "either, though nobody sings it"),
                    (5.1, "3. two a semitone apart,\nA3 + A#3: one ridge between\nthe rows, pulsing (they beat)"),
                    (7.4, "4. A4 with vibrato: the ridge\nwobbles, and its ladder too\n(A3, D3, A2, F2, D2)"),
                    (9.7, "5. two an octave apart, A3 + A4:\none ladder from A4 down, but A3\nbrighter than A4 (compare no. 4)"),
                    (12.0, "6. click:\nno period,\nnothing"), (13.0, "7. hiss: no period,\nnearly nothing")):
        ax.text(ta, C["PER_HI"] - 3.2, txt, color="white", fontsize=8.5, va="top", ha="left", zorder=6,
                bbox=dict(facecolor="black", alpha=0.6, edgecolor="none", pad=1.5))
    axw = fig.add_subplot(gs[1])
    ww = [dict(text=nm, s=s, e=e, approx=False, tags=[]) for s, e, _, nm in ev]
    ww += [dict(text="click", s=click, e=click + 0.05, approx=False, tags=[]),
           dict(text="hiss (noise)", s=hiss[0], e=hiss[1], approx=False, tags=[])]
    draw_words(axw, ww, 0.0, 15.0, 15.0, 15.0, C["FIG_W2"] * C["DPI"] * (MR - ML), 1, C["HARM_WORD_FS"], thin=True)
    axw.set_ylabel("events", fontsize=10, labelpad=30)
    for x in time_ticks(axw, 0.0, 15.0)[1:-1]:
        ax.axvline(x, color="#4fc3f7", lw=0.5, alpha=0.3, zorder=3.5)
        axw.axvline(x, color="#bbbbbb", lw=0.5, alpha=0.3, zorder=0.5)
    return dict(panels=[period_geometry(px_box(fig, ax)), dict(id="events", **px_box(fig, axw), time=True)],
                events=[dict(start=s, end=e, midi=[m for m, _ in ns], name=nm) for s, e, ns, nm in ev],
                click=click, hiss=list(hiss))


def refcard(out):
    """Render the reference card: known synthetic notes through the same measuring and drawing as a real window's
    harmony picture, and under it known synthetic voices through the voice picture's periodicity panel."""
    import librosa, textwrap
    sr = C["HARM_SR"]
    ya, yb, ev, (tv0, yv), click = refcard_audio(sr)
    D = harm_scales(harmony_data(ya, yb, sr, C["HARM_HOP"], harm_tuning(ya, sr)))
    bf = D["bass_f0"].copy(); bf[D["bass_db"] < D["bass_gate"]] = np.nan
    fv, _, _ = librosa.pyin(yv, fmin=C["F0_FLOOR"], fmax=C["F0_CEIL"], sr=sr, frame_length=2048,
                            hop_length=int(C["HOP"] * sr), center=True)
    lines = dict(bass=(np.arange(len(bf)) * D["hop"], bf), lead=(tv0 + np.arange(len(fv)) * int(C["HOP"] * sr) / sr, fv))
    ww = [dict(text=nm, s=s, e=e, approx=False, tags=[]) for s, e, ms, dc, nm in ev]
    ww += [dict(text="click", s=click, e=click + 0.05, approx=False, tags=[]),
           dict(text="a voice (A4, vibrato)", s=tv0, e=tv0 + 1.6, approx=False, tags=[])]
    annot = [(0.5, 84.6, "1. one plucked A2: its row, then its\novertones A3 E4 A4 C#5 E5 (G5) A5"),
             (2.9, 84.6, "2. C major triad C4 E4 G4:\nthree rows + each note's overtones"),
             (5.3, 84.6, "3. A minor triad A3 C4 E4"),
             (7.7, 84.6, "4. the same C major, 30 cents sharp:\neach row sits a third of a row high"),
             (10.1, 84.6, "5. bass note A1: below this panel (only\nits overtones A2, E3, A3... show here);\nread it in the bass panel"),
             (12.62, 44, "6. click at 0:12.5:\nall rows at once,\nwider lower down"),
             (13.1, 73, "7. a sung A4 (cyan):\nthe voice is only a line")]
    foot = ("REFERENCE CARD: synthetic sound through the same measuring and drawing as every harmony picture. The "
            "words in the lane name each event.\n"
            "Rows are semitones: C labelled with its octave, the other naturals by letter, sharps/flats rows shaded, "
            "3 image rows per semitone (so a bend or an off-pitch note sits between rows).\n"
            "A played note lights its own row AND its overtones above it: +12 semitones (an octave), +19 (octave + "
            "fifth), +24 (two octaves), +28 (two octaves + major third), +31, +34, +36.\n"
            "The overtones are part of one sound, not extra notes: a lone A2 is not an A major chord. To find what "
            "is played, look for the lowest row of each ladder and for rows that start together.\n"
            "Low rows are blurred in time (a filter narrow enough to tell semitones apart is long down there): "
            "starts and ends look soft below C3, and the bass panel smears most. The click shows it.\n"
            "The strip above the words folds the piano roll over octaves (C at the bottom, B at the top); "
            "overtones land on other classes too (a lone A also lights E and C#).\n"
            + "\n".join(textwrap.wrap(
                "THE PERIODICITY PANEL (in the voice picture, between the backing strip and the words) is the mirror "
                "image: how strongly the whole vocal stem repeats itself after each period, for every period at once (a "
                "summary autocorrelogram; brightness = strength; nothing is picked or counted). A sound that repeats "
                "every T also repeats every 2T, 3T, 4T..., so each voice lights its own row AND a ladder BELOW it: 12 "
                "semitones down (the octave below), 19, 24, 28, 31, fading toward the bottom. The top row of a ladder "
                "is the sung note; the rows below it are not other singers. Two voices are two ladders, and a row both "
                "ladders share can be as bright as either note (no. 2: A2). Voices a semitone apart merge into one "
                "pulsing ridge; an octave pair is one ladder, the upper voice's, with the lower voice's row the brighter. "
                "Ridges are about 1.5 semitones wide "
                "at A2, 2 at A3, 2.5 at A4, 3.5 at A5 (the timing is equally fine in milliseconds everywhere, which is "
                "fewer semitones up high); the peak sits on the note.", width=168)))
    per_h = 0.2 + 0.36 + C["PER_H"] + 0.51 + 0.3   # the periodicity block: gap, title, panel, event lane, tick labels
    foot_h = 0.204 * (foot.count("\n") + 1) + 0.2
    geom = draw_harmony(out, "REFERENCE CARD   0:00-0:15   synthetic notes", 0.0, 15.0, 15.0, D, ww, lines=lines,
                        annot=annot, foot=foot, extra_h=per_h + foot_h, foot_h=foot_h,
                        under=lambda fig, H: refcard_period(fig, H, C["HARM_H"] + 0.2))
    say(f"refcard: {out} ({geom['w']} x {geom['h']} px)")
    return geom


# ------------------------------------------------------------------ quality numbers (no lyric text)
def n_windows(dur):
    nwin = int(np.ceil(dur / C["WIN"] - 1e-6))
    if dur - (nwin - 1) * C["WIN"] < 1.0 and nwin > 1:
        nwin -= 1
    return nwin


def quality(W):
    """Technical numbers for a prepared work dir, per window and overall. 'pitched' = share of frames with a pitch
    value; 'pitched_active' = the same over frames whose lead level is above the song's 40th percentile (so silence
    between lines doesn't count against it). Works on work dirs made before the lead/backing split too."""
    meta = jload(os.path.join(W, "meta.json"))
    F = np.load(os.path.join(W, "features.npz"))
    words = jload(os.path.join(W, "words.json"))["words"]
    vo = jload(os.path.join(W, "voices.json"))
    ft, f0, db = F["ft"], F["f0"], F["db"][:len(F["ft"])]
    act = db > np.percentile(db, 40)
    back = F["back_on"] if "back_on" in F.files else None
    dur = meta["duration"]
    per = []
    for k in range(n_windows(dur)):
        t0, t1 = k * C["WIN"], min(dur, (k + 1) * C["WIN"])
        ww = [w for w in words if t0 <= w["s"] < t1]
        m = (ft >= t0) & (ft < t1)
        r = dict(k=k, words=len(ww), exact=sum(not w["approx"] for w in ww), tagged=sum(bool(w["tags"]) for w in ww),
                 pitched=round(float(np.isfinite(f0[m]).mean()), 3) if m.any() else None,
                 pitched_active=round(float(np.isfinite(f0[m & act]).mean()), 3) if (m & act).any() else None)
        if back is not None:
            r["backing_audible"] = round(float(back[m].mean()), 3) if m.any() else None
        per.append(r)
    n = len(words); ex = sum(not w["approx"] for w in words)
    tags = {}
    for w in words:
        for t in w["tags"]:
            tags[t] = tags.get(t, 0) + 1
    tot = dict(words=n, exact=ex, exact_rate=round(ex / max(1, n), 3),
               tagged=sum(bool(w["tags"]) for w in words),
               tagged_rate_of_exact=round(sum(bool(w["tags"]) for w in words) / max(1, ex), 3),
               tag_counts=dict(sorted(tags.items(), key=lambda x: -x[1])),
               pitched=round(float(np.isfinite(f0).mean()), 3),
               pitched_active=round(float(np.isfinite(f0[act]).mean()), 3),
               voices=vo["report"]["voices"], voice_changes=vo["report"]["changes"])
    if back is not None:
        tot["backing_audible"] = round(float(back.mean()), 3)
    if os.path.exists(os.path.join(W, "lead.json")):
        tot["split"] = jload(os.path.join(W, "lead.json"))
    return dict(total=tot, windows=per)


# ------------------------------------------------------------------ render
def hz2ch(cf, f):
    """Frequency (Hz) -> fractional channel index of a cochleagram with centre frequencies cf (ERB-spaced)."""
    f = np.asarray(f, float)
    y = np.full(f.shape, np.nan)
    ok = np.isfinite(f) & (f > 0)
    y[ok] = np.interp(erb_rate(f[ok]), erb_rate(cf), np.arange(len(cf)), left=np.nan, right=np.nan)
    return y


def draw_coch(ax, M, hop, cf, t0, t1, tend, lo, hi, fmax=None, ticks=(100, 200, 400, 800, 1600, 3200, 6400), fs=10,
              toff=0.0):
    """One cochleagram panel: x = time (t0..t1, data only up to tend), y = channel (ERB-spaced), labelled in Hz.
    toff = the time of M's first frame (0 for a whole song, the excerpt's start for a close-up)."""
    nch = len(cf) if fmax is None else int(min(len(cf), np.searchsorted(cf, fmax) + 1))
    j0, j1 = int(max(0, np.floor((t0 - toff) / hop))), int(min(M.shape[1], np.ceil((tend - toff) / hop)))
    if j1 > j0:
        ax.imshow(M[:nch, j0:j1].astype(np.float32), aspect="auto", origin="lower", cmap="magma", vmin=lo, vmax=hi,
                  extent=[toff + j0 * hop, toff + j1 * hop, -0.5, nch - 0.5], interpolation="antialiased")
    ax.set_facecolor("black")
    ax.set_xlim(t0, t1); ax.set_ylim(-0.5, nch - 0.5)
    tk = [f for f in ticks if cf[0] <= f <= cf[nch - 1]]
    ax.set_yticks(hz2ch(cf, tk)); ax.set_yticklabels([f"{f / 1000:g}k" if f >= 1000 else str(f) for f in tk], fontsize=fs)
    ax.minorticks_off(); ax.tick_params(labelbottom=False)
    if tend < t1:
        ax.axvspan(tend, t1, color="#bbbbbb", zorder=3)
    return nch


def held_notes(ww, ft, f0, t0, tend):
    """Up to HELD_MAX measured lead words with the longest continuous pitch run (gaps of 2 frames bridged) inside the
    window, run >= HELD_MIN. Returns [(word, run_start, run_end)] longest first."""
    cand = []
    for w in ww:
        if not measured(w) or w["voiced"] < C["MIN_VOICED"]:
            continue
        i0, i1 = np.searchsorted(ft, max(w["s"], t0)), np.searchsorted(ft, min(w["e"], tend))
        v = np.isfinite(f0[i0:i1])
        best, cur, gap, start = (0, 0), None, 0, 0
        for j, ok in enumerate(v):
            if ok:
                if cur is None:
                    start = j
                cur, gap = j, 0
                if cur - start > best[1] - best[0]:
                    best = (start, cur)
            elif cur is not None:
                gap += 1
                if gap > 2:
                    cur = None
        run = (best[1] - best[0]) * C["HOP"]
        if run >= C["HELD_MIN"]:
            cand.append((w, float(ft[i0 + best[0]]), float(ft[i0 + best[1]])))
    cand.sort(key=lambda x: -(x[2] - x[1]))
    return cand[:C["HELD_MAX"]]


def per_extra(per):
    """Inches a voice picture grows by when it has the periodicity panel."""
    return (C["PER_H"] + C["PER_GAP"]) if per else 0.0


def split_words(gs, H, per, bottom):
    """(periodicity slot, word-lane slot) of a voice grid gs whose last row was the word lane: with the periodicity
    panel the picture is per_extra() taller, the panel starts where the word lane did, and the word lane keeps its
    size, `bottom` inches above the picture's bottom edge. Everything above keeps its place from the top."""
    if not per:
        return None, gs[len(gs.get_height_ratios()) - 1]
    from matplotlib import gridspec
    from matplotlib.figure import Figure
    ML, MR = C["MARG_L"], C["MARG_R"]
    wt = (1 - gs[len(gs.get_height_ratios()) - 1].get_position(Figure(figsize=(C["FIG_W2"], H))).y1) * H  # in from the top
    gp = gridspec.GridSpec(1, 1, left=ML, right=MR, top=1 - wt / H, bottom=1 - (wt + C["PER_H"]) / H)
    gw = gridspec.GridSpec(1, 1, left=ML, right=MR, top=1 - (wt + per_extra(True)) / H, bottom=bottom / H)
    return gp[0], gw[0]


def voice_grid(H, per=False):
    """The voice picture's grids: (header, lead, backing, words), the held-note close-ups under them, and the slots
    of the periodicity panel (None without it) and the word lane. H is the picture's height in inches: VOICE_H, plus
    per_extra() with the periodicity panel."""
    from matplotlib import gridspec
    ML, MR = C["MARG_L"], C["MARG_R"]
    gs = gridspec.GridSpec(4, 1, height_ratios=[0.32, 3.75, 0.95, 1.5], hspace=0.07,
                           left=ML, right=MR, top=1 - 0.08 / H, bottom=(4.4 + per_extra(per)) / H)
    gc = gridspec.GridSpec(4, 5, width_ratios=[3.3, 1.2, 0.42, 3.3, 1.2], height_ratios=[2.3, 0.75, 0.75, 0.75],
                           left=ML, right=MR, top=3.86 / H, bottom=1.98 / H, hspace=0.1, wspace=0.06)
    return (gs, gc) + split_words(gs, H, per, 4.4)


def look_voice_grid(H, per=False):
    """The voice close-up's grid (header, lead, backing, words) and the periodicity and word-lane slots."""
    from matplotlib import gridspec
    gs = gridspec.GridSpec(4, 1, height_ratios=[0.32, 3.75, 0.95, 0.9], hspace=0.07, left=C["MARG_L"],
                           right=C["MARG_R"], top=1 - 0.08 / H, bottom=(0.3 + per_extra(per)) / H)
    return (gs,) + split_words(gs, H, per, 0.3)


def period_geometry(box):
    """The periodicity panel's entry for a geometry file, from its pixel box: the row of every labelled note."""
    lo, hi = C["PER_LO"], C["PER_HI"]
    row = lambda m: round(box["y0"] + (hi + 0.5 - m) / (hi - lo + 1) * (box["y1"] - box["y0"]), 2)  # noqa: E731
    return dict(id="periodicity", **box, time=True, midi_lo=lo, midi_hi=hi,
                notes={note_name(m): row(m) for m in range(lo, hi + 1) if "#" not in note_name(m)})


def band_grid(H2):
    """The band picture's grids: (header, five stems, stereo, bass and attacks), and the two song-so-far strips."""
    from matplotlib import gridspec
    ML, MR = C["MARG_L"], C["MARG_R"]
    gs = gridspec.GridSpec(8, 1, height_ratios=[0.32] + [1.12] * 5 + [1.15, 0.7], hspace=0.07,
                           left=ML, right=MR, top=1 - 0.08 / H2, bottom=2.3 / H2)
    g2 = gridspec.GridSpec(2, 1, height_ratios=[0.85, 0.75], hspace=0.05, left=ML, right=MR,
                           top=1.88 / H2, bottom=0.3 / H2)
    return gs, g2


def static_geometry(per=True):
    """Pixel boxes of the voice and band pictures' panels, from the same grids the renderer uses (the panels are at
    the same place in every window). Panels with time=True share the window's time axis: x0 = the window's start,
    x1 = start + window seconds. 'sofar' and 'lanes' span the whole song instead. per=False: the voice picture
    without the periodicity panel (a cache made before it existed)."""
    from matplotlib.figure import Figure
    FW, DPI = C["FIG_W2"], C["DPI"]
    out = {}
    for pic, H, grid in (("voice", C["VOICE_H"] + per_extra(per), lambda h: voice_grid(h, per)),
                         ("band", C["BAND_H"], band_grid)):
        fig = Figure(figsize=(FW, H), dpi=DPI)
        ga, gb = grid(H)[:2]

        def box(spec):
            b = spec.get_position(fig)
            w, h = FW * DPI, H * DPI
            return dict(x0=round(float(b.x0 * w), 2), x1=round(float(b.x1 * w), 2),
                        y0=round(float((1 - b.y1) * h), 2), y1=round(float((1 - b.y0) * h), 2))
        if pic == "voice":
            gp, gw = grid(H)[2:]
            ids = [("header", ga[0], False), ("lead", ga[1], True), ("backing", ga[2], True)] + \
                  ([("periodicity", gp, True)] if per else []) + [("words", gw, True), ("held", gb[:, :], False)]
        else:
            ids = [("header", ga[0], False)] + [(n, ga[1 + i], True) for i, n in enumerate(C["STEMS6"])] + \
                  [("stereo", ga[6], True), ("attacks", ga[7], True), ("sofar", gb[0], False), ("lanes", gb[1], False)]
        out[pic] = dict(w=fig.canvas.get_width_height()[0], h=fig.canvas.get_width_height()[1],
                        panels=[period_geometry(box(sp)) if i == "periodicity" else
                                dict(id=i, **box(sp), **({"time": True} if tm else {})) for i, sp, tm in ids])
    return out


def png_size(p):
    """(width, height) of a PNG file from its header, or None if it isn't one."""
    with open(p, "rb") as f:
        b = f.read(24)
    return (int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")) if b[:8] == b"\x89PNG\r\n\x1a\n" else None


def write_geometry(wd, nwin, harmony=None):
    """windows/geometry.json: for every picture of a window its size and each panel's pixel box (x0, x1 from the left
    edge; y0 = top, y1 = bottom, from the top edge), and for the semitone panels the pixel row of every labelled
    note. A page generator reads this instead of hardcoding pixels. The voice picture is described as it was
    rendered: with the periodicity panel only if the file on disk has that height."""
    pics = {}
    vp = os.path.join(wd, "000-voice.png")
    size = png_size(vp) if os.path.exists(vp) else None
    per = bool(size) and size[1] == static_geometry(True)["voice"]["h"]
    for name, g in static_geometry(per).items():
        if os.path.exists(os.path.join(wd, f"000-{name}.png")):
            pics[name] = dict(file="NNN-" + name + ".png", **g)
    if harmony:
        pics["harmony"] = dict(file="NNN-harmony.png", **harmony)
    jdump(dict(window=C["WIN"], nwin=nwin, time_axis="in a panel with time=true, x0 is the window's start and x1 is "
               "start + window seconds (also in the last, shorter window)", pictures=pics),
          os.path.join(wd, "geometry.json"))


def window_views(meta, words, vo):
    """For each window what every one of its pictures shares: span, its words, the voice changes in it, the header."""
    dur = meta["duration"]
    nwin = n_windows(dur)
    WIN = C["WIN"]
    changes = vo["changes"] if vo else []
    for k in range(nwin):
        t0 = k * WIN
        t1 = t0 + WIN
        tend = dur if k == nwin - 1 else t1
        ww = [w for w in words if t0 <= w["s"] < tend]
        chg = [c for c in changes if t0 <= c["t"] < tend and c is not changes[0]]
        cur_voice = None
        for c in changes:
            if c["t"] < t0 + 1e-3:
                cur_voice = c["voice"]
        vs = sorted(set(w["voice"] for w in ww if "voice" in w)) or ([cur_voice] if cur_voice else [])
        head = (f"window {k:03d} of {nwin - 1:03d}   {mmss_short(t0)}-{mmss_short(tend)}   "
                f"{meta.get('artist', '')} - {meta.get('title', '')}")
        if vo:
            head += (f"   voices so far: {sum(c['first'] and c['t'] < tend for c in changes)}   "
                     f"voice here: {', '.join(vs) if vs else '-'}")
        yield dict(k=k, t0=t0, t1=t1, tend=tend, ww=ww, chg=chg, cur_voice=cur_voice, head=head)


def render_harmony(W, OUT):
    """NNN-harmony.png for every window, from harmony.npz and the cached pitch tracks. Touches no other window file.
    Returns the picture's geometry."""
    meta = jload(os.path.join(W, "meta.json"))
    D = load_harmony(W)
    wp, vp, fp = (os.path.join(W, f) for f in ("words.json", "voices.json", "features.npz"))
    words = jload(wp)["words"] if os.path.exists(wp) else []
    vo = jload(vp) if os.path.exists(vp) else None
    lines = dict(bass=(np.arange(len(D["bass_f0"])) * D["hop"], D["bass_f0"]))
    if os.path.exists(fp):
        F = np.load(fp)
        lines["lead"] = (F["ft"], F["f0"])
        if "bf0" in F.files and "back_on" in F.files and bool(F["back_on"].any()):
            lines["backing"] = (F["ft"], F["bf0"])
    wd = os.path.join(OUT, "windows")
    os.makedirs(wd, exist_ok=True)
    geom, t = None, time.time()
    for v in window_views(meta, words, vo):
        sl = {n: (tt[np.searchsorted(tt, v["t0"]):np.searchsorted(tt, v["tend"])],
                  ff[np.searchsorted(tt, v["t0"]):np.searchsorted(tt, v["tend"])]) for n, (tt, ff) in lines.items()}
        tmp = os.path.join(wd, f"{v['k']:03d}-harmony.tmp.png")
        geom = draw_harmony(tmp, v["head"], v["t0"], v["t1"], v["tend"], D, v["ww"], lines=sl)
        os.replace(tmp, os.path.join(wd, f"{v['k']:03d}-harmony.png"))
    say(f"render: {n_windows(meta['duration'])} harmony pictures in {time.time() - t:.0f} s")
    return geom


def stage_render(a, W, OUT):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import gridspec
    from matplotlib.patches import Rectangle
    import textwrap, librosa
    from scipy.signal import butter, sosfiltfilt
    meta = jload(os.path.join(W, "meta.json"))
    F = np.load(os.path.join(W, "features.npz"))
    S = np.load(os.path.join(W, "sound.npz"))
    words = jload(os.path.join(W, "words.json"))["words"]
    vo = jload(os.path.join(W, "voices.json"))
    dur = meta["duration"]
    nwin = n_windows(dur)
    WIN = C["WIN"]
    ft, f0 = F["ft"], F["f0"]
    db_v, hnr_v, flat_v = F["db"], F["hnr"], F["flat"]
    use_full = F["use_full"] if "use_full" in F.files else np.zeros(len(ft), bool)
    split = jload(os.path.join(W, "lead.json")).get("model") if os.path.exists(os.path.join(W, "lead.json")) else None
    has_back = "backing" in S.files and "back_on" in F.files and bool(F["back_on"].any())
    bf0 = F["bf0"] if has_back else None
    bon = F["back_on"] if "back_on" in F.files else None
    lo, hi, colour = coch_scale(W, S)
    ear = C["SCALE"] == "ear"
    scale_txt = "'ear' scale for the song (level^%g, onsets emphasised)" % C["EAR_POWER"] if ear \
        else "one dB scale for the song"
    cf_l, hop_l = S["lead_cf"], float(S["lead_hop"])
    cf_s, hop_s = S["stem_cf"], float(S["stem_hop"])
    lead_c = colour(S["lead"], hop_l)
    stems = {n: colour(S[f"stem_{n}"], hop_s) for n in C["STEMS6"]}
    back_c = colour(S["backing"], hop_s) if has_back else None
    PD = load_periodicity(W)   # None on a cache without the stage's output: the voice picture as it was before
    mono = bool(S["mono"])
    at, low, onset = F["acc_t"], F["low"], F["onset"]
    lmin, lmax = np.percentile(low, (2, 99.8))
    omax = np.percentile(onset, 99.5)
    sof, sof_cf, B = S["sofar"], S["sofar_cf"], float(S["sofar_block"])
    lanes, lane_names = S["lanes"], [str(x) for x in S["lane_names"]]
    # the close-ups read the lead waveform (the whole vocal under a yellow band, like the measures)
    sr = 22050
    y_lead = load_mono(lead_wav(W), sr)
    y_voc = load_mono(os.path.join(W, "vocals.wav"), sr) if use_full.any() else None
    hp = butter(4, C["PITCH_HPF"], btype="highpass", fs=sr, output="sos")
    wd = os.path.join(OUT, "windows")
    os.makedirs(wd, exist_ok=True)
    for f in os.listdir(wd):  # a re-render replaces every window file (also an older layout's)
        if re.match(r"\d{3}([.-])", f) or f in ("render.json", "geometry.json"):
            os.remove(os.path.join(wd, f))
    changes = vo["changes"]
    plt.rcParams.update({"font.family": "DejaVu Sans"})
    FW, DPI, ML, MR = C["FIG_W2"], C["DPI"], C["MARG_L"], C["MARG_R"]
    ax_px = FW * DPI * (MR - ML)  # width of a timed panel, px
    t_render = time.time()
    for v in window_views(meta, words, vo):
        k, t0, t1, tend, ww, chg, cur_voice, head = (v[x] for x in ("k", "t0", "t1", "tend", "ww", "chg", "cur_voice",
                                                                  "head"))
        xt = np.arange(t0, t1 + 0.01, 1.0)
        i0, i1 = np.searchsorted(ft, t0), np.searchsorted(ft, tend)

        # ================================================================ picture 1: the voice
        H = C["VOICE_H"] + per_extra(PD is not None)
        fig = plt.figure(figsize=(FW, H), dpi=DPI)
        gs, gc, gp, gw = voice_grid(H, PD is not None)
        axh = fig.add_subplot(gs[0]); axh.axis("off")
        axh.text(0, 0.5, head + "   [the voice]", fontsize=14, va="center", ha="left", weight="bold")
        ax = fig.add_subplot(gs[1])
        nch = draw_coch(ax, lead_c, hop_l, cf_l, t0, t1, tend, lo, hi, fs=11)
        ax.plot(ft[i0:i1], hz2ch(cf_l, f0[i0:i1]), color="#00e5ff", lw=2.4)
        ax.set_ylabel(("lead vocal" if split else "vocal only") + ", Hz", fontsize=12)
        ax.text(t0 + 0.05, nch - 1.5, ("lead vocal (backing removed; yellow band on top = the whole vocal there), "
                                       if split else "vocal, ") +
                f"cochleagram: gammatone ERB channels, 5 ms frames, {scale_txt}; cyan = lead pitch",
                color="white", fontsize=10 if not ear else 9, va="top", ha="left", zorder=4)
        for w in ww:
            ax.axvline(w["s"], color="white", lw=0.6, alpha=0.35 if not w["approx"] else 0.15,
                       ls="-" if not w["approx"] else ":")
        uf = use_full[i0:i1]
        if uf.any():
            ax.fill_between(ft[i0:i1], nch - 3.5, nch - 0.5, where=uf, color="#ffd600", lw=0, step="mid", zorder=4)
        axk = fig.add_subplot(gs[2], sharex=None)
        if has_back:
            nb = draw_coch(axk, back_c, hop_s, cf_s, t0, t1, tend, lo, hi, fmax=C["BACK_FMAX"], ticks=(100, 400, 1600), fs=9)
            axk.plot(ft[i0:i1], hz2ch(cf_s, bf0[i0:i1]), color="#80deea", lw=1.2, alpha=0.6)
            bshare = float(bon[i0:i1].mean()) if i1 > i0 else 0.0
            axk.text(t0 + 0.05, nb - 1.5, f"backing: the vocal stem minus the lead (other voices, doubles, harmonies; can "
                     f"hold instrument bleed), same colour scale; faint line = its pitch; audible {bshare:.0%} of this "
                     f"window", color="white", fontsize=9.5, va="top", ha="left", zorder=4)
        else:
            axk.set_facecolor("#222222"); axk.set_yticks([]); axk.set_xlim(t0, t1)
            axk.text(t0 + 0.05, 0.5, "no backing vocals found" if split else "no lead/backing split in this run",
                     color="white", fontsize=10, va="center", ha="left", transform=axk.get_yaxis_transform())
            axk.tick_params(labelbottom=False)
        axk.set_ylabel("backing", fontsize=11)
        # word lane (time order), x ticks under it
        NR = C["WORD_ROWS"]
        timed = [ax, axk]
        if PD is not None:   # every period in the whole vocal stem at once, between the backing strip and the words
            axq = fig.add_subplot(gp)
            draw_period(axq, PD["R"], PD["hop"], 0.0, t0, t1, tend, PD["top"], PD["cents"], ww)
            timed.append(axq)
        axw = fig.add_subplot(gw)
        timed.append(axw)
        axw.set_ylabel("words", fontsize=12)
        draw_words(axw, ww, t0, t1, tend, WIN, ax_px, NR, 12)
        axw.set_xticks(xt); axw.set_xticklabels([mmss_short(x) if i % 5 == 0 else f"+{i}s" for i, x in enumerate(xt)],
                                               fontsize=10)
        for axx in timed:
            for x in xt[1:-1]:
                axx.axvline(x, color="#4fc3f7" if axx is not axw else "#bbbbbb", lw=0.5, alpha=0.3, zorder=0.5 if axx is axw else 3.5)
        for c in chg:
            for axx in timed:
                axx.axvline(c["t"], color="#76ff03" if axx is not axw else "#2e7d32", lw=3, ls="--", zorder=5)
            ax.text(c["t"] + 0.05, nch * 0.78, ("NEW VOICE: " if c["first"] else "VOICE CHANGE TO ") + c["voice"],
                    color="#76ff03", fontsize=14, weight="bold", va="top", zorder=6,
                    bbox=dict(facecolor="black", alpha=0.75, edgecolor="none", pad=3))
        # held-note close-ups
        held = held_notes(ww, ft, f0, t0, tend)
        held_txt = []
        if not held:
            axn = fig.add_subplot(gc[:, :]); axn.axis("off")
            axn.text(0.5, 0.5, f"held-note close-ups: no measured lead word holds a continuous pitch for "
                               f"{C['HELD_MIN']:g} s or more in this window", ha="center", va="center", fontsize=11,
                     color="#555555")
        for n_, (w, rs, re_) in enumerate(held):
            c0 = 0 if n_ == 0 else 3
            run = re_ - rs
            span = float(np.clip(run + 0.3, *C["HELD_SPAN"]))
            xa = rs - 0.15
            if xa + span > tend:
                xa = tend - span
            xa = max(xa, t0); xb = min(xa + span, tend)
            mid = (rs + re_) / 2
            full_here = bool(use_full[min(len(use_full) - 1, np.searchsorted(ft, mid))]) and y_voc is not None
            ysrc = y_voc if full_here else y_lead
            q0, q1 = int(xa * sr), int(xb * sr)
            seg = sosfiltfilt(hp, y_lead[q0:q1])  # pitch is always the lead stem's
            fr = f0[np.searchsorted(ft, rs):np.searchsorted(ft, re_) + 1]
            med = float(np.nanmedian(fr))
            fp, _, _ = librosa.pyin(seg.astype(np.float32), fmin=max(60.0, med / 1.5), fmax=min(1100.0, med * 1.5),
                                    sr=sr, frame_length=1024, hop_length=64, center=True)
            tp = xa + np.arange(len(fp)) * 64 / sr
            fpl = fp.copy()
            fpl[(tp < xa + 0.02) | (tp > xb - 0.02)] = np.nan
            cents = 1200 * np.log2(fpl / med)
            axc = fig.add_subplot(gc[0, c0])
            axc.axvspan(rs - xa, re_ - xa, color="#e0f7fa", zorder=0)
            axc.plot(tp - xa, cents, color="#00838f", lw=1.6)
            m10 = (ft >= xa) & (ft <= xb)
            axc.plot(ft[m10] - xa, 1200 * np.log2(f0[m10] / med), color="#00838f", lw=0.8, alpha=0.35)
            lim = np.nanpercentile(np.abs(cents), 98) if np.isfinite(cents).any() else 100
            lim = float(np.clip(lim * 1.15, 150, 700))
            axc.set_ylim(-lim, lim); axc.set_xlim(0, xb - xa)
            for cy in (-100, 0, 100):
                axc.axhline(cy, color="#999999", lw=0.6, ls="-" if cy == 0 else "--")
            axc.set_ylabel("cents", fontsize=9); axc.tick_params(labelsize=8, labelbottom=False)
            axc.set_title(f"held note {n_ + 1}: \"{w['text']}\" at {mmss(rs)}, pitch held {run:.2f} s, median "
                          f"{med:.0f} Hz ({librosa.hz_to_note(med, unicode=False)})", fontsize=9.5, loc="left", pad=3)
            axc.text(0.995, 0.97, "cents around the median; dashed = +-1 semitone; thin = 10 ms track",
                     transform=axc.transAxes, ha="right", va="top", fontsize=7.5, color="#555555")
            rows = [("level dB", db_v, "#444444"), ("HNR dB", hnr_v, "#6a1b9a"), ("flatness dB", None, "#e65100")]
            for r_, (lab, arr, col) in enumerate(rows):
                axr = fig.add_subplot(gc[1 + r_, c0])
                vals = 10 * np.log10(flat_v[:len(ft)][m10] + 1e-9) if arr is None else arr[:len(ft)][m10]
                if lab == "level dB":
                    vals = np.maximum(vals - np.nanmax(vals), -60.0)  # dB below the excerpt's peak
                axr.axvspan(rs - xa, re_ - xa, color="#e0f7fa", zorder=0)
                axr.plot(ft[m10] - xa, vals, color=col, lw=1.2)
                axr.set_xlim(0, xb - xa); axr.set_ylabel(lab, fontsize=8, rotation=0, ha="right", va="center")
                axr.tick_params(labelsize=7, labelbottom=(r_ == 2))
                if np.isfinite(vals).any():
                    lo_, hi_ = np.nanmin(vals), np.nanmax(vals)
                    pad = max(1.0, 0.1 * (hi_ - lo_))
                    axr.set_ylim(lo_ - pad, hi_ + pad); axr.set_yticks([round(lo_), round(hi_)])
                if r_ == 2:
                    axr.set_xlabel(f"s after {mmss(xa)}", fontsize=8, labelpad=1)
            axv = fig.add_subplot(gc[:, c0 + 1])
            per = C["HELD_CYCLES"] / med
            ya = ysrc[int((mid - per / 2) * sr):int((mid + per / 2) * sr)]
            axv.plot(np.arange(len(ya)) / sr * 1000, ya / (np.max(np.abs(ya)) + 1e-9), color="#222222", lw=1.0)
            axv.axhline(0, color="#bbbbbb", lw=0.5)
            axv.set_ylim(-1.1, 1.1); axv.set_yticks([]); axv.tick_params(labelsize=7)
            axv.set_title(f"waveform, {C['HELD_CYCLES']} cycles", fontsize=9, pad=3)
            axv.set_xlabel(f"ms from {mmss(mid - per / 2)} (scaled to fit)", fontsize=8, labelpad=1)
            held_txt.append(f"held note {n_ + 1}: {w['text']}  {mmss(rs)}-{mmss(re_)}  pitch held {run:.2f} s  "
                            f"median {med:.1f} Hz")
        # lyric lines
        out_lines, by_line = [], {}
        for w in ww:
            by_line.setdefault(w["line"], []).append(w)
        for li in sorted(by_line):
            lw = by_line[li]
            v = lw[0]["voice"]
            mark = ""
            for c in chg:
                if c["line"] == li:
                    mark = (">> NEW VOICE " + v + " >> ") if c["first"] else (">> VOICE CHANGE TO " + v + " >> ")
            parts = []
            for w in lw:
                s_ = ("~" if w["approx"] else "") + w["text"]
                if w.get("stem") == "backing" and not w["approx"]:
                    s_ = "{" + s_ + "}"
                if w["tags"]:
                    s_ += " [" + ", ".join(w["tags"]) + "]"
                parts.append(s_)
            out_lines.append(f"{mmss_short(lw[0]['s'])} {v}: " + mark + " ".join(parts))
        if not out_lines:
            out_lines = ["(no lyric words start in this window)"]
        axt = fig.add_axes([0.012, 0.004 * C["VOICE_H"] / H, 0.976, 1.6 / H]); axt.axis("off")
        fs = 15
        while True:
            width = int(FW * 0.97 * 72 / (0.61 * fs))
            wrapped = []
            for L in out_lines:
                wrapped += textwrap.wrap(L, width=width, subsequent_indent="        ")
            if len(wrapped) * fs * 1.32 / 72 <= 1.42 or fs <= 8:
                break
            fs -= 1
        axt.text(0, 0.99, "\n".join(wrapped), fontsize=fs, va="top", ha="left", family="DejaVu Sans Mono",
                 linespacing=1.32)
        axt.text(1, 0.0, "~word = time estimated   {word} = found in the backing voices (blue)   [tags] = how a word "
                         f"differs from this voice's own usual across the song (|z| >= {C['Z_TAG']}), red   "
                         "(...) = the lyric's ad-lib marks", fontsize=9, ha="right", va="bottom", color="#555555",
                 transform=axt.transAxes)
        fig.savefig(os.path.join(wd, f"{k:03d}-voice.png"), dpi=DPI)
        plt.close(fig)

        # ================================================================ picture 2: the band
        H2 = C["BAND_H"]
        fig = plt.figure(figsize=(FW, H2), dpi=DPI)
        gs, g2 = band_grid(H2)
        axh = fig.add_subplot(gs[0]); axh.axis("off")
        axh.text(0, 0.5, head + "   [the band]", fontsize=14, va="center", ha="left", weight="bold")
        panels = []
        for n_, name in enumerate(C["STEMS6"]):
            axs = fig.add_subplot(gs[1 + n_])
            nn = draw_coch(axs, stems[name], hop_s, cf_s, t0, t1, tend, lo, hi, ticks=(100, 300, 1000, 3000), fs=9)
            axs.set_ylabel(name, fontsize=12)
            if n_ == 0:
                axs.text(t0 + 0.05, nn - 1.5, f"five stems of the whole song (Demucs {C['BAND_MODEL']}; the voice is in "
                         "the other picture), cochleagrams on the lead's colour scale: faint = quiet, black = silent; "
                         "y in Hz" + ("; 'ear' scale: level^%g, onsets emphasised" % C["EAR_POWER"] if ear else ""),
                         color="white", fontsize=9.5, va="top", ha="left", zorder=4)
            panels.append(axs)
        axp = fig.add_subplot(gs[6])
        if mono:
            axp.set_facecolor("#222222"); axp.set_yticks([]); axp.set_xlim(t0, t1)
            axp.text(t0 + 0.05, 0.5, "song.wav is mono (left and right are the same): no stereo picture", color="white",
                     fontsize=11, va="center", ha="left", transform=axp.get_yaxis_transform())
        else:
            P, ph = S["pan"], float(S["pan_hop"])
            ptop = float(S["pan_top"])
            j0, j1 = int(np.floor(t0 / ph)), int(min(P.shape[1], np.floor(tend / ph) + 1))
            axp.imshow(P[::-1, j0:j1].astype(np.float32), aspect="auto", origin="lower", cmap="viridis",
                       vmin=ptop - C["PAN_RANGE_DB"], vmax=ptop, extent=[(j0 - 0.5) * ph, (j1 - 0.5) * ph, -1, 1],
                       interpolation="antialiased")
            axp.set_xlim(t0, t1); axp.set_ylim(-1, 1)
            axp.set_yticks([0.9, 0, -0.9]); axp.set_yticklabels(["L", "centre", "R"], fontsize=9)
            axp.text(t0 + 0.05, 0.93, "stereo: energy at each left-right position of the whole mix (per STFT bin, "
                     "(|R|-|L|)/(|R|+|L|), weighted by its energy; one scale for the song)", color="white", fontsize=9.5,
                     va="top", ha="left")
            if tend < t1:
                axp.axvspan(tend, t1, color="#bbbbbb")
        axp.set_ylabel("stereo", fontsize=12); axp.tick_params(labelbottom=False); axp.minorticks_off()
        axb = fig.add_subplot(gs[7])
        j0, j1 = np.searchsorted(at, t0), np.searchsorted(at, tend)
        axb.fill_between(at[j0:j1], 0, np.clip((low[j0:j1] - lmin) / (lmax - lmin), 0, 1), color="#ff6d00", alpha=0.75, lw=0)
        axb.vlines(at[j0:j1], 0, -np.clip(onset[j0:j1] / omax, 0, 1), color="#222222", lw=1.0)
        axb.axhline(0, color="#999999", lw=0.5)
        axb.set_ylim(-1.05, 1.05); axb.set_xlim(t0, t1); axb.set_yticks([0.5, -0.5])
        axb.set_yticklabels([f"bass <{int(C['LOW_BAND'])}Hz", "attacks"], fontsize=9); axb.tick_params(axis="y", length=0)
        axb.set_xticks(xt); axb.set_xticklabels([mmss_short(x) if i % 5 == 0 else f"+{i}s" for i, x in enumerate(xt)],
                                               fontsize=10)
        if tend < t1:
            axb.axvspan(tend, t1, color="#bbbbbb")
            axb.text((tend + t1) / 2, 0, "song ends", ha="center", va="center", fontsize=12)
        for axx in panels + [axp, axb]:
            for x in xt[1:-1]:
                axx.axvline(x, color="#4fc3f7" if axx is not axb else "#bbbbbb", lw=0.5, alpha=0.3,
                            zorder=3.5 if axx is not axb else 0.5)
        # song so far: 0 .. end of this window only (blocks that end by then), at a fixed width for the whole song
        nbk = int(np.floor(tend / B + 1e-6))
        axo = fig.add_subplot(g2[0]); axl = fig.add_subplot(g2[1])
        for axx, M, lo_, hi_ in ((axo, sof, float(S["sofar_top"]) - C["SOFAR_RANGE_DB"], float(S["sofar_top"])),
                                 (axl, lanes, float(S["lanes_top"]) - C["SOFAR_RANGE_DB"], float(S["lanes_top"]))):
            axx.set_facecolor("#e8e8e8")
            nr = M.shape[0]
            if nbk > 0:
                axx.imshow(M[:, :nbk].astype(np.float32), aspect="auto", origin="lower" if axx is axo else "upper",
                           cmap="magma", vmin=lo_, vmax=hi_, extent=[0, nbk * B, -0.5, nr - 0.5] if axx is axo
                           else [0, nbk * B, nr - 0.5, -0.5], interpolation="antialiased")
            axx.set_xlim(0, dur); axx.set_ylim((-0.5, nr - 0.5) if axx is axo else (nr - 0.5, -0.5))
            axx.add_patch(Rectangle((t0, -0.5), tend - t0, nr, fill=False, edgecolor="#00e5ff", lw=2.0, zorder=5))
            axx.minorticks_off()
        tk = [f for f in (100, 1000, 6000) if sof_cf[0] <= f <= sof_cf[-1]]
        axo.set_yticks(hz2ch(sof_cf, tk)); axo.set_yticklabels([f"{f / 1000:g}k" if f >= 1000 else str(f) for f in tk],
                                                               fontsize=7)
        axo.tick_params(labelbottom=False)
        axo.set_ylabel("so far\n(mix)", fontsize=9)
        axo.text(0.995, 0.92, f"song so far: 0:00 to the end of this window only (grey = not heard yet), "
                 f"{B:g} s blocks; cyan box = this window", transform=axo.transAxes, ha="right", va="top",
                 fontsize=8.5, color="#333333", zorder=6)
        axl.set_yticks(range(len(lane_names))); axl.set_yticklabels(lane_names, fontsize=8)
        mt = np.arange(0, dur + 1e-6, 30.0)
        axl.set_xticks(mt); axl.set_xticklabels([mmss_short(x) for x in mt], fontsize=8)
        axo.set_xticks(mt)
        fig.savefig(os.path.join(wd, f"{k:03d}-band.png"), dpi=DPI)
        plt.close(fig)

        # ================================================================ text fallback
        T = [f"window {k:03d} of {nwin - 1:03d}  {mmss(t0)}-{mmss(tend)}  {meta.get('artist', '')} - {meta.get('title', '')}",
             f"voice at window start: {cur_voice or '-'}"]
        for c in chg:
            T.append(f"{mmss(c['t'])}  " + (f"NEW VOICE {c['voice']}" if c["first"] else f"VOICE CHANGE -> {c['voice']}"))
        T.append("")
        T.append("lines:")
        T += ["  " + L for L in out_lines]
        T.append("")
        T.append("words (start-end, voice, ~ = time estimated, {} = found in the backing voices, tags; "
                 "z = robust z vs this voice's baseline; only exact lead words are measured):")
        for w in ww:
            z = w["z"]
            zs = " ".join(f"{n}={z[key]:+.1f}" for n, key in (("loud", "db"), ("pitch", "st"), ("len/syl", "dur_syl"),
                                                              ("move", "move"), ("flat", "flat"), ("hnr", "hnr"))
                          if z.get(key) is not None) if measured(w) else ""
            mk = "~" if w["approx"] else ("{" if w.get("stem") == "backing" else " ")
            T.append(f"  {mmss(w['s'])}-{mmss(w['e'])}  {w['voice']}  {mk}{w['text']:<18} "
                     f"{'; '.join(w['tags']):<36} {zs}")
        if held_txt:
            T.append("")
            T += held_txt
        vf = np.isfinite(f0[i0:i1]).mean() if i1 > i0 else 0
        T.append("")
        T.append(f"voiced (pitched) frames of the lead in window: {vf:.0%}; words timed exactly: "
                 f"{sum(not w['approx'] for w in ww)}/{len(ww)}"
                 + (f"; backing vocals audible: {float(bon[i0:i1].mean()) if i1 > i0 else 0:.0%}" if has_back else ""))
        open(os.path.join(wd, f"{k:03d}.txt"), "w").write("\n".join(T) + "\n")
    say(f"render: {nwin} windows x 2 pictures in {time.time() - t_render:.0f} s -> {wd}")
    harm = render_harmony(W, OUT) if os.path.exists(os.path.join(W, "harmony.npz")) else None
    write_geometry(wd, nwin, harm)
    jdump(dict(window=WIN, layout=LAYOUT3 if harm else LAYOUT, nwin=nwin, **({"scale": C["SCALE"]} if ear else {})),
          os.path.join(wd, "render.json"))   # no "scale" key = db (as every render before the switch existed)


# ------------------------------------------------------------------ close-ups (`look`): recomputed at a finer step
def excerpt(path, a, b, sr, mono=True):
    """Audio of [a, b) seconds only (nothing after b is read)."""
    import librosa
    y, _ = librosa.load(path, sr=sr, mono=mono, offset=a, duration=max(0.0, b - a))
    return y


def look_support(W):
    """Which close-up pictures a cache can give: each needs the stems and the whole-song scales it is drawn with."""
    def has(*fs):
        return all(os.path.exists(os.path.join(W, f)) for f in fs)
    out = []
    if has("sound.npz", "features.npz") and os.path.exists(lead_wav(W)):
        out.append("voice")
    if has("sound.npz", "features.npz", "song.wav", "accomp.wav", *[os.path.join("band", f"{n}.wav") for n in C["STEMS6"]]):
        out.append("band")
    if has("harmony.npz", "accomp.wav", os.path.join("band", "bass.wav")):
        out.append("harmony")
    return out


def look_steps(L):
    """Frame steps (samples at 22050 Hz) of a close-up L seconds long: each window step scaled by L / window, so a
    close-up has as many frames per pixel as a window and (window / L) times its time detail."""
    k, sr = L / C["WIN"], 22050
    return dict(lead=max(1, int(round(C["COCH_HOP_LEAD"] * k * sr))), stem=max(1, int(round(C["COCH_HOP_STEM"] * k * sr))),
                cq=max(16, 16 * int(round(C["HARM_HOP"] * k / 16))), pan=max(32, int(round(C["PAN_HOP"] * k * sr))),
                attacks=max(16, int(round(2 * int(C["HOP"] * sr) * k))),
                periodicity=max(1, int(round(C["PER_HOP"] * k * C["PER_SR"]))))


def pan_image(Lc, Rc, sr, hop_p):
    """The stereo picture: per STFT bin pan = (|R| - |L|) / (|R| + |L|), weighted by the bin's energy, summed into
    PAN_BINS rows (row 0 = left). dB."""
    import librosa
    Ls = np.abs(librosa.stft(Lc, n_fft=2048, hop_length=hop_p))
    Rs = np.abs(librosa.stft(Rc, n_fft=2048, hop_length=hop_p))
    keepf = librosa.fft_frequencies(sr=sr, n_fft=2048) >= C["PAN_FMIN"]
    Ls, Rs = Ls[keepf], Rs[keepf]
    pan = (Rs - Ls) / (Rs + Ls + 1e-9)
    en = Ls ** 2 + Rs ** 2
    PB = C["PAN_BINS"]
    bi = np.clip(np.round((pan + 1) / 2 * (PB - 1)).astype(int), 0, PB - 1)
    H = np.zeros((PB, pan.shape[1]))
    cols = np.broadcast_to(np.arange(pan.shape[1]), pan.shape)
    np.add.at(H, (bi.ravel(), cols.ravel()), en.ravel())
    return 10 * np.log10(H + 1e-10)


def look_render(W, st, n, ts, te, limit, out, kinds):
    """Render close-up n of [ts, te] into `out` from the cached audio, recomputed at look_steps(): only audio in
    [ts - LOOK_PAD, min(te + LOOK_PAD, limit)] is read (limit = the end of what is revealed), and every colour scale
    and gate is the whole song's, as in the windows. Returns (files written, info for NN.json)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import gridspec
    import librosa
    from scipy.signal import butter, sosfiltfilt
    sr = 22050
    L = te - ts
    a0, b0 = max(0.0, ts - C["LOOK_PAD"]), min(limit, te + C["LOOK_PAD"])
    steps = look_steps(L)
    FW, DPI, ML, MR = C["FIG_W2"], C["DPI"], C["MARG_L"], C["MARG_R"]
    ax_px = FW * DPI * (MR - ML)
    plt.rcParams.update({"font.family": "DejaVu Sans"})
    wp = os.path.join(W, "words.json")
    ww = [w for w in (jload(wp)["words"] if os.path.exists(wp) else []) if ts <= w["s"] < te]
    head = (f"close-up {n:02d}   {mmss(ts)}-{mmss(te)} ({L:g} s, {C['WIN'] / L:.1f}x a window's time detail)   "
            f"{st.get('artist', '')} - {st.get('title', '')}")
    files, geom = [], {}
    xt = np.arange(np.ceil(ts * 2 - 1e-6) / 2, te + 1e-6, 0.5)
    F = np.load(os.path.join(W, "features.npz")) if os.path.exists(os.path.join(W, "features.npz")) else None
    S = np.load(os.path.join(W, "sound.npz")) if os.path.exists(os.path.join(W, "sound.npz")) else None

    def grid_lines(axes, last):
        for axx in axes:
            for x in xt:
                if ts < x < te:
                    axx.axvline(x, color="#4fc3f7" if axx is not last else "#bbbbbb", lw=0.5, alpha=0.3,
                                zorder=0.5 if axx is last else 3.5)

    # the lead pitch, re-tracked on the excerpt at LOOK_PYIN_HOP and kept where the song's 10 ms track has a pitch
    lead_line = back_line = None
    if F is not None and os.path.exists(lead_wav(W)):
        ft, f0 = F["ft"], F["f0"]
        yl = excerpt(lead_wav(W), a0, b0, sr)
        hp = butter(4, C["PITCH_HPF"], btype="highpass", fs=sr, output="sos")
        fine, _, _ = librosa.pyin(sosfiltfilt(hp, yl).astype(np.float32), fmin=C["F0_FLOOR"], fmax=C["F0_CEIL"], sr=sr,
                                  frame_length=2048, hop_length=C["LOOK_PYIN_HOP"], center=True)
        tf = a0 + np.arange(len(fine)) * C["LOOK_PYIN_HOP"] / sr
        near = f0[np.clip(np.round(tf / (ft[1] - ft[0])).astype(int), 0, len(f0) - 1)]
        fine = np.where(np.isfinite(fine), fine, near)   # the song's track where the excerpt's has a gap
        fine[~np.isfinite(near)] = np.nan
        m = (tf >= ts) & (tf <= te)
        lead_line = (tf[m], fine[m])
        if "bf0" in F.files and "back_on" in F.files and bool(F["back_on"].any()):
            m = (ft >= ts) & (ft <= te)
            back_line = (ft[m], F["bf0"][m])

    # ---------------- the voice
    ear = C["SCALE"] == "ear"
    scale_txt = "the song's loudness scale ('ear')" if ear else "the song's dB scale"
    # the periodicity panel: only on a cache that has the stage's output (its whole-song scale) and the vocal stem
    PD = load_periodicity(W) if "voice" in kinds and os.path.exists(os.path.join(W, "vocals.wav")) else None
    if PD is None:
        steps.pop("periodicity")
    if "voice" in kinds:
        lo, hi, colour = coch_scale(W, S)
        cf_l, cf_s = S["lead_cf"], S["stem_cf"]
        hl, hs = steps["lead"] / sr, steps["stem"] / sr
        lead = cochleagram(yl, sr, cf_l, hl)
        use_full = F["use_full"] if "use_full" in F.files else np.zeros(len(ft), bool)
        tl = a0 + (np.arange(lead.shape[1]) + 0.5) * hl
        uf = use_full[np.clip(np.searchsorted(ft, tl), 0, len(ft) - 1)]
        if uf.any():
            voc = cochleagram(excerpt(os.path.join(W, "vocals.wav"), a0, b0, sr), sr, cf_l, hl)
            n_ = min(lead.shape[1], voc.shape[1])
            lead[:, :n_][:, uf[:n_]] = voc[:, :n_][:, uf[:n_]]
        lead = colour(lead, hl)
        H = C["LOOK_VOICE_H"] + per_extra(PD is not None)
        fig = plt.figure(figsize=(FW, H), dpi=DPI)
        gs, gp, gw = look_voice_grid(H, PD is not None)
        axh = fig.add_subplot(gs[0]); axh.axis("off")
        fit_header(fig, axh.text(0, 0.5, head + "   [the voice, close-up]", fontsize=13.5, va="center", ha="left",
                                 weight="bold"))
        ax = fig.add_subplot(gs[1])
        nch = draw_coch(ax, lead, hl, cf_l, ts, te, te, lo, hi, fs=11, toff=a0)
        ax.plot(lead_line[0], hz2ch(cf_l, lead_line[1]), color="#00e5ff", lw=2.4)
        ax.set_ylabel("lead vocal, Hz", fontsize=12)
        ax.text(ts + 0.004 * L, nch - (4.5 if uf.any() else 1.5), f"lead vocal, cochleagram recomputed for this span at {hl * 1000:.2f} ms "
                f"frames (a window: {C['COCH_HOP_LEAD'] * 1000:g} ms), {scale_txt}; cyan = lead pitch "
                f"re-tracked at {C['LOOK_PYIN_HOP'] / sr * 1000:.1f} ms; yellow band on top = the whole vocal there",
                color="white", fontsize=9.5, va="top", ha="left", zorder=4)
        for w in ww:
            ax.axvline(w["s"], color="white", lw=0.6, alpha=0.35 if not w["approx"] else 0.15,
                       ls="-" if not w["approx"] else ":")
        if uf.any():
            ax.fill_between(tl, nch - 3.5, nch - 0.5, where=uf, color="#ffd600", lw=0, step="mid", zorder=4)
        axk = fig.add_subplot(gs[2])
        bp = os.path.join(W, "backing.wav")
        if back_line is not None and os.path.exists(bp):
            back = colour(cochleagram(excerpt(bp, a0, b0, sr), sr, cf_s, hs), hs)
            nb = draw_coch(axk, back, hs, cf_s, ts, te, te, lo, hi, fmax=C["BACK_FMAX"], ticks=(100, 400, 1600), fs=9,
                           toff=a0)
            axk.plot(back_line[0], hz2ch(cf_s, back_line[1]), color="#80deea", lw=1.2, alpha=0.6)
            axk.text(ts + 0.004 * L, nb - 1.5, f"backing: the vocal stem minus the lead, {hs * 1000:.2f} ms frames, "
                     "same colour scale; faint line = its pitch (the song's 10 ms track)", color="white", fontsize=9.5,
                     va="top", ha="left", zorder=4)
        else:
            axk.set_facecolor("#222222"); axk.set_yticks([]); axk.set_xlim(ts, te)
            axk.text(0.004, 0.5, "no backing vocals in this run", color="white", fontsize=10, va="center", ha="left",
                     transform=axk.transAxes)
            axk.tick_params(labelbottom=False)
        axk.set_ylabel("backing", fontsize=11)
        timed = [("lead", ax), ("backing", axk)]
        if PD is not None:   # the whole vocal stem's periods, measured again on this span at the finer step
            hq = steps["periodicity"]
            R = periodicity(excerpt(os.path.join(W, "vocals.wav"), a0, b0, C["PER_SR"]), C["PER_SR"], hq)
            axq = fig.add_subplot(gp)
            draw_period(axq, R, hq / C["PER_SR"], a0, ts, te, te, PD["top"], PD["cents"], ww,
                        note=f", at {hq / C['PER_SR'] * 1000:.2f} ms steps ({C['PER_FRAME'] * 1000:g} ms frames)")
            timed.append(("periodicity", axq))
        axw = fig.add_subplot(gw)
        timed.append(("words", axw))
        axw.set_ylabel("words", fontsize=12)
        draw_words(axw, ww, ts, te, te, L, ax_px, C["LOOK_WORD_ROWS"], 12)
        time_ticks(axw, ts, te, look=True)
        grid_lines([x for _, x in timed], axw)
        geom["voice"] = dict(w=fig.canvas.get_width_height()[0], h=fig.canvas.get_width_height()[1], panels=[
            period_geometry(px_box(fig, x)) if i == "periodicity" else dict(id=i, **px_box(fig, x), time=True)
            for i, x in timed])
        fig.savefig(os.path.join(out, f"{n:02d}-voice.png"), dpi=DPI)
        plt.close(fig)
        files.append(f"{n:02d}-voice.png")

    # ---------------- the band
    if "band" in kinds:
        lo, hi, colour = coch_scale(W, S)
        cf_s, hs = S["stem_cf"], steps["stem"] / sr
        H2 = C["LOOK_BAND_H"]
        fig = plt.figure(figsize=(FW, H2), dpi=DPI)
        gs = gridspec.GridSpec(8, 1, height_ratios=[0.32] + [1.12] * 5 + [1.15, 0.7], hspace=0.07, left=ML, right=MR,
                               top=1 - 0.08 / H2, bottom=0.3 / H2)
        axh = fig.add_subplot(gs[0]); axh.axis("off")
        fit_header(fig, axh.text(0, 0.5, head + "   [the band, close-up]", fontsize=13.5, va="center", ha="left",
                                 weight="bold"))
        panels = []
        for i, name in enumerate(C["STEMS6"]):
            axs = fig.add_subplot(gs[1 + i])
            M = colour(cochleagram(excerpt(os.path.join(W, "band", f"{name}.wav"), a0, b0, sr), sr, cf_s, hs), hs)
            nn = draw_coch(axs, M, hs, cf_s, ts, te, te, lo, hi, ticks=(100, 300, 1000, 3000), fs=9, toff=a0)
            axs.set_ylabel(name, fontsize=12)
            if i == 0:
                axs.text(ts + 0.004 * L, nn - 1.5, f"the five stems, cochleagrams recomputed for this span at "
                         f"{hs * 1000:.2f} ms frames (a window: {C['COCH_HOP_STEM'] * 1000:g} ms), {scale_txt}",
                         color="white", fontsize=9.5, va="top", ha="left", zorder=4)
            panels.append((name, axs))
        axp = fig.add_subplot(gs[6])
        if bool(S["mono"]):
            axp.set_facecolor("#222222"); axp.set_yticks([]); axp.set_xlim(ts, te)
            axp.text(0.004, 0.5, "song.wav is mono: no stereo picture", color="white", fontsize=11, va="center",
                     ha="left", transform=axp.transAxes)
        else:
            ys = excerpt(os.path.join(W, "song.wav"), a0, b0, sr, mono=False)
            ph = steps["pan"] / sr
            P = pan_image(ys[0], ys[1], sr, steps["pan"])
            ptop = float(S["pan_top"])
            j0 = max(0, int(np.ceil((ts - a0) / ph - 1e-9))); j1 = min(P.shape[1], int(np.floor((te - a0) / ph + 1e-9)) + 1)
            axp.imshow(P[::-1, j0:j1].astype(np.float32), aspect="auto", origin="lower", cmap="viridis",
                       vmin=ptop - C["PAN_RANGE_DB"], vmax=ptop, extent=[a0 + (j0 - 0.5) * ph, a0 + (j1 - 0.5) * ph, -1, 1],
                       interpolation="antialiased")
            axp.set_xlim(ts, te); axp.set_ylim(-1, 1)
            axp.set_yticks([0.9, 0, -0.9]); axp.set_yticklabels(["L", "centre", "R"], fontsize=9)
            axp.text(ts + 0.004 * L, 0.93, f"stereo: energy at each left-right position of the whole mix, "
                     f"{ph * 1000:.1f} ms steps (each still a 93 ms analysis frame); the song's scale", color="white",
                     fontsize=9.5, va="top", ha="left")
        axp.set_ylabel("stereo", fontsize=12); axp.tick_params(labelbottom=False); axp.minorticks_off()
        axb = fig.add_subplot(gs[7])
        ya = excerpt(os.path.join(W, "accomp.wav"), a0, b0, sr)
        ha = steps["attacks"]
        Sa = np.abs(librosa.stft(ya, n_fft=4096, hop_length=ha)) ** 2
        low = 10 * np.log10(Sa[librosa.fft_frequencies(sr=sr, n_fft=4096) <= C["LOW_BAND"]].sum(0) + 1e-10)
        # attacks: the same spectral flux as the windows' (each frame against the one 20 ms before it)
        ons = librosa.onset.onset_strength(y=ya, sr=sr, hop_length=ha, lag=max(1, int(round(2 * int(C["HOP"] * sr) / ha))))
        at = a0 + np.arange(len(low)) * ha / sr
        lmin, lmax = np.percentile(F["low"], (2, 99.8))
        omax = np.percentile(F["onset"], 99.5)
        m = (at >= ts) & (at <= te)
        axb.fill_between(at[m], 0, np.clip((low[m] - lmin) / (lmax - lmin), 0, 1), color="#ff6d00", alpha=0.75, lw=0)
        axb.vlines(at[m], 0, -np.clip(ons[:len(at)][m] / omax, 0, 1), color="#222222", lw=1.0)
        axb.axhline(0, color="#999999", lw=0.5)
        axb.set_ylim(-1.05, 1.05); axb.set_xlim(ts, te); axb.set_yticks([0.5, -0.5])
        axb.set_yticklabels([f"bass <{int(C['LOW_BAND'])}Hz", "attacks"], fontsize=9); axb.tick_params(axis="y", length=0)
        time_ticks(axb, ts, te, look=True)
        grid_lines([x for _, x in panels] + [axp, axb], axb)
        geom["band"] = dict(w=int(round(FW * DPI)), h=int(round(H2 * DPI)), panels=[
            dict(id=i, **px_box(fig, x), time=True) for i, x in panels + [("stereo", axp), ("attacks", axb)]])
        fig.savefig(os.path.join(out, f"{n:02d}-band.png"), dpi=DPI)
        plt.close(fig)
        files.append(f"{n:02d}-band.png")

    # ---------------- the notes
    if "harmony" in kinds:
        Hs = load_harmony(W)
        ya = excerpt(os.path.join(W, "accomp.wav"), a0, b0, sr)
        yb = excerpt(os.path.join(W, "band", "bass.wav"), a0, b0, sr)
        n_ = min(len(ya), len(yb))
        D = harmony_data(ya[:n_], yb[:n_], sr, steps["cq"], Hs["cents"])
        D.update({k: Hs[k] for k in ("cq_top", "bcq_top", "chroma_top", "bass_gate")})
        bf = D["bass_f0"].copy()
        bf[D["bass_db"] < D["bass_gate"]] = np.nan
        tb = a0 + np.arange(len(bf)) * D["hop"]
        m = (tb >= ts) & (tb <= te)
        lines = dict(bass=(tb[m], bf[m]))
        if lead_line is not None:
            lines["lead"] = lead_line
        if back_line is not None:
            lines["backing"] = back_line
        geom["harmony"] = draw_harmony(os.path.join(out, f"{n:02d}-harmony.png"), head, ts, te, te, D, ww, toff=a0,
                                       lines=lines, look=True)
        files.append(f"{n:02d}-harmony.png")
    info = dict(look=n, start=ts, end=te, window=C["WIN"], steps_ms={k: round(v / sr * 1000, 3) for k, v in steps.items()},
                time_axis="in every panel x0 is start and x1 is end", pictures=geom)
    # the words of the span, as text
    T = [f"close-up {n:02d}  {mmss(ts)}-{mmss(te)}  {st.get('artist', '')} - {st.get('title', '')}",
         "pictures: " + ", ".join(files), "",
         "words starting in this span (start-end, ~ = time estimated, {} = found in the backing voices, tags):"]
    for w in ww:
        mk = "~" if w["approx"] else ("{" if w.get("stem") == "backing" else " ")
        T.append(f"  {mmss(w['s'])}-{mmss(w['e'])}  {w.get('voice', '-')}  {mk}{w['text']:<18} {'; '.join(w.get('tags', []))}")
    if not ww:
        T.append("  (none)")
    open(os.path.join(out, f"{n:02d}.txt"), "w").write("\n".join(T) + "\n")
    jdump(info, os.path.join(out, f"{n:02d}.json"))
    return files + [f"{n:02d}.txt"], info


STAGES = ["fetch", "separate", "bands", "lead", "transcribe", "align", "voices", "features", "sound", "periodicity",
          "harmony", "render"]
OUTPUTS = dict(fetch="meta.json", separate="vocals.wav", bands=os.path.join("band", "other.wav"), lead="lead.wav",
               transcribe="whisper.json", align="align.json", voices="voices.json", features="words.json",
               sound="sound.npz", periodicity="periodicity.npz", harmony="harmony.npz", render=None)
# what each stage reads: a stage runs if its output is missing, if --redo names it or something it depends on, or if
# a stage it depends on ran (so adding the band view to an old work dir runs only bands, sound, harmony and render,
# and adding the harmony picture to a two-picture work dir runs only harmony and render; a work dir made before the
# periodicity panel runs only periodicity, which reads vocals.wav alone, and render)
DEPS = dict(fetch=[], separate=["fetch"], bands=["fetch"], lead=["separate"], transcribe=["lead"],
            align=["transcribe", "lead", "separate"], voices=["align"], features=["voices", "align", "lead", "separate"],
            sound=["bands", "features", "lead", "separate"], periodicity=["separate"], harmony=["separate", "bands"],
            render=["sound", "periodicity", "harmony", "features", "voices", "align"])


def downstream(st):
    """st and every stage that depends on it, directly or not."""
    out = {st}
    for s in STAGES:
        if any(d in out for d in DEPS[s]):
            out.add(s)
    return out


def plan_stages(W, redo=None, only=None):
    """The stages `prepare` will run on work dir W, in order: those whose output is missing, --redo's stage and all
    that depend on it, and every stage that reads from one that runs. Render always runs."""
    redo = downstream(redo) if redo else set()
    ran = []
    for st in STAGES:
        if only and st != only:
            continue
        o = OUTPUTS[st]
        if not only and o and os.path.exists(os.path.join(W, o)) and st not in redo \
                and not any(d in ran for d in DEPS[st]):
            continue
        ran.append(st)
    return ran


FFMPEG = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/usr/local/bin/ffprobe"


# ------------------------------------------------------------------ seal (same rules as v2's listen.py, own labels)
LABELS = ("feel", "voice", "band", "words")      # required in every window note of a first listen
OPTIONAL = ("expect",)                         # accepted, not required
LABEL_HELP = "feel: / voice: / band: / words: (expect: optional)"
LABEL_HELP3 = "feel: / voice: / band: / words: (harmony: and expect: optional)"
LEGACY_LABELS = ("feel", "voice", "words", "expect")  # runs prepared before the band view: all four required
LEGACY_HELP = "feel: / voice: / words: / expect:"
SECOND_LABELS = ("feel", "harmony", "changed")   # required in every window note of a second listen
SECOND_HELP = "feel: / harmony: / changed: (expect: optional)"
LAYOUT = "voice+band"                          # state.json marker of a two-picture run
LAYOUT3 = "voice+band+harmony"                 # ... of a three-picture run
SECOND = "second"                              # ... of a second listen (`again`): the harmony picture + the first note
_LINE = re.compile(r"^[\s>*\-_#]*(" + "|".join(LABELS + OPTIONAL + ("harmony", "changed")) + r")[\s*_]*:[\s*_]*(.*)$",
                   re.I)
_SAW = re.compile(r"^[\s>*\-_#]*saw[\s*_]*:[\s*_]*(.*)$", re.I)


def run_labels(s):
    """(required labels, help text) for a run, from its state.json: older runs keep their four labels."""
    lay = s.get("layout")
    if lay == SECOND:
        return SECOND_LABELS, SECOND_HELP
    if lay == LAYOUT3:
        return LABELS, LABEL_HELP3
    return (LABELS, LABEL_HELP) if lay == LAYOUT else (LEGACY_LABELS, LEGACY_HELP)


def first_files(layout, k):
    """The window files of a first listen: three pictures, two (before the harmony picture) or one (before the band
    view), and the text."""
    if layout == LAYOUT3:
        return [f"{k:03d}-voice.png", f"{k:03d}-band.png", f"{k:03d}-harmony.png", f"{k:03d}.txt"]
    if layout == LAYOUT:
        return [f"{k:03d}-voice.png", f"{k:03d}-band.png", f"{k:03d}.txt"]
    return [f"{k:03d}.png", f"{k:03d}.txt"]


def window_files(s, k):
    """The files `next` reveals for window k, as (name in RUN/windows, source path). A second listen gets the harmony
    picture, the text and the listener's own first-listen note (and with --full the first listen's pictures too)."""
    wd = os.path.join(s["work"], "windows")
    if s.get("layout") != SECOND:
        return [(f, os.path.join(wd, f)) for f in first_files(s.get("layout"), k)]
    fs = [f for f in first_files(s.get("first_layout"), k) if f.endswith(".png")] if s.get("full") else []
    fs = [f for f in fs if "harmony" not in f] + [f"{k:03d}-harmony.png", f"{k:03d}.txt"]
    return [(f, os.path.join(wd, f)) for f in fs] + \
           [(f"{k:03d}-first.md", os.path.join(s["first"], "notes", f"{k:03d}.md"))]


def layout_words(s):
    lay = s.get("layout")
    if lay == SECOND:
        return "second listen: the harmony picture and your first note" + (", plus the first listen's pictures"
                                                                            if s.get("full") else "")
    return {LAYOUT3: "three pictures each", LAYOUT: "two pictures each"}.get(lay, "one picture each")


_PLACEHOLDER = re.compile(r"^(\.+|…|-+|todo|tbd|xxx|n/?a|none|\?+|<[^>]*>|\[[^\]]*\])$", re.I)
_FILLER = {"todo", "tbd", "xxx", "none", "na", "placeholder", "lorem", "ipsum", "blah", "asdf", "fill", "filler", "etc"}
# seal.log lines are "<date> <time>  <text>"; both patterns are anchored there, so nothing a listener types into a
# logged --why can pass for a seal
_SEAL = re.compile(r"^\S+ \S+  note (\S+) sealed  sha256 ([0-9a-f]{64})")
_LOOKSEAL = re.compile(r"^\S+ \S+  look (\d\d) saw sealed  sha256 ([0-9a-f]{64})")
AFTER_MIN_WORDS = 8


def real_words(c):
    """Distinct words (2+ letters) that aren't filler: 'TODO TODO' has none, 'nothing comes' has two."""
    return {w.lower() for w in re.findall(r"[^\W\d_][^\W\d_'’-]+", c)} - _FILLER


def note_problems(text, labels=LABELS):
    """What's missing from a window note: each required label on its own line with real content (two real words at
    least, not a placeholder). Optional labels (expect) and free text around them are fine."""
    found = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            found.setdefault(m.group(1).lower(), m.group(2).strip().strip("*_ "))
    bad = []
    for lab in labels:
        c = found.get(lab)
        if c is None:
            bad.append(f"no '{lab}:' line")
        elif _PLACEHOLDER.match(c) or len(real_words(c)) < 2:
            bad.append(f"'{lab}:' has no real content")
    return bad


def saw_problems(text):
    """What's missing from a close-up's note: a 'saw:' line with LOOK_SAW_WORDS real words at least."""
    for line in text.splitlines():
        m = _SAW.match(line)
        if m:
            c = m.group(1).strip().strip("*_ ")
            return [] if not _PLACEHOLDER.match(c) and len(real_words(c)) >= C["LOOK_SAW_WORDS"] \
                else ["'saw:' has no real content"]
    return ["no 'saw:' line"]


def seal_problems(d):
    """Re-verify every sealed note (window notes, after.md, close-up notes) against the sha256 logged at its seal."""
    p = os.path.join(d, "seal.log"); bad = []; n = 0
    if not os.path.exists(p):
        return 0, bad
    for line in open(p):
        for pat, sub in ((_SEAL, "notes"), (_LOOKSEAL, "looks")):
            m = pat.match(line)
            if not m:
                continue
            n += 1
            f = os.path.join(d, sub, f"{m.group(1)}.md")
            if not os.path.exists(f):
                bad.append(f"{sub}/{m.group(1)}.md is missing")
            elif hashlib.sha256(open(f, "rb").read()).hexdigest() != m.group(2):
                bad.append(f"{sub}/{m.group(1)}.md changed after it was sealed")
    return n, bad


def sealed_already(d, name, pat=_SEAL):
    p = os.path.join(d, "seal.log")
    return os.path.exists(p) and any(m and m.group(1) == name for m in map(pat.match, open(p)))


def log_line(d, text):
    with open(os.path.join(d, "seal.log"), "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")


def seal(d, name, before, check=note_problems, help_=LABEL_HELP):
    """Seal notes/NAME.md: check it, make it read-only, log its sha256. Exits if it isn't ready."""
    n = os.path.join(d, "notes", f"{name}.md")
    if sealed_already(d, name):
        return
    if not os.path.exists(n):
        sys.exit(f"SEALED: write {n} first ({help_}). {before} waits until then.")
    bad = check(open(n).read())
    if bad:
        sys.exit(f"SEALED: {n} isn't ready ({'; '.join(bad)}). {before} waits until then.")
    h = hashlib.sha256(open(n, "rb").read()).hexdigest()
    os.chmod(n, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    log_line(d, f"note {name} sealed  sha256 {h}  before {before}")


def seal_look(d, s, before):
    """Seal the note of the last close-up (looks/NN.md, a 'saw:' line), if there is one and it isn't sealed yet.
    Exits if it isn't ready: the next look, `next` and `after` all wait for it."""
    k = s.get("looks_used", 0)
    name = f"{k:02d}"
    if k == 0 or sealed_already(d, name, _LOOKSEAL):
        return
    n = os.path.join(d, "looks", f"{name}.md")
    if not os.path.exists(n):
        sys.exit(f"SEALED: write {n} first (a line 'saw: ...': what close-up {name} showed you). {before} waits "
                 f"until then.")
    bad = saw_problems(open(n).read())
    if bad:
        sys.exit(f"SEALED: {n} isn't ready ({'; '.join(bad)}). {before} waits until then.")
    h = hashlib.sha256(open(n, "rb").read()).hexdigest()
    os.chmod(n, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    log_line(d, f"look {name} saw sealed  sha256 {h}  before {before}")


def after_problems(text):
    return [] if len(real_words(text)) >= AFTER_MIN_WORDS else [f"under {AFTER_MIN_WORDS} real words"]


def refuse_ahead(d, s):
    """Exit (and log) if windows/ holds a window `next` never revealed, or looks/ a close-up `look` never made."""
    k, used = s["revealed"], s.get("looks_used", 0)
    wd, ld = os.path.join(d, "windows"), os.path.join(d, "looks")
    ahead = sorted(f for f in (os.listdir(wd) if os.path.isdir(wd) else []) if re.match(r"\d{3}[.-]", f) and int(f[:3]) >= k)
    if ahead:
        log_line(d, f"found unrevealed window file(s) in windows/: {', '.join(ahead)}")
        sys.exit(f"LOOKED AHEAD: windows/ holds {', '.join(ahead)}, which `next` never revealed. Remove them; "
                 f"this is logged in seal.log.")
    ahead = sorted(f for f in (os.listdir(ld) if os.path.isdir(ld) else []) if re.match(r"\d{2}[.-]", f) and int(f[:2]) > used)
    if ahead:
        log_line(d, f"found close-up file(s) in looks/ that `look` never made: {', '.join(ahead)}")
        sys.exit(f"LOOKED AHEAD: looks/ holds {', '.join(ahead)}, which `look` never made. Remove them; "
                 f"this is logged in seal.log.")


# ------------------------------------------------------------------ commands
def load_state(d):
    p = os.path.join(d, "state.json")
    if not os.path.exists(p):
        legacy = os.path.isdir(os.path.join(d, "windows"))
        sys.exit(f"{d}: no state.json, so not a sealed v3 run" +
                 (" (an unsealed legacy run: all its windows were written at once)" if legacy else "") +
                 ". Start one with: see.py prepare SOURCE --artist A --title T --run DIR")
    return jload(p)


def started(d):
    """Has a listen begun in d (anything revealed, any note written)?"""
    if os.path.exists(os.path.join(d, "state.json")) and jload(os.path.join(d, "state.json")).get("revealed", 0) > 0:
        return True
    for sub in ("windows", "notes", "looks"):
        q = os.path.join(d, sub)
        if os.path.isdir(q) and os.listdir(q):
            return True
    return os.path.exists(os.path.join(d, "seal.log"))


def prepare(a):
    RUN = os.path.abspath(a.run)
    W = os.path.abspath(a.work or os.path.join(HERE, "audio", os.path.basename(RUN.rstrip("/"))))
    if W == RUN or W.startswith(RUN + os.sep):
        sys.exit("--work must be outside the run folder (that is the point of the seal)")
    if started(RUN):
        sys.exit(f"REFUSED: {RUN} already has a listen in it (windows, notes or a seal.log). Prepare a new run folder.")
    if not a.window or a.window < 3:
        sys.exit("--window must be at least 3 s")
    if a.looks < 0:
        sys.exit("--looks can't be negative")
    C["WIN"] = float(a.window)
    C["SCALE"] = a.scale
    os.makedirs(W, exist_ok=True); os.makedirs(RUN, exist_ok=True)
    log = {}
    for st in plan_stages(W, a.redo, a.only):   # the plan depends only on which outputs exist before the first stage
        t = time.time()
        say(f"== {st}")
        fn = globals()["stage_" + st]
        fn(a, W, W) if st == "render" else fn(a, W)
        log[st] = round(time.time() - t, 1)
        say(f"   {st}: {log[st]} s")
    # run summary (no lyric text): stays in WORK until `after`, since it holds whole-song numbers
    summ = dict(constants=C, timings=log)
    prev = os.path.join(W, "summary.json")
    if os.path.exists(prev):
        summ["timings"] = {**jload(prev).get("timings", {}), **log}
    for f, key in (("align.json", "align"), ("voices.json", "voices"), ("words.json", "features")):
        pth = os.path.join(W, f)
        if os.path.exists(pth):
            summ[key] = jload(pth)["report"]
    if os.path.exists(os.path.join(W, "words.json")):
        summ["quality"] = quality(W)
    jdump(summ, prev)
    meta = jload(os.path.join(W, "meta.json"))
    nwin = n_windows(meta["duration"])
    st_ = dict(tool="listening v3 see.py", layout=LAYOUT3, window=C["WIN"], scale=C["SCALE"], labels=list(LABELS),
               optional=list(OPTIONAL) + ["harmony"], looks=int(a.looks), looks_used=0,
               work=W, nwin=nwin, artist=meta.get("artist"), title=meta.get("title"), duration=meta["duration"],
               revealed=0, prepared=time.strftime("%Y-%m-%d %H:%M:%S"))
    rend = os.path.join(W, "windows", "render.json")
    if not os.path.exists(rend) or jload(rend).get("window") != C["WIN"] or jload(rend).get("layout") != LAYOUT3 \
            or jload(rend).get("scale", "db") != C["SCALE"]:
        sys.exit(f"windows not rendered for {C['WIN']:g} s windows on the {C['SCALE']} scale yet; run prepare again "
                 f"without --only")
    missing = [f for k in range(nwin) for f, src in window_files(st_, k) if not os.path.exists(src)]
    if missing:
        sys.exit(f"windows not rendered yet ({len(missing)} files missing); run prepare again without --only")
    os.makedirs(os.path.join(RUN, "notes"), exist_ok=True)
    jdump(st_, os.path.join(RUN, "state.json"))
    say(f"prepared: {nwin} windows of {C['WIN']:g} s (three pictures each; {a.looks} close-ups to spend with "
        f"`look`), sealed in {W}/windows (don't open it).\nStart listening with: see.py next {a.run}")


def again(a):
    """A second listen of a finished one: a new run folder over the same cache, showing per window the harmony
    picture and the listener's own first note. Only the harmony stage and the harmony pictures are added to the
    cache; the first run is only read."""
    R1, R2 = os.path.abspath(a.first), os.path.abspath(a.run)
    s1 = load_state(R1)
    _, bad = seal_problems(R1)
    if bad:
        sys.exit(f"TAMPERED: in the first listen {R1}: " + "; ".join(bad) + ". (see its seal.log)")
    if not sealed_already(R1, "after"):
        sys.exit(f"not yet: {R1} isn't a finished listen (its after.md isn't sealed). Finish it first.")
    W = s1["work"]
    if R2 == R1 or R2.startswith(R1 + os.sep) or R1.startswith(R2 + os.sep):
        sys.exit("--run must be a new folder of its own, outside the first listen")
    if W == R2 or W.startswith(R2 + os.sep) or R2.startswith(W + os.sep):
        sys.exit("--run must be outside the cache (that is the point of the seal)")
    if started(R2):
        sys.exit(f"REFUSED: {R2} already has a listen in it (windows, notes or a seal.log). Choose a new run folder.")
    if a.looks < 0:
        sys.exit("--looks can't be negative")
    C["WIN"] = float(s1.get("window", 10.0))
    nwin = s1["nwin"]
    miss = [f for f in ("meta.json", "accomp.wav", os.path.join("band", "bass.wav")) if not os.path.exists(os.path.join(W, f))]
    if miss and not os.path.exists(os.path.join(W, "harmony.npz")):
        sys.exit(f"the cache {W} has no {', '.join(miss)}, which the harmony picture is measured from; `again` adds "
                 f"only the harmony stage (it never fetches or separates). Nothing was changed.")
    if not os.path.exists(os.path.join(W, "harmony.npz")):
        say("== harmony")
        stage_harmony(a, W)
    if not os.path.exists(os.path.join(W, "periodicity.npz")) and os.path.exists(os.path.join(W, "vocals.wav")):
        say("== periodicity")   # so this listen's close-ups have the periodicity panel (no window is drawn again)
        stage_periodicity(a, W)
    wd = os.path.join(W, "windows")
    gj = os.path.join(wd, "geometry.json")
    have = os.path.exists(gj) and jload(gj).get("window") == C["WIN"] and "harmony" in jload(gj).get("pictures", {}) \
        and all(os.path.exists(os.path.join(wd, f"{k:03d}-harmony.png")) for k in range(nwin))
    if not have:
        say("== render (harmony pictures only)")
        write_geometry(wd, nwin, render_harmony(W, W))
    st_ = dict(tool="listening v3 see.py", layout=SECOND, first=R1, first_layout=s1.get("layout"), full=bool(a.full),
               window=C["WIN"], scale=s1.get("scale", "db"), labels=list(SECOND_LABELS), optional=list(OPTIONAL), looks=int(a.looks), looks_used=0,
               work=W, nwin=nwin, artist=s1.get("artist"), title=s1.get("title"), duration=s1["duration"],
               revealed=0, prepared=time.strftime("%Y-%m-%d %H:%M:%S"))
    missing = [src for k in range(nwin) for f, src in window_files(st_, k) if not os.path.exists(src)]
    if missing:
        sys.exit(f"{len(missing)} file(s) a second listen reveals are missing (first: {missing[0]}). Nothing was "
                 f"prepared in {R2}.")
    os.makedirs(os.path.join(R2, "notes"), exist_ok=True)
    jdump(st_, os.path.join(R2, "state.json"))
    say(f"second listen prepared: {nwin} windows of {C['WIN']:g} s ({layout_words(st_)}; {a.looks} close-ups to "
        f"spend with `look`).\nStart with: see.py next {a.run}")


def nxt(d):
    d = os.path.abspath(d)
    s = load_state(d)
    labels, help_ = run_labels(s)
    check = lambda t: note_problems(t, labels)  # noqa: E731
    n_sealed, bad = seal_problems(d)
    if bad:
        sys.exit("TAMPERED: " + "; ".join(bad) + ". Sealed notes must not change; nothing more is revealed. (see seal.log)")
    k, nwin = s["revealed"], s["nwin"]
    wd = os.path.join(d, "windows")
    os.makedirs(wd, exist_ok=True); os.makedirs(os.path.join(d, "notes"), exist_ok=True)
    refuse_ahead(d, s)
    if k >= nwin:
        seal_look(d, s, "the after note")
        seal(d, f"{nwin - 1:03d}", "the after note", check, help_)
        sys.exit(f"The song is over and your last note is sealed. Write notes/after.md (the whole song: what stayed, "
                 f"what changed, what the voice and the band did over the whole), then: see.py after {d}")
    seal_look(d, s, f"revealing window {k:03d}")
    if k > 0:
        seal(d, f"{k - 1:03d}", f"revealing window {k:03d}", check, help_)
    files = window_files(s, k)
    lay = s.get("layout")
    rj = os.path.join(s["work"], "windows", "geometry.json" if lay == SECOND else "render.json")
    if lay in (LAYOUT, LAYOUT3, SECOND) and os.path.exists(rj) and jload(rj).get("window") != s.get("window"):
        sys.exit(f"the cache {s['work']}/windows was re-rendered with {jload(rj).get('window')} s windows, but this run "
                 f"uses {s.get('window')} s: run prepare again with --window {s.get('window'):g} (nothing revealed)")
    if lay in (LAYOUT, LAYOUT3) and os.path.exists(rj) and jload(rj).get("scale", "db") != s.get("scale", "db"):
        sys.exit(f"the cache {s['work']}/windows was re-rendered on the {jload(rj).get('scale', 'db')} scale, but this "
                 f"run uses {s.get('scale', 'db')}: run prepare again with --scale {s.get('scale', 'db')} (nothing "
                 f"revealed)")
    if lay == SECOND:
        _, bad1 = seal_problems(s["first"])
        if bad1:
            sys.exit(f"TAMPERED: in the first listen {s['first']}: " + "; ".join(bad1) + ". Nothing revealed.")
    for f, src in files:
        if not os.path.exists(src):
            sys.exit(f"{src} is missing: run prepare again")
    for f, src in files:
        shutil.copyfile(src, os.path.join(wd, f))
        if f.endswith("-first.md"):
            os.chmod(os.path.join(wd, f), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    s["revealed"] = k + 1
    jdump(s, os.path.join(d, "state.json"))
    log_line(d, f"window {k:03d} revealed")
    win = s.get("window", 10.0)
    t0, t1 = k * win, min(s["duration"], (k + 1) * win)
    pics = [f for f, _ in files if f.endswith(".png")]
    print(f"window {k:03d} of {nwin - 1:03d} ({mmss_short(t0)}-{mmss_short(t1)}): read "
          + " and ".join(f"{wd}/{f}" for f in pics) + f" (the words also in text: {k:03d}.txt"
          + (f"; your note from the first listen: {k:03d}-first.md" if lay == SECOND else "") + ")")
    if k == nwin - 1:
        print(f"This is the last window. Write notes/{k:03d}.md ({help_}), then see.py next {d} seals it.")
    else:
        print(f"Then write {d}/notes/{k:03d}.md with the lines {help_}, two real words each at least; "
              f"see.py next {d} seals it and reveals window {k + 1:03d}.")


def parse_time(x):
    """'m:ss', 'm:ss.s' or seconds -> seconds."""
    m = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", x.strip())
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    try:
        return float(x)
    except ValueError:
        sys.exit(f"can't read the time '{x}': write m:ss, m:ss.s or seconds")


def look(a):
    """A close-up the listener chooses: LOOK_MIN..LOOK_MAX seconds inside what is already revealed, a reason given
    first (logged before anything is rendered), a per-run budget, and a sealed 'saw:' note after each."""
    d = os.path.abspath(a.run)
    s = load_state(d)
    _, bad = seal_problems(d)
    if bad:
        sys.exit("TAMPERED: " + "; ".join(bad) + ". Sealed notes must not change; no close-up is made. (see seal.log)")
    refuse_ahead(d, s)
    if sealed_already(d, "after"):
        sys.exit("REFUSED: this listen is complete and sealed; a close-up would add to it. A second listen "
                 "(see.py again RUN --run RUN2) has close-ups of its own.")
    ts, te = parse_time(a.start), parse_time(a.end)
    win = s.get("window", 10.0)
    limit = min(s["duration"], s["revealed"] * win)
    L = te - ts
    if not (C["LOOK_MIN"] - 1e-6 <= L <= C["LOOK_MAX"] + 1e-6):
        sys.exit(f"REFUSED: a close-up spans {C['LOOK_MIN']:g} to {C['LOOK_MAX']:g} seconds; {a.start}-{a.end} is {L:g} s.")
    if ts < 0 or te > limit + 1e-6:
        sys.exit(f"REFUSED: a close-up lies inside what is already revealed (0:00-{mmss(limit)} so far), never "
                 f"ahead; {a.start}-{a.end} is not.")
    why = " ".join((a.why or "").split())
    if _PLACEHOLDER.match(why) or len(real_words(why)) < C["LOOK_WHY_WORDS"]:
        sys.exit(f"REFUSED: --why needs {C['LOOK_WHY_WORDS']} real words at least: say what you are looking for "
                 f"before you look.")
    used, budget = s.get("looks_used", 0), s.get("looks", C["LOOKS"])
    if used >= budget:
        sys.exit(f"REFUSED: all {budget} close-ups of this run are spent.")
    n = used + 1
    seal_look(d, s, f"close-up {n:02d}")
    kinds = look_support(s["work"])
    if not kinds:
        sys.exit(f"REFUSED: the cache {s['work']} can't give a close-up (no stems or no whole-song scales in it). "
                 f"Nothing was spent.")
    C["WIN"] = float(win)
    C["SCALE"] = s.get("scale", "db")   # the run's own scale (runs prepared before the switch have none: db)
    ld = os.path.join(d, "looks")
    os.makedirs(ld, exist_ok=True)
    log_line(d, f"look {n:02d}  {mmss(ts)}-{mmss(te)}  why: {why}")   # the reason is on record before any picture exists
    t = time.time()
    tmp = os.path.join(ld, ".rendering")   # drawn here first: a render that dies leaves no half-made close-up behind
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        files, _ = look_render(s["work"], s, n, ts, te, limit, tmp, kinds)
    except BaseException as ex:
        shutil.rmtree(tmp, ignore_errors=True)
        log_line(d, f"look {n:02d} could not be rendered ({type(ex).__name__}); not counted")
        sys.exit(f"close-up {n:02d} could not be rendered ({type(ex).__name__}: {ex}); it is not counted.")
    s["looks_used"] = n
    s.setdefault("looks", budget)
    jdump(s, os.path.join(d, "state.json"))   # counted before its files appear, so they are never "ahead"
    for f in sorted(os.listdir(tmp)):
        os.replace(os.path.join(tmp, f), os.path.join(ld, f))
    shutil.rmtree(tmp, ignore_errors=True)
    log_line(d, f"look {n:02d} rendered: {', '.join(files)}")
    pics = [f for f in files if f.endswith(".png")]
    print(f"close-up {n:02d} ({mmss(ts)}-{mmss(te)}, rendered in {time.time() - t:.0f} s): read "
          + " and ".join(f"{ld}/{f}" for f in pics) + f" (its words in text: {n:02d}.txt)")
    print(f"Then write {ld}/{n:02d}.md with a line 'saw: ...' (what it showed you, two real words at least); the next "
          f"look, next or after seals it. Close-ups left: {budget - n} of {budget}.")


def after(d):
    d = os.path.abspath(d)
    s = load_state(d)
    labels, help_ = run_labels(s)
    _, bad = seal_problems(d)
    if bad:
        sys.exit("TAMPERED: " + "; ".join(bad) + ". (see seal.log)")
    if s["revealed"] < s["nwin"]:
        sys.exit(f"not yet: {s['nwin'] - s['revealed']} window(s) still sealed. Keep going with see.py next {d}")
    seal_look(d, s, "the after note")
    seal(d, f"{s['nwin'] - 1:03d}", "the after note", lambda t: note_problems(t, labels), help_)
    if sealed_already(d, "after"):
        print("after.md is already sealed; the listen is complete.")
    else:
        seal(d, "after", "the end of the listen", check=after_problems, help_="the whole-song note")
        log_line(d, "listen complete")
        print(f"after.md sealed; the listen is complete.")
    src = os.path.join(s["work"], "summary.json")
    if os.path.exists(src):
        shutil.copyfile(src, os.path.join(d, "summary.json"))
        print(f"whole-song technical numbers (no lyrics): {d}/summary.json")


def status(d):
    d = os.path.abspath(d)
    s = load_state(d)
    labels, help_ = run_labels(s)
    k, nwin = s["revealed"], s["nwin"]
    print(f"{s.get('artist')} - {s.get('title')}: {k} of {nwin} windows revealed "
          f"({s.get('window', 10.0):g} s windows, {layout_words(s)}"
          + (", cochleagrams on the ear scale" if s.get("scale") == "ear" else "") + f"; note labels {help_})")
    n, bad = seal_problems(d)
    print(f"seal check: {n} sealed notes, " + ("all match their logged sha256" if not bad else "MISMATCH: " + "; ".join(bad)))
    used = s.get("looks_used", 0)
    if "looks" in s or used:
        budget = s.get("looks", C["LOOKS"])
        pend = used and not sealed_already(d, f"{used:02d}", _LOOKSEAL)
        print(f"close-ups: {used} used, {max(0, budget - used)} left of {budget}"
              + (f"; looks/{used:02d}.md (saw: ...) still to be written or sealed" if pend else ""))
    if k == 0:
        print(f"next: see.py next {d}  (reveals window 000)")
    elif k < nwin:
        nb = note_problems(open(os.path.join(d, "notes", f"{k - 1:03d}.md")).read(), labels) \
            if os.path.exists(os.path.join(d, "notes", f"{k - 1:03d}.md")) else ["not written"]
        print(f"next: notes/{k - 1:03d}.md " + ("is ready" if not nb else f"({'; '.join(nb)})") + f", then see.py next {d}")
    elif not sealed_already(d, "after"):
        print(f"all windows revealed: write notes/{nwin - 1:03d}.md if it isn't, then notes/after.md, then see.py after {d}")
    else:
        print("the listen is complete")
    lp = os.path.join(d, "seal.log")
    if os.path.exists(lp):
        print(open(lp).read().rstrip())


def main():
    cmds = ("prepare", "next", "status", "after", "look", "again", "refcard")
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd in ("next", "status", "after"):
        if len(sys.argv) != 3:
            sys.exit(f"usage: see.py {cmd} DIR")
        return dict(next=nxt, status=status, after=after)[cmd](sys.argv[2])
    if cmd == "refcard":
        if len(sys.argv) != 3:
            sys.exit("usage: see.py refcard OUT.png")
        return refcard(sys.argv[2])
    if cmd == "look":
        p = argparse.ArgumentParser(prog="see.py look", description="a close-up of a span you choose, inside what is "
                                    "already revealed; say why first")
        p.add_argument("run")
        p.add_argument("start", help="m:ss, m:ss.s or seconds")
        p.add_argument("end", help=f"m:ss, m:ss.s or seconds; {C['LOOK_MIN']:g} to {C['LOOK_MAX']:g} s after start")
        p.add_argument("--why", required=True, help="what you are looking for (logged before anything is rendered)")
        return look(p.parse_args(sys.argv[2:]))
    if cmd == "again":
        p = argparse.ArgumentParser(prog="see.py again", description="a second listen of a finished one: the harmony "
                                    "picture and your own first notes, window by window")
        p.add_argument("first", help="the finished first listen (its after.md sealed)")
        p.add_argument("--run", required=True, help="the new run folder of the second listen")
        p.add_argument("--looks", type=int, default=C["LOOKS"], help=f"close-ups to spend (default {C['LOOKS']})")
        p.add_argument("--full", action="store_true", help="also reveal the first listen's pictures per window")
        return again(p.parse_args(sys.argv[2:]))
    p = argparse.ArgumentParser(prog="see.py prepare", description="all heavy stages; windows rendered into WORK, sealed")
    p.add_argument("source", help="local audio file, URL, or ytsearch1:...")
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--run", required=True, help="the listening folder: gets state.json, notes/, and windows as revealed")
    p.add_argument("--work", default=None, help="cache dir for audio, stems, lyrics, json and the sealed windows "
                                                "(default v3/audio/<run folder name>; gitignored)")
    p.add_argument("--redo", choices=STAGES, help="rerun this stage and every stage that depends on it")
    p.add_argument("--only", choices=STAGES, help="run just this stage (it must have its inputs)")
    p.add_argument("--lyrics-offset", type=float, default=None, help="force a global LRC offset, s")
    p.add_argument("--no-split", action="store_true", help="skip the lead/backing split (lead = the whole vocal stem)")
    p.add_argument("--window", type=float, default=C["WIN"], metavar="SECONDS",
                   help=f"seconds per window (default {C['WIN']:g}); stored in state.json")
    p.add_argument("--looks", type=int, default=C["LOOKS"], help=f"close-ups the listener may spend (default {C['LOOKS']})")
    p.add_argument("--scale", choices=("db", "ear"), default=C["SCALE"],
                   help=f"how the cochleagrams map intensity to colour (default {C['SCALE']}): db = envelope power in "
                        f"dB; ear = loudness as felt (power^{C['EAR_POWER']:g}) with onsets emphasised; stored in "
                        f"state.json")
    prepare(p.parse_args(sys.argv[2:]))


if __name__ == "__main__":
    main()
