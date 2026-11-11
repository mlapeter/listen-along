"""words.py: synced lyrics from LRCLIB, released line by line, never ahead of the window.

Lyrics are copyrighted: they are stored only in the run folder (DIR/src/), and nothing here prints
a whole song. fetch reports counts and first/last timestamps only; each window prints only the lines
newly released by that window.
"""
import json, os, re, time, urllib.parse, urllib.request, urllib.error

UA = "listen.py/2 (prosthetic-ear)"
API = "https://lrclib.net/api"
_STAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text):
    """[(t_seconds, line), ...] sorted by time. Lines with several stamps appear once per stamp;
    metadata tags like [ar:...] and empty lines are dropped."""
    out = []
    for raw in (text or "").splitlines():
        stamps = _STAMP.findall(raw)
        if not stamps:
            continue
        line = _STAMP.sub("", raw).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), line))
    out.sort(key=lambda x: x[0])
    return [(t, l) for t, l in out if l]


def release(lines, prev_end, end, offset=0.0):
    """Lines newly released by a window ending at `end`, the previous one having ended at prev_end:
    stamp + offset < end, and >= prev_end (so each line is released exactly once)."""
    return [(t + offset, l) for t, l in lines if prev_end <= t + offset < end]


def release_idx(lines, done, end, offset=0.0):
    """Indices of lines newly released by a window ending at `end`: stamp + offset < end and not
    released before. Keyed by index, so changing the offset mid-listen never repeats or skips a line
    (a line the new offset moves into the past comes out now, once)."""
    return [i for i, (t, _) in enumerate(lines) if i not in done and t + offset < end]


def _get(url, tries=5, offline=False):
    if offline or os.environ.get("LISTEN_OFFLINE") == "1":
        raise RuntimeError("offline: no network")
    wait = 2.0
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 503) and i < tries - 1:
                time.sleep(wait); wait *= 2; continue
            raise
        except urllib.error.URLError:
            if i < tries - 1:
                time.sleep(wait); wait *= 2; continue
            raise


def lrclib(artist, title, duration=None):
    """Best LRCLIB record for (artist, title, duration). Tries /api/get, then /api/search (by fields,
    then free text); a failure of one (503 after retries, timeout, anything) falls through to the next.
    Prefers synced lyrics, then the closest duration. Returns the record, None if nothing was found,
    or raises the last error if every request failed."""
    errors = []; res = []
    if artist and title and duration:
        q = urllib.parse.urlencode(dict(artist_name=artist, track_name=title, duration=int(round(duration))))
        try:
            r = _get(f"{API}/get?{q}")
            if r and r.get("syncedLyrics"):
                return r
            if r:
                res.append(r)
        except Exception as e:
            errors.append(e)
    queries = [dict(track_name=title, artist_name=artist)] if artist else []
    queries.append(dict(q=f"{artist} {title}" if artist else title))
    for qd in queries:
        try:
            got = _get(f"{API}/search?" + urllib.parse.urlencode(qd)) or []
        except Exception as e:
            errors.append(e); continue
        res += got
        if any(r.get("syncedLyrics") for r in got):
            break
    if not res:
        if errors and len(errors) == (1 if artist and title and duration else 0) + len(queries):
            raise errors[-1]
        return None
    dur = duration or 0
    res.sort(key=lambda r: (not r.get("syncedLyrics"), not r.get("plainLyrics"),
                            abs((r.get("duration") or 0) - dur) if dur else 0))
    return res[0]


def store(src_dir, rec):
    """Save the chosen record in DIR/src/ and return a short, text-free report."""
    os.makedirs(src_dir, exist_ok=True)
    json.dump(rec, open(os.path.join(src_dir, "lyrics.json"), "w"))
    synced = rec.get("syncedLyrics") or ""
    if synced:
        open(os.path.join(src_dir, "lyrics.lrc"), "w").write(synced)
        lines = parse_lrc(synced)
        return dict(kind="synced", lines=len(lines), first=lines[0][0] if lines else None,
                    last=lines[-1][0] if lines else None, duration=rec.get("duration"))
    if rec.get("plainLyrics"):
        return dict(kind="plain", lines=len(rec["plainLyrics"].splitlines()), duration=rec.get("duration"))
    return dict(kind="none")


def load(src_dir):
    """(kind, lines) from DIR/src: kind 'synced' with timed lines, 'plain' (not shown), or None."""
    p = os.path.join(src_dir, "lyrics.lrc")
    if os.path.exists(p):
        return "synced", parse_lrc(open(p).read())
    j = os.path.join(src_dir, "lyrics.json")
    if os.path.exists(j):
        rec = json.load(open(j))
        if rec.get("plainLyrics"):
            return "plain", []
    return None, []
