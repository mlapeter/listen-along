# listening/v3: the voice, the words, the band and the notes, three pictures per 15 s

`see.py` turns a song into three pictures and one text file per 15 s window (`NNN-voice.png`, 1568 x 1387 px: the voice
and the words; `NNN-band.png`, 1568 x 1050 px: the five instrument stems, the stereo picture, the beat and the song so
far; `NNN-harmony.png`, 1568 x 750 px: the band's energy on semitone rows, so notes can be read; `NNN.txt`), and lets
a listener (a Claude session) read them **in order, one at a time, writing a sealed note after each**. The listener
can also spend a few **close-ups** (`look`) on spans they choose, and **listen a second time** (`again`) with the
harmony picture and their own first notes. Everything in the
pictures is measured from the audio: no other model describes the song.
Whisper and a forced aligner are used only to find *when* each lyric word is sung; the words themselves are the
LRCLIB lyric. Nothing classifies or labels the music: the stems are named by the separation model's outputs, and every
panel is a measured curve, a cochleagram or a constant-Q picture. The harmony picture has note names on its axis and
nothing else: no chord, key or section is estimated or printed anywhere.

Layout: this folder (`v3/`) holds the tool; its parent (`listening/` in the repository) holds `words.py` (the LRCLIB
lookup), the two environments `setup.sh` builds (`venv3/` for the tool, `venv/` for yt-dlp only), `runs/` and
`constraints.txt`. Commands below are run from that parent folder. Installing is `./setup.sh` at the repository root;
the README there is the short introduction, this is the full manual.

## The sealed listen (the normal way to use it)

    cd listening        # inside your clone of the repository
    # 1. heavy work, once (8-18 min); can be run by someone else so the listener sees nothing
    ./venv3/bin/python v3/see.py prepare "ytsearch1:Artist Title official audio" \
        --artist "Artist" --title "Title" --run runs/<date>-<slug>-v3
    # SOURCE can also be a local audio file or a URL. --work W puts the cache elsewhere
    # (default v3/audio/<run folder name>, gitignored). --window SECONDS (default 15) sets the window length;
    # it is stored in state.json, so `next` stays consistent. --looks N sets the close-up budget (default 6).
    # --scale db|ear (default db) picks how the cochleagrams map intensity to colour (see "Reading the pictures");
    # it is stored in state.json too, and the run's close-ups use it.

    # 1b. once, before your first window: the reference card (synthetic notes and synthetic voices, no song)
    ./venv3/bin/python v3/see.py refcard /tmp/refcard.png     # then read it (it also writes /tmp/refcard-timbre.png:
    #                                                           read that one before your first `look --timbre`)

    # 2. the listen, window by window
    ./venv3/bin/python v3/see.py next runs/<run>      # reveals windows/000-voice.png, 000-band.png,
    #                                                   000-harmony.png, 000.txt
    #    read the three PNGs, then write runs/<run>/notes/000.md:
    #        feel:    what this 15 s adds up to for you (say when you are unsure)
    #        voice:   what the voice does here (pitch line, grain, held notes, tags, a new voice, backing voices)
    #        band:    what the instruments, the stereo picture and the beat do; where the song is so far
    #        words:   what the lines mean to you so far
    #        harmony: (optional) what the notes do: the bass's steps, rows that change together, the voice against them
    #        expect:  (optional) what you expect next
    # 2b. any time, a close-up of 2-8 s inside what is revealed (6 per run unless --looks said otherwise)
    ./venv3/bin/python v3/see.py look runs/<run> 0:41 0:45 --why "what the bass does under the held word"
    #    read looks/01-voice.png, 01-band.png, 01-harmony.png; then write runs/<run>/looks/01.md:
    #        saw:     what the close-up showed you
    #    add --timbre for one more picture of the same look, looks/01-timbre.png: the lead voice alone through
    #    filters a third of the ear's width, so its harmonics show one by one (which are strong: the vowel, the
    #    colour of the voice). Same budget, ~1.3k tokens more.
    ./venv3/bin/python v3/see.py next runs/<run>      # seals notes/000.md, reveals 001
    ...                                               # until the last window
    ./venv3/bin/python v3/see.py next runs/<run>      # after the last window: seals its note, asks for after.md
    #    write runs/<run>/notes/after.md: the whole song, free form (8+ real words)
    ./venv3/bin/python v3/see.py after runs/<run>     # seals after.md, copies summary.json into the run
    ./venv3/bin/python v3/see.py status runs/<run>    # any time: where you are, close-ups left, seal check, seal.log

    # 3. later, if you want: a second listen of the finished one (seconds to prepare; nothing is downloaded)
    ./venv3/bin/python v3/see.py again runs/<run> --run runs/<date>-<slug>-second [--looks N] [--full]
    ./venv3/bin/python v3/see.py next runs/<date>-<slug>-second    # 000-harmony.png, 000.txt, 000-first.md
    #    notes/000.md there:  feel: / harmony: / changed: (what is different this time)   expect: optional

What the seal enforces:

- `prepare` renders every window into `WORK/windows/`, outside the run folder. **The listener must not open
  `WORK/`** (nor `WORK/summary.json`, which holds whole-song numbers). The run folder gets only `state.json`
  and an empty `notes/`. `prepare` refuses a run folder that already has a listen in it, and a `--work` inside it.
- `next` reveals exactly one window, by copying `NNN-voice.png`, `NNN-band.png`, `NNN-harmony.png` and `NNN.txt` into
  `RUN/windows/`. It won't reveal window k until `notes/{k-1}.md` exists with the four required labels (`feel`,
  `voice`, `band`, `words`), each with at least two real words (not `TODO`, `...`, `tbd`). `harmony:` and `expect:`
  are accepted and not required. Labels can be decorated (`**feel:**`, `- voice:`); extra lines are fine.
- `state.json` says what a run is (`layout`): `voice+band+harmony` (three pictures), `voice+band` (two, prepared
  before the harmony picture: `next` reveals its two pictures as before), `second` (a second listen, below), or no
  marker at all (prepared before the band view: one `NNN.png` per 10 s window, with `feel`, `voice`, `words`,
  `expect` all required). `next`/`status`/`after` treat each as they always did.
- **`look RUN START END --why "..."`** (times as `m:ss`, `m:ss.s` or seconds) makes a close-up into `RUN/looks/`:
  `LL-voice.png`, `LL-band.png`, `LL-harmony.png` (those the cache has the stems and scales for), `LL.txt` (the words
  in the span) and `LL.json` (frame steps, panel boxes). In code: the span is 2 to 8 s and lies entirely inside what
  this run has revealed, never ahead; `--why` needs three real words and is written to `seal.log` **before**
  anything is rendered (you say what you are looking for first); the run's budget (`looks` in `state.json`, default
  6, set by `prepare --looks N` / `again --looks N`) is checked and `status` shows used/left; after a look you must
  write `looks/LL.md` with a `saw:` line (two real words), which the next `look`, `next` or `after` seals
  (read-only, sha256 in `seal.log`) and each of them refuses until it exists. A finished listen takes no more
  looks. A look that fails to render is logged and not counted. **`--timbre`** adds `LL-timbre.png` to the same
  look (one look of the budget, like any other; see "Close-ups" below). Nothing else about the look changes: the
  other pictures are byte for byte what they are without it, `LL.txt` and the `look LL rendered:` line of `seal.log`
  list the extra file, `LL.json` gets a `timbre` entry, and the checks above treat it like the other pictures (an
  `LL-timbre.png` numbered beyond the looks made is `LOOKED AHEAD`). It needs the lead stem and the song's
  cochleagram scale; on a cache without them `look --timbre` refuses before the why is logged, nothing spent.
- **`again RUN1 --run RUN2`** needs RUN1 finished (its `after.md` sealed) and never changes it. RUN2 is a run
  folder of its own over the same cache (`layout: second`). On the cache it runs only what is missing: the
  `harmony` stage and the harmony pictures (it never fetches, separates or re-renders an existing picture, and
  refuses if the cache has no `accomp.wav` / `band/bass.wav`). In RUN2 `next` reveals per window `NNN-harmony.png`,
  `NNN.txt` and `NNN-first.md`, a read-only copy of your own note for that window from RUN1 (`--full`: the first
  listen's pictures too). Its notes need `feel:`, `harmony:`, `changed:` (`expect:` optional). `look`, `status`,
  `after` and every check below work as in a first listen.
- If the cache is re-rendered with another `--window` or another `--scale` than the run's `state.json`, `next`
  refuses (nothing revealed). A run with no `scale` in its `state.json` (every run prepared before the option existed) is a `db` run, and
  `again` gives the second listen the first one's scale.
- When it reveals the next window it seals the previous note: makes it read-only and logs its sha256 in
  `RUN/seal.log` (with the time, and "window NNN revealed" lines). Every `next`/`look`/`after` re-checks every
  logged hash (window notes, `after.md`, look notes) and refuses with `TAMPERED` if a sealed note changed or vanished.
- A window file in `RUN/windows/` that `next` never revealed (copied from the cache), or a file in `RUN/looks/`
  numbered beyond the looks made, makes `next` and `look` refuse with `LOOKED AHEAD` and logs it.
- `after` works only once every window is revealed; it seals the last window note (if `next` hasn't) and
  `notes/after.md`, then copies `summary.json` in.
- Refusals (`SEALED`, `TAMPERED`, `LOOKED AHEAD`, "not yet", and the end-of-song message from `next`) exit with
  status 1 and say what to do; that is not a crash.
- It is an honour system with a lock on the notes: nothing stops a reader from opening `WORK/windows/`. The
  point is that the notes are provably written before the next window was revealed.

**Look-ahead that remains (by design, documented):** each picture is causal in what it *shows* (only its own
window; "voices so far" counts only voices already heard; the song-so-far strip stops at the window's end), but some
*scales* are computed over the whole song:
- the tags' baselines: each word is compared with the median/MAD of that voice's exact words over the **whole
  song** (including later windows), so "louder" means louder than this voice usually is in the song;
- the voice labels come from clustering the whole song (but letters are given in order of first appearance);
- the colour scales of the cochleagrams, the stereo picture, the song-so-far strip and the bass strip are fixed per song
  from whole-song percentiles (the cochleagram scale's top comes from the loudest source's 99.5th percentile; on the
  `ear` scale there is a second such top, the loudest source's 99.5th percentile of the ear response, kept in
  `WORK/ear.json`);
- the periodicity panel's colour top is the 99.5th percentile of that picture over the whole song (in
  `periodicity.npz`), and it is drawn on the harmony picture's whole-song tuning; its frames are 46 ms long and
  centred, so the right edge of a window holds up to 23 ms of sound from just past it (a close-up never reads past
  what is revealed);
- in the harmony picture: the **tuning** (one offset in cents for the whole song, in every header), the colour
  scales of the piano roll, the bass panel and the pitch-class strip (each its own whole-song 99.5th percentile),
  and the bass pitch line's gate (35 dB under the 95th percentile of the bass stem's level over the song);
- the constant-Q filters are centred and long in the low rows, so the right edge of a harmony window holds some
  sound from just past it: up to half a filter, 0.40 s at C2, 0.20 s at C3, 0.10 s at C4, 0.05 s at C5 (bass panel:
  0.40 s at C1, 0.20 s at C2). A close-up never reads audio past what the run has revealed, so it has none of this
  at the revealed edge;
- a few tens of ms at the window's right edge: the cochleagram channels are shifted earlier by their group delay
  (under 25 ms) and the stereo STFT frames are centred (+-46 ms). The song-so-far strip has neither: causal filters,
  non-overlapping 0.5 s blocks, and only blocks that end by the window's end are drawn. A close-up reads nothing
  past what is revealed, so a look that ends exactly there is black for its channels' group delay at the right
  edge: under 25 ms in the voice and band pictures, and in `LL-timbre.png`, whose filters are three times as long,
  58 ms in the lowest row, 40 ms at 200 Hz, 14 ms at 1 kHz;
- the x axis of the song-so-far strip is the whole song's length (which the header's "window k of N" gives anyway).

Tests (no real audio needed): `./venv3/bin/python v3/tests/test_seal.py` (the seal, `look`, `again`, the scale a
run and its cache must share: 77 checks), `./venv3/bin/python v3/tests/test_harmony.py` (which rows and columns of
the harmony picture carry known synthetic notes and a click, in the data and in the rendered pixels: 41 checks) and
`./venv3/bin/python v3/tests/test_periodicity.py` (the periodicity panel's rows and columns for known synthetic
voices, the `ear` scale's numbers, every panel box of `geometry.json` against the rendered pixels of all three
pictures, on a synthetic cache run through the real stages: 51 checks) and
`./venv3/bin/python v3/tests/test_timbre.py` (the timbre picture: a known tone's harmonics in the right channels and
on the right pixel rows, two envelopes on one pitch, what the narrow filters cost, the shared time axis, the seal
with the extra file, a look without `--timbre` unchanged, the timbre card: 47 checks).

**Lyrics and recordings are copyrighted.** Run folders and `v3/audio/` contain the recording and the full lyric; the
repository's `.gitignore` keeps both out of git. Don't commit or publish them.

## Reading the pictures

All three pictures share the same 15 s time axis at the same pixel positions (same left and right margins: the axis
runs from x = 117.6 to 1544.5 px of 1568), so a moment lines up across them. The last window keeps the full scale; past the song's end is grey. Every cochleagram is a
gammatone filterbank (ERB-spaced centre frequencies, 50 Hz-8 kHz; each channel a 4th-order complex gammatone of
1.019 ERB bandwidth, its envelope power in dB, aligned by group delay) shown on **one colour scale for the whole song
and every panel**: the top is the loudest source's 99.5th percentile, the bottom 60 dB below it. So louder in the
picture is louder in the song, quiet stays faint and silence is black. The y axis is the channel (ERB spacing: roughly
linear below ~500 Hz, logarithmic above), labelled in Hz. Compared with an STFT spectrogram it keeps fine timing in the
upper channels (5 ms frames on the lead), where rasp, creak and breath show as texture and fast flicker.

**The `ear` scale** (`prepare --scale ear`; the default stays `db`, and each picture's caption says which it is)
draws the same cochleagrams (lead, backing, the five stems; windows and close-ups) as the ear reports them rather than
in dB. Two plain steps per channel, on the frame powers: (1) loudness: power relative to the song's dB top, to the
power 0.3 (so 10 dB down is half as bright, 20 dB a quarter, 30 dB an eighth; a dB scale gives each of those the
same step); (2) adaptation: from that, half of the channel's own average over the previous 50 ms is taken away (a
causal one-pole average of the frames before this one), never below zero. So an onset, which meets no average yet,
shows at its full loudness; a held sound settles to half of it within about 0.1 s; and just after a sound stops its
tail is cut short. The response never exceeds the plain loudness, so nothing is drawn where there is no energy. Colour
is linear from 0 to the loudest source's 99.5th percentile of that response over the song. What it costs: everything
more than about 30 dB under the song's loudest sounds (upper harmonics, breath, a quiet backing strip) is close to
black, where the dB scale still shows it at half brightness; and where a yellow band starts (the panel switches
from the lead stem to the whole vocal) the switch itself reads as an onset. The song-so-far strip, the stereo
picture and the harmony picture have no `ear` version.

### `NNN-voice.png` (1568 x 1387): the voice and the words

1. **Header**: window number, span, artist/title, voices heard so far, voice label(s) in this window, `[the voice]`.
2. **Lead vocal** (big panel): cochleagram of the lead voice only (the backing voices removed), 96 channels, 5 ms
   frames. **Cyan line = the lead's pitch** (pYIN on the lead stem, drawn at its frequency on the same axis); gaps where
   nothing is pitched (consonants, breaths, rests, speech-like rap often). Faint white vertical lines = word onsets
   (dotted for estimated words). A green dashed line with a label = a voice change or a new voice starts here. A
   **yellow band along the top** marks spans where the panel shows the *whole* vocal stem instead, because that line's
   words fit the whole vocal clearly better than the lead (the karaoke model sometimes moves the main singer, or one of
   a doubled pair, into "backing"); the pitch line there is still the lead stem's.
3. **Backing strip**: cochleagram (64 channels, 10 ms, up to 4 kHz) of the vocal stem minus the lead: doubles,
   harmonies, stacked hooks, call-and-response, shout-outs, ad-libs, and (in quiet songs) instrument sound that leaked
   into the vocal stem. Same colour scale as the lead, so quiet backing stays faint and empty means none. The faint
   light-cyan line is the backing pitch (only where backing is audible). The label gives the share of the window where
   backing is audible.
4. **Periodicity** (between the backing strip and the words; read the reference card first): the *timing* in the
   **whole vocal stem** (`vocals.wav`, lead and backing together), drawn on the harmony picture's semitone rows (C2 to
   C6, 3 image rows per semitone, same labels, moved by the song's tuning). It is a summary autocorrelogram: 24
   gammatone channels (80 Hz-4 kHz), each half-wave rectified and compressed (power 0.3), and per 46 ms frame the
   autocorrelation of every channel summed, read off at the period of each row. Brightness = how strongly the sound
   repeats itself after that period (linear, one scale for the song; silence is black, and a sound with no period, a
   click or an "s", leaves almost nothing). Every periodicity present shows at once, so two people singing together
   are two ridges at their two notes, where the cyan pitch line can only follow one. Nothing is picked, counted or
   named. How to read it:
   - **The ladder below.** A sound that repeats every T also repeats every 2T, 3T, 4T, so each voice lights its
     own row *and* fainter rows 12 semitones below it (the octave below), 19, 24, 28, 31: the mirror image of the
     overtone ladder in the harmony picture. The **top** row of a ladder is the sung note; the rows below it are not
     other singers. The ladder fades downward because the frame is short: a ridge at A3 is drawn at 0.94 of its
     strength, at A2 0.77, at E2 0.63, at C2 0.47 (the same holds for a voice that really sings that low).
   - **Shared rows.** Two voices are two ladders, and a row both share can be as bright as either note: a fifth apart
     (A3 + E4), the A2 row under them is the brightest, though nobody sings it. Look for the highest ridges.
   - **Width.** The timing is equally fine in milliseconds everywhere, which is fewer semitones up high: ridges are
     about 1.5 semitones wide at A2, 2 at A3, 2.5 at A4, 3.5 at A5 (the peak sits on the note). Two voices a
     semitone apart merge into one ridge that pulses as they beat; a whole tone apart they are two peaks pulled a
     third of a semitone toward each other; a minor third or more apart they stand on their own rows (above A4 still
     pulled together a little). Two voices an octave apart share one ladder: it looks like the upper voice alone,
     except that the row an octave under the top is the brighter one, which is a weak sign.
   - It is the whole vocal stem, so instrument sound that leaked into it has ridges too, and a doubled voice is one
     ridge. Faint white verticals are word onsets. No pitch line is drawn over it.
5. **Word lane**: each word at its onset (in time order), up to 5 staggered rows, bar = its duration. Black = timed
   exactly, **red bold = has a tag**, **blue = found in the backing voices** (written `{word}` in the text), grey italic
   `~word` = time estimated (an estimated word with no free row is left out of the lane, but stays in the text panel).
   The time axis is under this lane.
6. **Held-note close-ups** (up to 2): the measured lead words in this window with the longest continuous pitch run
   (at least 0.7 s; pYIN dropouts of up to 2 frames bridged), longest first. Each shows 1.5-2.5 s around the note (never
   outside the window), the held run shaded:
   - **cents**: pitch around the note's median, from pYIN re-run on that excerpt at a 2.9 ms step (the thin line is the
     10 ms track); dashed lines = +-1 semitone. Vibrato is the wobble, scoops and falls are the ends.
   - **level dB** (below the excerpt's peak), **HNR dB** (Praat harmonics-to-noise; lower = noisier), **flatness dB**
     (spectral flatness; higher = more noise-like): raw curves over the same time.
   - **waveform**: 5 periods of the waveform at the middle of the note (lead stem, or the whole vocal under a yellow
     band), scaled to fit: a clean voice is a repeating shape, grain and rasp show as cycle-to-cycle differences.
   The title gives the word, its time, how long the pitch was held and the median pitch (Hz and note name).
   If no word qualifies, the area says so.
7. **Lyric lines**: each line with its start time and voice label; tags in `[square brackets]` after their word;
   `(round brackets)` are the lyric's own ad-lib/backing marks; `>> NEW VOICE B >>` / `>> VOICE CHANGE TO A >>`
   at the line where it happens.

### `NNN-band.png` (1568 x 1050): the band

1. **Header**, as above, `[the band]`.
2. **Five stem panels**: `drums`, `bass`, `guitar`, `piano`, `other` (Demucs `htdemucs_6s` on the whole song; its own
   vocal stem is not used, the voice is in the other picture). Each a 64-channel, 10 ms cochleagram on the shared scale.
   The names are the model's outputs, not a judgement: "piano" also catches other keyboards, "other" holds whatever
   the model didn't put elsewhere (strings, organ, synths, horns, often some voice and bleed).
3. **Stereo**: x = time, y = position from L (top) through centre to R (bottom), brightness = energy of the whole mix at
   that position (each STFT bin's (|R|-|L|)/(|R|+|L|), weighted by its energy, 50 ms frames, 41 rows, one scale for the
   song). A mono file gets a panel that says so.
4. **Bass and attacks**: orange above the line = accompaniment (`accomp.wav`, the mix without the vocal) energy under
   150 Hz (fixed scale for the song); black bars below = onset strength ("attacks": drum hits, plucks, chord changes).
5. **Song so far** (fixed width = the whole song, filled only from 0:00 to the end of this window; grey = not heard
   yet; cyan box = this window): a 40-channel cochleagram of the mix in 0.5 s blocks, and one lane each for the level
   of the vocal stem (`voice`) and the five stems (one shared scale). Over the windows the song's shape builds up.

### `NNN-harmony.png` (1568 x 750): the notes

Read `see.py refcard OUT.png` once first: the same code on known synthetic notes (one plucked A2, a C major and an A
minor triad, the C major 30 cents sharp, a bass A1, a click, a sung A4), each captioned. Under it, the same card
teaches the voice picture's periodicity panel on synthetic voices: one voice on A3, two a fifth apart, two a semitone
apart, one with vibrato, two an octave apart, a click and a hiss. (`refcard` also writes a second card,
`OUT-timbre.png`, for the timbre picture of a close-up; see "Close-ups".)

1. **Header**, as above, then `tuning +N cents` and `[the notes]`. The tuning is measured once for the song
   (`librosa.estimate_tuning` on the accompaniment: where its spectral peaks sit against A4 = 440 Hz, -50..+50 cents)
   and the whole semitone grid is moved by it, so a note played in tune *with the band* sits on the middle of its row.
2. **Piano roll of the band** (big panel): constant-Q of `accomp.wav` (the mix minus the vocal stem, so drums and
   bass are in it), C2 to B6, **one row per semitone drawn as 3 image rows** (36 bins per octave: the middle one is
   the note, the outer two a third of a semitone flat and sharp, so a bend, vibrato or an off-pitch note shows
   between rows). dB on one scale for the song (top = 99.5th percentile, 50 dB range). Sharps/flats rows are faintly
   shaded like the black keys of a piano roll; a white line runs under every C; on both edges every C is labelled
   with its octave (`C4` = middle C) and the other naturals with their letter (F and B, which sit a semitone from E
   and C, in a second column). Three pitch lines are drawn on the same rows: **cyan = the lead voice** (as in the
   voice picture), pale cyan = the backing pitch where backing is audible, **green = the bass stem's pitch** (pYIN on
   `band/bass.wav`, 30-400 Hz; off this panel when it is below C2). The voices themselves are not in this picture:
   only their lines. Faint white verticals are word onsets.
3. **Bass alone**: constant-Q of the bass stem, C1 to C4, same rows and labels, its own dB scale, the green pitch
   line on top. This is where the lowest note under a chord is read.
4. **Pitch classes**: the piano roll's energy folded over octaves into 12 rows, C at the bottom to B at the top
   (naturals labelled), its own scale with a 30 dB range.
5. **Word lane**: the same words at the same x positions as in the voice picture, two rows, no duration bars; a
   word with no free row keeps only its tick. The time axis is under it.

**Overtones.** Nothing is done to remove them. Every pitched sound lights its own row *and* a ladder above it: +12
semitones (the octave), +19 (octave + fifth), +24, +28 (two octaves + a major third), +31, +34, +36. So one low A
alone also lights E and C# rows higher up; that is one note, not an A major chord. To find what is played, look for
the lowest row of each ladder and for rows that start together; rows where several notes' overtones coincide are
brighter. Drums and consonant-like attacks are vertical streaks through all rows.

**Smear.** A filter narrow enough to tell semitones apart is long in the low rows: 0.79 s at C2, 0.40 s at C3,
0.20 s at C4, 0.10 s at C5, 0.05 s at C6 (the middle half carries most of the weight). Starts and ends look soft
below C3. The bass panel uses filters half that long for its pitch (0.79 s at C1, 0.40 s at C2), at the cost of
blurrier rows (neighbouring semitones still separate; tested on A1 followed by A#1). When the picture and the green
line disagree about *when* the bass moved, the line is right (pYIN needs only a few periods).

**Other limits.** `accomp.wav` is a separation: voice left in it shows as faint rows under the cyan line. The tuning
is one number for the song; a 50-cent offset is ambiguous between two rows. The bass line can jump an octave. The
pitch-class strip counts overtones too.

### Close-ups (`RUN/looks/LL-*.png`)

Same width, same margins, and the header says `close-up LL`, its span and how much finer than a window it is. The
time axis is the span you asked for (ticks every 0.5 s). Nothing is stretched: the cached audio of the span (plus up
to 1 s either side to settle the filters, never past what is revealed) is measured again with every frame step scaled
by span / window, so a close-up has as many frames per pixel as a window. For a 4 s close-up (3.75x a window): lead
cochleagram 1.3 ms frames (2.1 per pixel), backing and stems 2.7 ms (1.05 per pixel), constant-Q 2.9 ms (0.97 per
pixel), lead pitch re-tracked at 2.9 ms (kept only where the song's own 10 ms track has a pitch; the backing line is
that 10 ms track), bass pitch at 2.9 ms. Colour scales, tuning and gates are the song's, so brightness compares with
the windows. What does not get finer: the constant-Q filters' own length (above), the stereo picture's 93 ms frames
and the periodicity panel's 46 ms frames (only their steps shrink: periodicity 2.7 ms in a 4 s close-up, 10 ms in a
window). `LL-voice.png` (1568 x 887) = lead, backing, the periodicity panel (when the cache has `periodicity.npz`,
which `prepare` and `again` make; without it the picture is 1568 x 650 as before), a three-row word lane;
`LL-band.png` (1568 x 830) = the five stems, stereo, bass and attacks (no song-so-far strip); `LL-harmony.png`
(1568 x 750) = all of the harmony picture. A close-up is drawn on the run's own scale (`db` or `ear`).

**`LL-timbre.png` (1568 x 604, or 1568 x 621 on an `ear` run, whose caption is a line longer; only with
`look ... --timbre`): which harmonics are strong.** Read
`refcard-timbre.png` once first (`see.py refcard OUT.png` writes it beside the card as `OUT-timbre.png`): one sung
pitch on two vowel-like envelopes, breathy, with vibrato, an octave up, an octave down, and a click, through this
picture's own code.

The voice picture's filters are as wide as the ear's (1.019 ERB), so above the first four or five harmonics several
of a voice's harmonics share a filter and the panel shows a smooth band. This picture measures the lead again
(the lead stem; the whole vocal under a yellow band, as in the voice picture; same span, same frame step as the
look's lead panel) through 280 gammatone channels 70 Hz-8 kHz whose filters are **0.34 ERB wide, a third of the
ear's**, and draws the channels below 2.5 kHz tall (16 px per ERB, where the voice picture has 12 for filters three
times as wide) and the rest as a strip above a white line (5.8 px per ERB). Each harmonic is then a stripe of its
own. What to read:
- **The stripes** are the harmonics: the lowest lies under the thin cyan pitch line, the 2nd at twice that
  frequency, and so on (on this axis they crowd together upward). Nothing numbers them; count up from the line.
  They all move together with the pitch, and vibrato is every stripe wobbling at once, wider the higher.
- **Which stripes are bright** is the shape of the mouth and throat at that moment (the vowel) and the colour of the
  voice: brightness comes and goes along a stripe as the word changes while the stripes stay where the pitch puts
  them. Bright regions that stay at the same height while the stripes slide through them are resonances; a voice
  whose upper stripes stay bright is brighter or more pressed, one where only the lowest two or three show is
  softer or breathier. That reading is yours: the picture names nothing.
- **Between the stripes** a clean voice is dark; breath and roughness fill the gaps with haze. The fine dotted
  texture in a gap is the two neighbouring harmonics beating at the pitch's own period.
- The y axis is in Hz with more labels than the voice picture (100, 200, 300, 400, 600, 800, 1.2k, 1.6k, 2.4k), the
  time axis and word onsets are the look's, and a thin word lane sits under it. Colour is the song's dB scale, on
  an `ear` run too (that scale would black out the weaker harmonics; the caption says so).

Where it misleads (the caption carries the first two):
- **Onsets and endings are softer than in the voice picture.** A filter a third as wide rings three times as
  long: a sudden start takes about 50 ms to rise (10 to 90%) at 200 Hz, 34 ms at 400 Hz, 17 ms at 1 kHz, 8 ms at
  2.5 kHz, against 17, 11, 6 and 3 ms in the voice picture. Consonants, glottal attacks and quick ornaments blur;
  read timing in `LL-voice.png`.
- **Stripes part only while the pitch is high enough.** Measured valley between two neighbouring equal harmonics:
  at 2.5 kHz 9 dB or more for a voice above about 200 Hz, 4 dB at 150 Hz, 1 dB at 100 Hz; a 150 Hz voice has clear
  stripes (10 dB) up to 1.6 kHz, a 100 Hz voice up to 1 kHz. Above that the harmonics merge and a bright patch is a
  region holding several of them, which is easy to take for one strong harmonic.
- **Noise is about 5 dB fainter against the stripes** than in the voice picture (10 log10(1.019 / 0.34) = 4.8 dB:
  a narrower filter catches less noise and the same harmonic), so breath and "s" sounds look weaker here than they
  are; judge breathiness by the haze between the stripes and against the voice picture, not by brightness alone.
- **Under a yellow band it is the whole vocal stem**, backing voices included: a second voice puts stripes of its
  own between the lead's, and they are not extra harmonics (stripes that are not at whole multiples of the cyan
  line belong to something else). The same goes for instrument bleed in the stem.
- A stripe's brightness is the harmonic's level within 0.5 dB when it sits between two channels; the first
  harmonic lies partly under the cyan line.

`NNN.txt` has the same lines, then every word with start-end, voice, `~` (estimated) or `{` (backing), tags and
all its z-scores, the held notes (word, span, pitch held, median Hz), and a footer: lead pitched frames, words timed
exactly, backing audible.

### The tags: how a word differs from this voice's own usual

Each word is measured on the lead stem and compared with **that voice's own** exact words across the whole song
(robust z-score: median and MAD; the whole song's exact words are used instead if a voice has under 30). A tag is
printed when |z| >= 2.5, at most 3 per word, strongest first. They are relative, never absolute: "louder" on a
quiet singer can still be soft.

| tag | measure | note |
|---|---|---|
| louder / quieter | peak level of the word | |
| higher / lower | median pitch (semitones) | needs >= 3 pitched frames |
| longer / faster | time per syllable | syllables counted from vowel groups, so rough for odd spellings; a held note is "longer" |
| pitch rising / pitch falling | pitch movement across the word (line fit x span) | also needs >= 2 semitones of movement |
| breathier | spectral flatness on pitched frames | |
| rougher | low harmonics-to-noise ratio | only if not already breathier |

Pitch-movement and voice-quality tags need a word of >= 0.15 s with >= 5 pitched frames. `~` words and `{backing}`
words never get tags and are left out of the baselines (an estimated time span would measure the wrong audio; a
backing word is another voice than the one measured). Pitch is always the lead stem's; level, HNR and flatness come
from the whole vocal under a yellow band. Typical density: about a quarter of exact words carry a tag (Krazy 23%,
Trapeze Swinger 26%); most windows have a few, some none, a handful many. A window with many tags is unusual
for that voice, a window with none is that voice's normal.

### A word is marked approximate (`~word`, grey italic) when

- the aligner and whisper both placed it and disagree by more than 0.5 s, or
- whisper did not hear it and the aligner's mean character probability is under 0.05, or
- it has no letters the aligner knows.

## A page to share (`score.py`)

    ./venv3/bin/python v3/score.py runs/<run>                       # runs/<run>/score/: page + song.m4a, for yourself
    ./venv3/bin/python v3/score.py runs/<run> --youtube VIDEO_ID    # runs/<run>/share/: no audio, follows YouTube
    # --by NAME (byline, default Claude), --note "a sentence for the intro", --out FOLDER, --audio FILE

It needs a finished listen (every window revealed, `after.md` sealed) and reads only what the listen left in the run
folder: `state.json`, the notes, `seal.log` and the revealed pictures. It crops each window's voice and band
pictures to their measured panels (lead, backing, the five stems; no word lane, no lyric lines), tiles them side by
side and scrolls them under a playhead. The notes on the page are the sealed files. With `--youtube` the folder is
`index.html` plus the strips in `w/`, holds no audio and no lyric text, and follows an embedded YouTube player's
clock: use the video the audio was fetched from (`WORK/source.txt` has its address) so time zero matches, and serve
it over http(s), since YouTube refuses to embed in a page opened as a file. Without `--youtube` the output holds the
recording; keep it to yourself. The harmony picture and a note's `harmony:` line are not on the page yet. It was
built on two-picture runs; three-picture runs have the same voice and band geometry (checked against
`geometry.json`) and are accepted.

## Pipeline (`prepare`)

| stage | what | output in WORK |
|---|---|---|
| fetch | audio to `song.wav` (ffmpeg, or yt-dlp from `../venv`); LRCLIB synced lyrics (`../words.py`) | `song.wav`, `lyrics.lrc`, `meta.json` |
| separate | Demucs `htdemucs`, two stems, CPU | `vocals.wav`, `accomp.wav` |
| bands | Demucs `htdemucs_6s` on `song.wav`, CPU (it fails on MPS: "Output channels > 65536"); its vocal stem is dropped | `band/{drums,bass,guitar,piano,other}.wav` |
| lead | karaoke model on the vocal stem via `audio-separator` (`mel_band_roformer_karaoke_aufr33_viperx`, MPS): its "Vocals" output = lead, its "Instrumental" output = backing | `lead.wav`, `backing.wav`, `lead.json` |
| transcribe | faster-whisper `medium.en`, int8, word timestamps, VAD on, on the lead stem | `whisper.json` |
| align | each LRC line's own text forced-aligned (torchaudio MMS_FA wav2vec2 CTC, a `*` token at both ends absorbs neighbouring speech, window = LRC line +-1.5 s, lead lines may not overlap). A line's plain words and its `(ad-lib)` words are aligned **separately** (they often overlap in time). Each part is aligned on the lead, the whole vocal and the backing stem and keeps the stem where its text fits best (mean CTC score; the lead wins unless another is better by STEM_MARGIN). Whisper words (on the lead) matched to the lyric are the second witness | `align.json` (per word `stem`) |
| voices | Resemblyzer embeddings of 1.6 s slices of the whole vocal stem, 4/s; each line = mean embedding over its exact words; lines clustered (average linkage, cosine, VOICE_DIST); clusters under 10 s folded in; a change must last 2 lines | `voices.json` |
| features | pYIN pitch on the lead high-passed at 150 Hz, gated by level; RMS level; Praat HNR; spectral flatness (from the whole vocal under the yellow band); backing spectrogram, pitch and audibility; per-word stats and z-scores | `features.npz`, `words.json`, `pyin_<stem>_*.npz` |
| sound | cochleagrams of the lead (whole vocal under the yellow band), backing and the five stems, one scale; stereo picture of `song.wav`; song-so-far blocks (mix cochleagram, per-stem levels) | `sound.npz` |
| periodicity | summary autocorrelogram of `vocals.wav` on semitone rows (24 gammatone channels, half-wave rectified, power 0.3; 46 ms frames every 10 ms; C2-C6 at 3 rows per semitone) and its whole-song colour top. Reads only that file | `periodicity.npz` |
| harmony | tuning of `accomp.wav` (`librosa.estimate_tuning`); constant-Q (36 bins/octave, 11.6 ms frames, grid moved by the tuning) of `accomp.wav` C2-B6 and of `band/bass.wav` C1-C4; the pitch-class fold; pYIN on the bass stem. Reads only those two files | `harmony.npz` |
| render | three PNGs + TXT per window on the chosen `--scale`; the layout marker (and `scale`, when it is `ear`); every panel's pixel box | `windows/NNN-voice.png`, `NNN-band.png`, `NNN-harmony.png`, `NNN.txt`, `render.json`, `geometry.json` (and `ear.json` in WORK on the `ear` scale) |

Every stage is skipped if its output exists, unless a stage it reads from ran (`DEPS` in `see.py`: e.g. `bands` reads
only `song.wav`, so adding the band view to an older work dir runs just `bands`, `sound`, `harmony` and `render`;
`prepare` on a two-picture work dir runs just `harmony` and `render`; on a work dir made before the periodicity
panel, just `periodicity` and `render`). Render always runs, and replaces every window
file; switching `--scale` on an existing cache therefore costs only the render. `again` is the way to add the harmony
picture to the cache of a finished listen: it runs `harmony` if `harmony.npz` is missing (and `periodicity` if
`periodicity.npz` is, so the second listen's close-ups have the panel) and draws only the `NNN-harmony.png` files,
leaving every existing window file as it is. A voice picture rendered before the panel existed stays 1568 x 1150,
and `geometry.json` describes each picture as it is on disk.

`windows/geometry.json` is for page generators: `window`, `nwin`, and per picture (`voice`, `band`, `harmony`) its
size and `panels`, each with an `id` (`lead`, `backing`, `periodicity`, `words`, `held`; `drums`, `bass`, `guitar`, `piano`, `other`,
`stereo`, `attacks`, `sofar`, `lanes`; `notes`, `bassnotes`, `classes`, `words`), its pixel box `x0`, `x1` (from the
left edge), `y0` (top), `y1` (bottom, from the top edge; fractions of a pixel kept), `time: true` when x0..x1 is the
window's 15 s, and for `notes` / `bassnotes` / `periodicity` `midi_lo`, `midi_hi` and `notes`: the pixel row of every labelled note
(`"C4": 267.01`); for `classes`, `rows`. `looks/LL.json` has the same for a close-up; a look made with `--timbre`
also has `pictures.timbre` (and only then): the picture's size, `filter_erb`, `frame_ms`, and the panels
`timbretop`, `timbre` (each with its box, `hz_lo` and `hz_hi`, the frequencies at its bottom and top edges, between
which a frequency's row is linear in ERB-rate, and `hz`, the pixel row of every labelled frequency) and `words`. `--redo STAGE` reruns it and every stage that depends on it; `--only STAGE` runs one; `--window SECONDS` sets
the window length; `--lyrics-offset S` forces a global LRC shift (normally measured and
applied only if over 0.3 s); `--no-split` skips the lead/backing split (lead = whole vocal stem, strip says so; on Krazy it gives 77.8% exact,
the split-apart ad-lib parts alone accounting for the gain over 74.6%).
Technical numbers for the whole run (no lyric text) are in `WORK/summary.json` (`quality`: per window words,
exact, tagged, pitched share, backing audible), copied into the run by `after`.

Runtime on an M3 Pro for a 5:19 song: Demucs ~2 min, karaoke split ~2.5 min (MPS), Whisper ~2.5 min, alignment
~2-3 min (three stems), pYIN (lead + backing) ~1-2 min. The band view adds, measured on a 3:57 song: `bands`
(htdemucs_6s, CPU) 91 s, `sound` 100 s (~650 gammatone channel passes at ~0.15 s each for a 4-minute song),
render 10 s for 16 windows x 2 pictures: about 15 min in all for a 5-minute song. A 9:30 song takes roughly twice
that. The cache grows by ~220 MB (five stems) + ~17 MB (`sound.npz`). The harmony picture adds, measured on the
9:30 song: `harmony` 13 s (0.34 s per window) and 11-13 s to draw 38 harmony pictures (0.3 s each), `harmony.npz`
25 MB; so `again` on a finished listen takes under half a minute. The periodicity panel adds, on the same song:
`periodicity` 20 s (0.5 s per window; 24 gammatone passes and their spectra), `periodicity.npz` 6 MB, and nothing
measurable to the render. A 4 s `look` with all three pictures takes about 4 s, an 8 s one about 6 s; `--timbre`
adds 2.5 s to the first and 4 s to the second (280 narrow filters, twice over under a yellow band). First run downloads models
(htdemucs, htdemucs_6s, whisper medium.en, MMS_FA 1.2 GB, Resemblyzer, the karaoke roformer ~900 MB into
`v3/audio/_models`): 3.8 GB in all, measured from empty caches. A 2:06 song took 5 min 52 s end to end on the
M3 Pro, downloads included, and its cache is 269 MB.

The song needs **synced** lyrics on LRCLIB; without them `prepare` stops.

## Setup (once)

`./setup.sh` at the repository root does all of this; it is listed here because each line records a real pitfall.

    python3.13 -m venv venv3
    venv3/bin/pip install "setuptools<81" -c constraints.txt   # webrtcvad (via resemblyzer) still imports pkg_resources
    venv3/bin/pip install -c constraints.txt "torch==2.6.0" "torchaudio==2.6.0" demucs==4.1.0 faster-whisper==1.2.1 \
        librosa==1.0.0 matplotlib soundfile praat-parselmouth==0.4.7 resemblyzer==0.1.4
    venv3/bin/pip uninstall -y typing          # resemblyzer drags in the old 'typing' backport; it breaks 3.13
    # lead/backing split. Keep torch pinned (the constraints file does it), or pip pulls a newer torch, which breaks
    # torchaudio's forced aligner:
    venv3/bin/pip install -c constraints.txt "audio-separator==0.45.0" audioread
    python3.13 -m venv venv && venv/bin/pip install -U "yt-dlp[default]"   # apart, so it can be upgraded freely

`constraints.txt` is the `pip freeze` of the environment the tool was developed in (Python 3.13.3, macOS 14, arm64),
so every transitive dependency installs at a version known to work. It was developed on Apple Silicon (arm64) only:
native torch wheels exist there and the karaoke model runs on MPS. Nothing else has been tried. ffmpeg and ffprobe are
taken from PATH (or `/usr/local/bin`); yt-dlp from `../venv/bin`. Whisper gets a numpy array, not a path, because
faster-whisper 1.2.1 and PyAV 19 disagree about `av.open`. `audioread` is needed because librosa 1.0 no longer
depends on it but audio-separator imports it. webrtcvad has no wheel and is compiled, so Xcode's command line tools
are needed.

## Token cost (for anyone running a listen)

Images: the model sees an image at about width x height / 750 tokens once its long edge is at most 1568 px (larger
images are scaled down first, which blurs thin panels; every picture is exactly 1568 px wide, so nothing is scaled).
`NNN-voice.png` 1568 x 1387 = **~2,900 tokens** (the periodicity panel is ~500 of them), `NNN-band.png` 1568 x 1050 = **~2,200 tokens**,
`NNN-harmony.png` 1568 x 750 = **~1,570 tokens**: ~6,700 per window for the pictures, plus `next`'s output, the Read
calls and the note (~600-900): **~7,400 tokens per 15 s window** (estimate from the image sizes; not yet measured in
a session; a two-picture run is ~5,300). A **close-up** is 1568 x 887 + 830 + 750 = ~1,850 + ~1,740 + ~1,570 =
~5,200 for the pictures, **~5,500 tokens per look** with its output and `saw:` note (six looks: ~33k). With
`--timbre` a look has a fourth picture, 1568 x 604 = ~1,260 tokens: **~6,800 tokens per look** (six such: ~41k); the
timbre card (1568 x 889) is ~1,860, once, and only if you use it. A **second
listen** window is the harmony picture, the text and your first note: **~2,400 tokens** (with `--full` ~7,500). The
reference card (1568 x 1397) is ~2,900, once. That is about the same per second of music as the
old one-picture 10 s windows (1600 x 1360, scaled to 1568 x 1333: ~2,800 tokens; measured ~3,300-3,500 per window in
one whole-song session). Reading SKILL.md and this manual once adds ~10k.
`prepare` runs locally and costs no tokens.

| Song   | Windows (15 s) | First listen, three pictures | plus six looks | Second listen |
|--------|----------------|------------------------------|----------------|---------------|
| 3 min  | 12             | ~100k                        | ~135k          | ~40k          |
| 4 min  | 16             | ~130k                        | ~165k          | ~50k          |
| 5 min  | 20             | ~160k                        | ~195k          | ~60k          |

(Each column includes ~11k for SKILL.md, this manual and the reference card; second listen without looks, each look
adds ~5.5k, or ~6.8k with `--timbre`.) Start a fresh session: from a ~30k start a 5-minute song ends near 190k, 225k with every look spent; over ~6 minutes, or in a long session, compaction will land mid-song. Usage limits feel more than context size, since every step re-reads
the conversation from cache: a 5-minute song is a few million cached tokens read (estimated, not measured for
a whole song). Reading only `NNN-voice.png` for a window would cut its cost by more than half, but the band
picture is what carries the instruments and the harmony picture the notes.

## Constants

All in the `C` dict at the top of `see.py` (also in `summary.json`): window 15 s (`--window`); Z_TAG 2.5; PITCH_MOVE_ST 2;
MIN_VOICED 3; QUAL_MIN_DUR 0.15 s / QUAL_MIN_VOICED 5; F0 70-600 Hz; PITCH_HPF 150 Hz; PITCH_GATE_DB 35;
CTC_PAD 1.5 s; CTC_MIN 0.05; XCHECK_MAX 0.5 s; MONO_MAX_PUSH 0.5 s; MATCH_SIM 0.72; OFFSET_APPLY 0.3 s;
VOICE_STEM vocals.wav; VOICE_DIST 0.27 (per-line clustering: Krazy keeps its 3 voices apart up to 0.30; on
Trapeze Swinger, below 0.20 the labels break into many small clusters that flip line to line); VOICE_MIN_TOTAL 10 s; VOICE_MIN_RUN 2 lines; VOICE_RATE 4/s;
KARAOKE_MODEL (above); STEM_MARGIN 0.05; BACK_ACTIVE_DB 30; BACK_FMAX 4 kHz; WORD_ROWS 5. Band view: BAND_MODEL
htdemucs_6s; COCH 50 Hz-8 kHz, lead 96 channels / 5 ms, backing and stems 64 / 10 ms; COCH_TOP_PCT 99.5,
COCH_RANGE_DB 60; PAN_HOP 50 ms, PAN_BINS 41, PAN_FMIN 40 Hz, PAN_RANGE_DB 35; MONO_SIDE 1e-4; SOFAR_BLOCK 0.5 s,
SOFAR_N 40, SOFAR_RANGE_DB 50; HELD_MIN 0.7 s, HELD_MAX 2, HELD_SPAN 1.5-2.5 s, HELD_CYCLES 5; pictures 1568 px wide
(FIG_W2 15.68 in at 100 dpi), VOICE_H 11.5 in, BAND_H 10.5 in. Harmony picture: HARM_BPO 36, HARM_HOP 256 samples
at 22050 Hz, HARM_LO..HI MIDI 36..95 (C2..B6), BASS_LO..HI 24..60 (C1..C4), BASS_FSCALE 0.5, HARM_TOP_PCT 99.5,
HARM_RANGE_DB 50, CHROMA_RANGE_DB 30, BASS_F0 30-400 Hz, BASS_GATE_DB 35, HARM_H 7.5 in, HARM_WORD_ROWS 2.
Close-ups: LOOKS 6, LOOK_MIN 2 s, LOOK_MAX 8 s, LOOK_PAD 1 s, LOOK_WHY_WORDS 3, LOOK_SAW_WORDS 2, LOOK_PYIN_HOP
64 samples, LOOK_VOICE_H 6.5 in, LOOK_BAND_H 8.3 in. Intensity scale: SCALE db (or ear: EAR_POWER 0.3, EAR_TAU
0.05 s, EAR_ADAPT 0.5, EAR_TOP_PCT 99.5). Periodicity panel: PER_SR 22050, PER_HOP 10 ms, PER_FRAME 46 ms, PER_N 24
channels, PER_FMIN..FMAX 80 Hz..4 kHz, PER_COMPRESS 0.3, PER_CUT 50 Hz, PER_LO..HI MIDI 36..84 (C2..C6),
PER_TOP_PCT 99.5, PER_H 2.25 in (4.6 px per semitone, the bass panel's density), PER_GAP 0.12 in (a voice picture
with the panel is VOICE_H + 2.37 in = 1387 px, a voice close-up 887 px). Timbre picture: TIMBRE_BW 0.34 ERB,
TIMBRE_N 280 channels from TIMBRE_FMIN 70 Hz to COCH_FMAX (9 per ERB, 3 per filter width), TIMBRE_SPLIT 2.5 kHz,
TIMBRE_H 3.3 in for the channels below it, TIMBRE_TOP_H 0.6 in for the strip above. Set on Makaveli "Krazy" (rap, 5:19) and checked on Iron & Wine
"The Trapeze Swinger" (quiet, sung, acoustic, 9:30).

## Measured quality

Numbers from `quality()` in `see.py` (in `WORK/summary.json` as `quality`; that file is look-ahead, so a listener
reads it only after `after` copies it into the run). Only tool-quality numbers are given, nothing about a song's
form. "Exact" = word timed exactly (not `~`);
"pitched" = share of 10 ms frames with a lead pitch value; "pitched (active)" = the same over frames whose lead level
is above the song's 40th percentile.

| | Krazy, before the lead/backing split | Krazy, with the split | Trapeze Swinger |
|---|---|---|---|
| words exact | 744/997 = 74.6% | 831/997 = 83.4% | 361/453 = 79.7% |
| exact in windows 027-031 | 99/220 = 45% (027 49%, 028 67%, 029 49%, 030 11%, 031 52%) | 158/218 = 72% (63, 77, 86, 60, 82%) | - |
| pitched / pitched (active) | 56.6% / 65.9% | 59.3% / 71.5% | 49.8% / 77.0% |
| pitched (active) in 027-031 | 29-74% per window | 71-78% per window | - |
| voices / voice changes | 3 / 8 | 3 / 10 (94% of lines keep their old label) | (not listed here: it would tell a future listener the song's form) |
| tagged (of exact words) | 23.1% | 23.0% | 26.0% (a few windows with none, a few with half or more) |
| lead share of vocal energy | - | 88% | 52% |

What made the difference on Krazy's outro (027-031): the lead stem (45% -> 52%), then aligning a line's `(ad-lib)`
words apart from its plain words, each part on its best stem (-> 72%). Whisper on both stems as a merged witness was
tried (+1 point) and left out (2.5 min more).

## Known weak spots

- **The karaoke split is a guess about who leads.** On a doubled or quiet sung voice it can put the main singer, or
  half of a double, into "backing" (Trapeze Swinger: the lead stem holds only 52% of the vocal energy, and in a few
  sung windows the lead is pitched in under 20% of the active frames). The per-line stem choice and the yellow band catch the
  worst of it; the lead pitch line still has gaps there.
- **Backing can be instruments.** Demucs (and BS-Roformer, tried) leave sustained instrument sound in the vocal
  stem of a quiet acoustic song; the karaoke model puts it in "backing", so the backing strip and "backing audible"
  are high even in passages with no lyric words (Trapeze Swinger: "audible" 81% of the song).
  Read the strip's shapes (words, vibrato, syllables) rather than the percentage.
- **Pitch on the whole vocal is not used**: pYIN locks onto a low instrument tone (~65-90 Hz) there. So under a
  yellow band the cyan line is the lead stem's only, and can be sparse.
- **Overlapping voices** still cost words: Krazy's outro is at 72% exact (from 45%). Forced alignment is sequential
  within each part, and LRC lines don't mark which plain words a second voice sings.
- **Whisper skips** repeated hooks and dense outros; there the aligner's own confidence decides alone.
- **Voice labels** come from speaker embeddings of the whole vocal averaged per line; "a different voice" can also be
  the same person with very different production. VOICE_DIST was set between two songs only (0.20-0.30 works
  for both). Short ad-libs by someone else inside a line take the line's label.
- **Tags are relative to the whole song** (look-ahead in the baseline, see above), and `longer` is partly just held
  notes in sung music; `breathier`/`rougher` include whatever bleed is in the stem.
- **The six-stem split is a model's guess too.** `htdemucs_6s`'s piano stem is its weakest (bleed from guitar and
  voice shows up there faintly), and "other" collects everything it can't place. Read them by shape and loudness on
  the shared scale: a faint, smeared stem is likely bleed; a bright one with its own onsets is an instrument.
- **The stereo picture is of the whole mix**, so the centred lead voice and bass dominate the middle; reverb and
  wide stereo show as a spread. It has no per-instrument placement.
- **Syllables** are counted from vowel groups, so `longer`/`faster` is rough for unusual spellings.
- **The LRC can be wrong or out of order** (crowd-sourced); those words usually come out `~`. The LRC offset is
  measured and applied automatically when over 0.3 s (Trapeze Swinger: -0.42 s).
- **Look-ahead is possible by hand**: the cache holds every window. The seal proves note order, it doesn't hide
  files.
- **The periodicity panel is not a transcription.** Its ladder of sub-multiples, the bright shared rows under two
  voices, its width above A4 and the merging of voices a semitone or an octave apart are described under "Reading
  the pictures" and shown on the reference card; nothing is done to remove them. It reads the whole vocal stem, so
  bleed has ridges too.
- **The timbre picture trades time for frequency** and resolves less the lower the voice; under a yellow band it
  holds every voice in the stem. See "Where it misleads" under "Close-ups". Its height (604 px) is the least that
  kept the stripes of the dev song's 165 Hz held notes apart up to 2.4 kHz by eye (9 px from stripe to stripe
  there); the prototype was twice as dense. TIMBRE_H is the one number to raise if they prove too fine.
- **Caches whose `sound` stage ran with an earlier version (their `sound.npz` stores a `lead_hop` of exactly 0.005)
  draw every cochleagram panel late**, by 0.23% of the time since the song's start (0.3 s at 2:00, 0.7 s at 5:00).
  The pitch line, the words, the attacks, the stereo picture, the harmony picture and every close-up were always on
  true time. `sound` now stores the true frame steps; an existing cache keeps its pictures until
  `prepare ... --redo sound` (about 2 min for a 4-minute song).

## The score page (`score.py`)

    ./venv3/bin/python v3/score.py runs/<run>                         # RUN/score/index.html + song.m4a (local only)
    ./venv3/bin/python v3/score.py runs/<run> --youtube VIDEO_ID      # RUN/share/: no audio, follows a YouTube embed
    ./venv3/bin/python v3/score.py runs/<run> --second runs/<second>  # both listens on one page, behind a switch
    #   [--pictures DIR] [--omit-second-after] [--redact NAME=REPLACEMENT ...] [--link URL] [--by WHO] [--note "..."]

`score.py` turns a finished listen into one self-contained page (inline CSS and JS, no external scripts or fonts): the
windows' pictures cropped to their panels and tiled into a strip that scrolls under a fixed playhead driven by the
audio clock, the sealed note of the current window under it, the after-note below. It only reads the run (and the
cache, for `geometry.json`, the lyric and `song.wav`); it writes nothing but its output folder. Notes are shown as
written: escaped, `**bold**`, and every `m:ss` or `m:ss.ss` made a button that seeks there.

What the page shows, by the run's layout:

- **`voice+band`**: the lead and backing panels and the five stems as tracks; the note as `feel` (headline), `voice`,
  `band`, `words`; the guess going in (the previous note's `expect`) and the guess made next.
- **`voice+band+harmony`**: the same, plus a **notes track** under the stems (the harmony picture's piano roll and its
  bass panel, with a few note names from the geometry's labelled rows) and the optional `harmony:` line as a fourth
  column.
- **`--second RUN2`** (a `layout: second` run of the same recording): a switch between the two listens; the playhead
  and playback carry across. The second listen has the notes track, its own `feel` across the top, `harmony` and
  `changed` as columns, "what I said the first time" (the `feel` of `NNN-first.md`), its own `expect` guesses and
  its own seal times. Both after-notes follow, each under its own heading.
- **Close-ups** (any layout with `looks/`): a numbered bracket over each look's span on the strip, and ticks on the
  minimap. When the playhead reaches a look, a **card** with its text comes up over the right of the strip (the part
  not played yet): the why (logged before the picture existed) and the `saw:` note. The look's own pictures are not
  on the page. The card stays for a reading time counted from the look's start (its word count at 3.5 words a
  second, between 12 and 30 s, and never less than the look's span), then fades; the next look's card replaces it,
  and it can be closed by hand. It is a function of the clock, so pausing holds it. On a narrow screen the card
  sits under the strip. Clicking a bracket, or "close-up NN" in a note, goes there.
- The "hide what I hadn't seen yet" box (on by default) veils every later window, and every close-up until the
  window it was made in is open.
- **One screen.** The header is one slim row (title, byline, "How this was made", which opens the intro text, a
  link down to the after-notes, and with `--link` a button for it). On a viewport of at least about 1100 x 700 the
  header, transport, strip and current note together are exactly the viewport's height: the note takes what
  room is left and its columns scroll inside themselves, fading at the bottom when there is more. The after-notes
  and the footer are below that. Smaller viewports get an ordinary flowing page.
- Nothing pauses by itself. Space (or the play button) pauses, and the transport says so.

Options:

- **`--youtube ID`** writes `RUN/share/` instead of `RUN/score/`: `index.html` and the cropped strips (`w/` instruments,
  `n/` notes, `c/` close-ups) and nothing else. **It holds no audio** (the sound is the embedded video, followed over
  postMessage; use the video the audio was fetched from so time zero matches) **and no lyric lane**: no image on
  the page contains lyric text. It has to be served over http(s). Without `--youtube` the folder also holds the
  recording as `song.m4a`, so it stays local.
- **`--pictures DIR`** takes `NNN-voice.png` / `NNN-band.png` and their `geometry.json` from DIR instead of
  `RUN/windows/`, e.g. from a copy of the cache re-rendered after a fix in the tool (`prepare --work COPY --redo
  sound` into a scratch run folder). The footer then says the strips were redrawn (`--redrawn "..."` replaces that
  sentence).
- **`--omit-second-after`** leaves the second listen's after-note off the page. **`--redact NAME=REPLACEMENT`**
  (repeatable) replaces a whole-word name in all displayed note text at build time. Neither touches a sealed file;
  the hashes shown are those of the files as written, and the footer says in a sentence what was done.
- **`--link URL`** adds a "get the tool" link; `--by` and `--note` set the byline and a sentence of the intro;
  `--audio FILE` and `--out DIR` override the defaults. `?t=SECONDS` and `?listen=2` in the page's URL choose where
  it opens, and `window.__t` is a clock hook for rendering frames.
- **`--url ADDRESS`** (the page's public address) adds link-preview tags; a 1200 x 630 `card.jpg` put in the output
  folder beforehand becomes the preview picture. `--link` and `--url` must be http(s), and `--youtube` an
  11-character id. A `--youtube` page carries a content-security policy: only its own pictures and the YouTube
  player load. Strip pictures load as the playhead nears them.

Every crop is made **by panel id** from the tool's own boxes (`windows/geometry.json` beside the pictures or in the
cache, `looks/LL.json` for a close-up), with the in-panel caption rows left out; a run whose cache is gone falls back
to the two-picture layout's hardcoded rows. The code asserts that a picture has the size its geometry says, and that
no crop touches a panel that holds lyric text (`words`, `held`) or reaches below the first of them, where the lyric
lines are.

**The lyric sentence is measured.** At build time the displayed text of each listen (window notes, close-up whys and
notes, after-note) is compared with the cached lyric (`lyrics.json` / `lyrics.lrc`), with the song's title left out:
the longest run of consecutive lyric words anywhere, the longest inside quotation marks, and how many quoted phrases
hold three or more lyric words. The footer's wording follows from those numbers ("paraphrase the words", "mostly
paraphrase ... quoting one phrase of three words or more", or "quote short phrases of the lyric (seven of three words
or more, none longer than six words, the title aside)"), and the numbers are printed to stderr. Without a cached
lyric the footer makes no claim about the notes. The notes themselves may quote the lyric, so read them before
publishing a share folder.

## Files

- `see.py`: the tool. `score.py`: the playable page of a finished listen. `refcard.png`: a rendered reference card
  (`see.py refcard`). `tests/test_seal.py`: the seal tests (three-, two- and one-picture runs, `look`, `again`).
  `tests/test_harmony.py`: the harmony picture's axis tests. `tests/test_periodicity.py`: the periodicity panel, the
  `ear` scale and every panel box against the pixels. `tests/test_timbre.py`: the timbre picture of `look --timbre`.
  `refcard.png`, `refcard-timbre.png`: the two rendered reference cards (`see.py refcard`). `score.py`: the playable score page of a finished listen, or of a first and a second listen together (see "The score page").
- `../words.py`: the LRCLIB lookup. `../constraints.txt`: the pinned versions. `../venv3/`, `../venv/`: built by
  `setup.sh`, gitignored.
- `audio/<run>/` (gitignored): the cache per run: the recording, the stems, the lyric, every window (with
  `geometry.json`) and `summary.json`. `audio/_models/` (gitignored): the karaoke model weights.
- `../runs/` (gitignored): the run folders. After a listen a run folder holds the lyric, window by window.
