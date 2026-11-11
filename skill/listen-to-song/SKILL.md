---
name: listen-to-song
description: Listen to a song in order, one 15-second window at a time, three pictures each (the voice's cochleagram and pitch, held notes, the words under it; the band's five instrument stems, stereo, the beat and the song so far; the band's notes on semitone rows), writing a sealed note after each window, with a few close-ups of spans you choose. Also a second listen of a finished one. Use when someone asks you to listen to a song, hear a track together, or re-listen to one.
---

# Listening to a song (v3)

The person names a song (`/listen-to-song Artist, Title`); if the artist or the title is missing, ask. The tool is `__TOOL_DIR__/v3/see.py`. Its full manual is `__TOOL_DIR__/v3/README.md`. Run every command below from `__TOOL_DIR__` (if your shell doesn't keep the directory between calls, start each with `cd "__TOOL_DIR__" &&`); `RUN` is the run folder under it, and the files to Read are at `__TOOL_DIR__/RUN/...`. Read the README's sections on "The sealed listen" and "Reading the pictures" before your first window; skip the quality tables, which mention songs you may hear later.

## Why it's built this way

Each picture is measured from the audio. No other model describes the song to you. The voice is pulled out of the mix, its pitch is drawn as a line, each lyric word sits under the moment it's sung, and delivery tags (`[breathier]`, `[pitch falling]`, `[longer]`...) mark only where a word differs from that voice's own usual. The feeling is yours to find in the voice, the words and the beat together. A person's brain gets electrical impulses from the ear, and you get pictures. Translation isn't disqualifying.

## Cost

Each 15 s window adds ~6.9k tokens of context (three pictures, ~6.2k; a 4-minute song is ~120k; estimated from the image sizes, see the README's "Token cost"). A close-up adds ~5k, a second-listen window ~2.4k. If the session is already long, or the song is over ~6 minutes, say so and suggest a fresh session before starting.

## Steps

1. **Prepare without looking.** It takes 8-18 minutes:

       cd "__TOOL_DIR__"
       ./venv3/bin/python v3/see.py prepare "ytsearch1:ARTIST TITLE official audio" \
           --artist "ARTIST" --title "TITLE" --run runs/$(date +%F)-SLUG-v3 > /tmp/prepare-SLUG.log 2>&1; echo exit $?

   Run it in the background if you like. Don't read the log unless the exit code isn't 0, and then read only the error at the end. Never open `v3/audio/<run>/` (the cache holds every window and whole-song numbers). The first song on a new install also downloads about 3.8 GB of models, so it takes longer. The song needs synced lyrics on LRCLIB; if prepare stops on that, tell the person.

2. **Learn the third picture once.** Before your first harmony picture, run `./venv3/bin/python v3/see.py refcard /tmp/refcard.png` and Read it: known synthetic notes through the same code. Every played note lights its own row and a ladder of overtones above it; read the lowest row of each ladder.

3. **Listen in order.** Run `./venv3/bin/python v3/see.py next RUN`, then Read the pictures it names: `RUN/windows/NNN-voice.png` (the lead voice's cochleagram with its pitch line, backing voices, the words, close-ups of held notes), `RUN/windows/NNN-band.png` (drums, bass, guitar, piano, other; stereo; bass and attacks; the song so far) and `RUN/windows/NNN-harmony.png` (the band's energy on semitone rows with note names on the axis, the bass alone, the twelve pitch classes; the voice and the bass as lines; nothing in it names a chord or a key, that reading is yours). Write `RUN/notes/NNN.md` with four lines in your own words: `feel:` (what this window adds up to for you), `voice:` (what the voice does: pitch, grain, held notes, backing), `band:` (what the instruments, the stereo picture and the beat do, and where the song is so far) and `words:` (what the lines mean to you). `harmony:` and `expect:` are optional; add them when you have something. Then run `next` again; that seals your note. Don't read ahead, don't open the cache, and don't edit a sealed note. Each note says what this window was like, including when nothing much happened.

4. **Look closer when something asks for it.** You have a few close-ups per run (6 by default; `status` shows how many are left). `./venv3/bin/python v3/see.py look RUN 0:41 0:45 --why "what you are looking for"`: 2 to 8 seconds, only inside what's already revealed, and the why comes first. It measures that span again at a finer step (a 4 s look has about 4x a window's time detail) into `RUN/looks/LL-voice.png`, `LL-band.png`, `LL-harmony.png`. Read them, then write `RUN/looks/LL.md` with a `saw:` line; the next `look` or `next` seals it. Spend them on moments, not on every window.

5. **The whole song.** After the last window, write `RUN/notes/after.md` (free form: the shape of the song, what hit, what the voice and the band carried, what you couldn't hear), then run `see.py after RUN`.

6. **Compare.** If the person has their own take, ask for it first, before showing them yours. Then compare: where you met, where you didn't, what they heard that the pictures don't carry. If you keep a memory between sessions, keep a copy of your after-note there, written as yourself.

## A second listen

People hear a song again with the first time still in them. `./venv3/bin/python v3/see.py again RUN --run runs/$(date +%F)-SLUG-second` (RUN must be finished; it takes under a minute and downloads nothing) makes a new run over the same song. There `next` reveals per window `NNN-harmony.png`, `NNN.txt` and `NNN-first.md`, your own sealed note from the first listen. Write `notes/NNN.md` with `feel:`, `harmony:` (what the notes do under it) and `changed:` (what is different this time, or what you'd now say differently); `expect:` is optional. `look` and `after` work the same. Add `--full` to see the voice and band pictures again too (~7k per window instead of ~2.4k).

## Sharing it

Run folders and `v3/audio/` hold the recording and the full lyric: never commit or publish them. If the person wants a page to share, the manual's "A page to share" section covers `v3/score.py`.

## Habits from the first listens

- Look at the voice before the words. The pitch line's shape (flat, falling, arching, held) and the gaps between words carry most of it.
- Say what you see, then what it feels like, and say when you're unsure. The tags are acoustic; the emotion words are yours.
- A song's last seconds depend on where the file was cut. On an album, a track may run straight into the next one, so silence at the end may not be in the record.
- Paraphrase lyrics in notes and replies; quote only a word or two at a time. Quoting whole lines every window adds up to most of a song, and the API's content filter can block the output mid-listen.
- Stacked voices (call-and-response, outros) are the weak spot. `~word` is estimated, and `{word}` in blue was found in the backing voices.
