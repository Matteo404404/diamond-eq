"""Measurement lookup across the public squig.link graph databases.

squig.link publishes a directory of reviewer sites; each site lists its
measured models in data/phone_book.json and stores one text file per channel.
All of them use IEC 60318-4 (711-style) ear simulators, which is what the
Diamond target is defined on; the B&K 5128 databases are skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import curves

SITES_URL = "https://squig.link/squigsites.json"
USER_AGENT = "diamondeq/0.1 (headphone EQ tool)"
INDEX_MAX_AGE = 14 * 24 * 3600
KINDS = ("IEMs", "Headphones", "Earbuds")

# Reviewer sites in the order PEQdB trusts them (accuracy of their rigs), then
# the other large databases. Sites not listed come after these.
PRIORITY = [
    "superreview", "oratory1990", "timmyv", "therollo9", "precog", "vsg", "audioamigo", "pw",
    "sai", "gadgetrytech", "kuulokenurkka", "hbb", "vortexreviews", "eliseaudio", "jaytiss",
    "csi-zone", "dhrme", "ish",
]
# graph.hangout.audio lists its models but refuses data downloads from anything
# but its own web page (HTTP 403). That is the owner's choice, so it is left out.
BLOCKED = {"crinacle"}

# oratory1990's measurements (GRAS ear simulators), as published in the AutoEq
# project: one CSV per model plus a tab-separated index.
AUTOEQ = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/measurements/oratory1990"
_AUTOEQ_KINDS = {"over-ear": "Headphones", "in-ear": "IEMs", "earbud": "Earbuds"}


def cache_dir() -> Path:
    d = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "diamondeq"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _site_base(site: dict) -> str:
    kind, user = site.get("urlType"), site["username"]
    if kind == "altDomain":
        return site["altDomain"].rstrip("/")
    if kind == "root":
        return "https://squig.link"
    if kind == "labFolder":
        return f"https://squig.link/lab/{user}"
    return f"https://{user}.squig.link"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _entries(book, site: dict, kind: str, base: str) -> list[dict]:
    out = []
    for brand in book if isinstance(book, list) else []:
        bname = str(brand.get("name", "")).strip()
        for p in brand.get("phones", []):
            if isinstance(p, str):
                name, files, variants = p, [p], [""]
            else:
                name = str(p.get("name", "")).strip()
                f = p.get("file") or name
                files = [f] if isinstance(f, str) else [str(x) for x in f]
                suffix = p.get("suffix")
                if isinstance(suffix, list) and len(suffix) == len(files):
                    variants = [str(s) for s in suffix]
                elif len(files) > 1:
                    variants = files
                else:
                    variants = [""]
            if not name or not files:
                continue
            full = f"{bname} {name}".strip()
            out.append({"name": full, "key": _norm(full), "kind": kind, "site": site.get("name", site["username"]),
                        "user": site["username"], "base": base, "files": files, "variants": variants})
    return out


def _autoeq_entries() -> list[dict]:
    out = []
    try:
        rows = _get(AUTOEQ + "/name_index.tsv").decode("utf-8", errors="replace").splitlines()[1:]
    except (urllib.error.URLError, OSError):
        return out
    for row in rows:
        cols = row.split("\t")
        if len(cols) < 4 or cols[3].strip() not in _AUTOEQ_KINDS or not cols[2].strip():
            continue
        name, form = cols[2].strip(), cols[3].strip()
        out.append({"name": name, "key": _norm(name), "kind": _AUTOEQ_KINDS[form], "site": "oratory1990",
                    "user": "oratory1990", "base": f"{AUTOEQ}/data/{form}", "files": [name], "variants": [""]})
    return out


def build_index(progress=None) -> dict:
    """Download every site's model list (one small request per database)."""
    sites = json.loads(_get(SITES_URL))
    jobs = []
    for site in sites:
        if site["username"] in BLOCKED:
            continue
        for db in site.get("dbs", []):
            if db.get("type") in KINDS:
                jobs.append((site, db["type"], _site_base(site) + "/" + db.get("folder", "/").strip("/")))

    def load(job):
        site, kind, base = job
        base = base.rstrip("/")
        try:
            return _entries(json.loads(_get(base + "/data/phone_book.json")), site, kind, base)
        except (urllib.error.URLError, ValueError, OSError, KeyError):
            return []

    entries, done = _autoeq_entries(), 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(load, jobs):
            entries += result
            done += 1
            if progress:
                progress(done, len(jobs))
    index = {"built": time.time(), "databases": len(jobs), "entries": entries}
    (cache_dir() / "index.json").write_text(json.dumps(index))
    return index


_index_cache: dict | None = None


def load_index(refresh: bool = False, progress=None) -> dict:
    global _index_cache
    path = cache_dir() / "index.json"
    if not refresh and _index_cache is None and path.exists():
        try:
            _index_cache = json.loads(path.read_text())
        except ValueError:
            _index_cache = None
    if refresh or _index_cache is None:
        _index_cache = build_index(progress)
    return _index_cache


def index_age() -> float | None:
    path = cache_dir() / "index.json"
    return time.time() - path.stat().st_mtime if path.exists() else None


def _rank(user: str) -> int:
    return PRIORITY.index(user) if user in PRIORITY else len(PRIORITY)


def search(query: str, limit: int = 30) -> list[dict]:
    """Models matching every word of the query, each with its measurements."""
    words = [_norm(w) for w in query.split()]
    words = [w for w in words if w]
    if not words:
        return []
    groups: dict[str, dict] = {}
    for e in load_index()["entries"]:
        hay = e["key"] + _norm(e["site"])
        if not all(w in hay for w in words):
            continue
        g = groups.setdefault(e["key"] + "|" + e["kind"], {"name": e["name"], "kind": e["kind"], "measurements": []})
        for f, v in zip(e["files"], e["variants"]):
            g["measurements"].append({"site": e["site"], "user": e["user"], "variant": v, "base": e["base"], "file": f})
    joined = _norm(query)
    out = sorted(groups.values(), key=lambda g: (
        0 if _norm(g["name"]) == joined else 1,
        min(_rank(m["user"]) for m in g["measurements"]),
        len(g["name"]),
    ))
    for g in out:
        g["measurements"].sort(key=lambda m: _rank(m["user"]))
    return out[:limit]


def _fetch_text(url: str) -> str | None:
    path = cache_dir() / "measurements" / (hashlib.sha1(url.encode()).hexdigest() + ".txt")
    if path.exists():
        return path.read_text(errors="replace") or None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = _get(url).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            path.write_text("")  # remember that this channel does not exist
            return None
        if e.code in (401, 403):
            raise LookupError(f"{urllib.parse.urlparse(url).netloc} does not allow its data to be downloaded; "
                              "choose a measurement from another site") from e
        raise
    path.write_text(text)
    return text


def fetch_measurement(base: str, file: str) -> np.ndarray:
    """Response of one measurement on the analysis grid (channels averaged)."""
    if base.startswith(AUTOEQ):
        text = _fetch_text(f"{base}/{urllib.parse.quote(file)}.csv")
        if not text:
            raise LookupError(f"no measurement data found for {file!r}")
        return curves.to_grid(*curves.parse_measurement(text))

    def url(suffix: str) -> str:
        return f"{base.rstrip('/')}/data/{urllib.parse.quote(file + suffix)}.txt"

    for channels in ((" L", " R"), (" L1", " R1", " L2", " R2", " L3", " R3"), ("",)):
        found = []
        for ch in channels:
            text = _fetch_text(url(ch))
            if text:
                try:
                    found.append(curves.to_grid(*curves.parse_measurement(text)))
                except ValueError:
                    pass
        if found:
            return curves.average(found)
    raise LookupError(f"no measurement data found for {file!r} at {base}")
