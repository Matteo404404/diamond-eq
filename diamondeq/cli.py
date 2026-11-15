"""Command line interface. `diamondeq serve` is the background service; the
other commands edit profiles and assignments, which the service picks up."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import daemon, pipewire, profile, selftest, sources, state


def _pick_output(text: str) -> dict:
    sinks = pipewire.list_sinks()
    if text == "default":
        hits = [s for s in sinks if s["default"]]
    else:
        hits = [s for s in sinks if text.lower() in (s["label"] + " " + s["key"]).lower()]
    if len(hits) != 1:
        names = "\n  ".join(f"{s['label']}  [{s['key']}]" for s in sinks)
        sys.exit(f"output {text!r} matches {len(hits)} devices; connected outputs:\n  {names}")
    return hits[0]


def cmd_status(_):
    st = state.load()
    sinks = pipewire.list_sinks()
    profiles = {p["id"]: p for p in profile.load_all()}
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{st['port']}/api/state", timeout=3) as r:
            active = {o["key"] for o in json.load(r)["outputs"] if o["active"]}
        service = "running"
    except OSError:
        active, service = set(), "not running (start it with: systemctl --user start diamondeq)"
    print(f"service: {service}   EQ: {'on' if st['enabled'] else 'off'}")
    for s in sinks:
        pid = st["assignments"].get(s["key"])
        p = profiles.get(pid)
        mark = "*" if s["default"] else " "
        eq = f"{p['name']} ({p['preamp_db']:+.1f} dB preamp, {len(p['filters'])} filters)" if p else "no EQ"
        print(f" {mark} {s['label']}\n     {'ACTIVE' if s['key'] in active else 'idle  '}  {eq}")
    offline = [k for k in st["assignments"] if k not in {s["key"] for s in sinks}]
    for k in offline:
        p = profiles.get(st["assignments"][k])
        print(f"   {st['labels'].get(k, k)} (not connected)\n     {p['name'] if p else 'missing profile'}")


def cmd_search(args):
    for m in sources.search(" ".join(args.query), limit=args.limit):
        sites = ", ".join(dict.fromkeys(x["site"] for x in m["measurements"]))
        print(f"{m['name']}  [{m['kind']}]  — {sites}")


def cmd_add(args):
    results = sources.search(" ".join(args.query))
    if not results:
        sys.exit("no measured model matches; try fewer words, or `diamondeq refresh`")
    model = results[0]
    ms = model["measurements"]
    if args.site:
        ms = [m for m in ms if args.site.lower() in m["site"].lower()]
        if not ms:
            sys.exit(f"{model['name']} has no measurement from a site matching {args.site!r}")
    chosen = ms if args.all else ms[:1]
    print(f"{model['name']}: using " + ", ".join(f"{m['site']} {m['variant']}".strip() for m in chosen))
    prof = profile.create(model["name"], model["kind"], chosen,
                          {"bands": args.bands, "max_loss_db": None if args.exact else args.max_loss})
    profile.save(prof)
    _describe(prof)
    if args.output:
        out = _pick_output(args.output)
        state.update(assignments={out["key"]: prof["id"]})
        print(f"assigned to {out['label']}")


def _describe(prof: dict):
    s = prof["stats"]
    print(f"profile {prof['id']}: preamp {prof['preamp_db']:+.2f} dB, loudness {s.get('loudness_change_db', 0):+.1f} dB")
    if "error_rms_db" in s:
        print("  deviation from target (dB RMS): " + ", ".join(f"{k} Hz {v}" for k, v in s["error_rms_db"].items()))
    for f in prof["filters"]:
        print(f"  {f['type']:3} {f['fc']:8.1f} Hz  {f['gain']:+6.2f} dB  Q {f['q']:.2f}")


def cmd_assign(args):
    out = _pick_output(args.output)
    if args.profile == "none":
        state.update(assignments={out["key"]: None})
        return print(f"{out['label']}: EQ removed")
    hits = [p for p in profile.load_all() if args.profile.lower() in (p["id"] + " " + p["name"]).lower()]
    if len(hits) != 1:
        sys.exit(f"profile {args.profile!r} matches {len(hits)}; see `diamondeq profiles`")
    state.update(assignments={out["key"]: hits[0]["id"]})
    print(f"{out['label']}: {hits[0]['name']}")


def cmd_profiles(_):
    for p in profile.load_all():
        print(f"{p['id']}  —  {p['name']}  ({p['preamp_db']:+.1f} dB, {len(p['filters'])} filters)")


def cmd_export(args):
    p = profile.load(args.id)
    if not p:
        sys.exit("unknown profile")
    print({"apo": profile.to_apo, "graphiceq": profile.to_graphic_eq,
           "json": lambda x: json.dumps(profile.summary(x), indent=2)}[args.format](p), end="")


def cmd_import(args):
    prof = profile.from_apo(args.name, Path(args.file).read_text())
    profile.save(prof)
    _describe(prof)


def cmd_selftest(args):
    profs = [profile.load(args.id)] if args.id else profile.load_all()
    if not profs or not profs[0]:
        sys.exit("no profile to test")
    for p in profs:
        r = selftest.run(profile.filters_of(p), p["preamp_db"])
        print(f"{p['name']}: applied EQ deviates from design by max {r['max_dev_db']} dB "
              f"(at {r['max_dev_at_hz']} Hz), RMS {r['rms_dev_db']} dB")


def cmd_refresh(_):
    idx = sources.load_index(refresh=True, progress=lambda d, n: print(f"\r{d}/{n} databases", end="", flush=True))
    print(f"\n{len(idx['entries'])} measured models indexed")


def cmd_phone(args):
    st = state.load()
    link = daemon.usb_phone_link(st["port"])
    print(link["message"])
    if not link["ok"]:
        sys.exit(1)
    apk = Path(__file__).resolve().parent.parent / "android/app/build/outputs/apk/debug/app-debug.apk"
    if args.install:
        if not apk.exists():
            sys.exit(f"APK not built yet: {apk}")
        subprocess.run(["adb", "install", "-r", str(apk)], check=True)
    subprocess.run(["adb", "shell", "am", "start", "-n", "dev.diamondeq/.MainActivity"], capture_output=True)
    print("phone app opened; it syncs profiles from this PC")


def cmd_open(_):
    url = f"http://127.0.0.1:{state.load()['port']}"
    print(url)
    if shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="diamondeq", description="Equalize headphones to the PEQdB Diamond target.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the background service").set_defaults(fn=lambda _: daemon.serve())
    sub.add_parser("status", help="outputs and their EQ").set_defaults(fn=cmd_status)
    sub.add_parser("open", help="open the web UI").set_defaults(fn=cmd_open)
    p = sub.add_parser("search", help="find a measured model")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("add", help="create an EQ profile for a model")
    p.add_argument("query", nargs="+")
    p.add_argument("--site", help="use the measurement of this reviewer site")
    p.add_argument("--all", action="store_true", help="average every available measurement")
    p.add_argument("--bands", type=int, default=10)
    p.add_argument("--max-loss", type=float, default=9.0, help="limit the loudness drop in dB (default 9)")
    p.add_argument("--exact", action="store_true", help="always match the target exactly, whatever it costs")
    p.add_argument("--output", help="assign to this output (name fragment, or 'default')")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("assign", help="choose the profile of an output")
    p.add_argument("output")
    p.add_argument("profile", help="profile name fragment, or 'none'")
    p.set_defaults(fn=cmd_assign)
    sub.add_parser("on", help="enable EQ").set_defaults(fn=lambda _: state.update(enabled=True))
    sub.add_parser("off", help="disable EQ").set_defaults(fn=lambda _: state.update(enabled=False))
    sub.add_parser("profiles", help="list profiles").set_defaults(fn=cmd_profiles)
    p = sub.add_parser("export", help="print a profile for other equalizers")
    p.add_argument("id")
    p.add_argument("--format", choices=["apo", "graphiceq", "json"], default="apo")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("import", help="use an existing parametric EQ file (e.g. exported from peqdb.com)")
    p.add_argument("name")
    p.add_argument("file")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("selftest", help="measure the EQ PipeWire applies against the design")
    p.add_argument("id", nargs="?")
    p.set_defaults(fn=cmd_selftest)
    sub.add_parser("refresh", help="rebuild the list of measured models").set_defaults(fn=cmd_refresh)
    p = sub.add_parser("phone", help="link the phone over USB and sync it")
    p.add_argument("--install", action="store_true", help="also install the Android app")
    p.set_defaults(fn=cmd_phone)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
