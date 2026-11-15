"""Background service: keeps each output's EQ filter running and serves the
local web UI plus the phone sync endpoint."""
from __future__ import annotations

import ipaddress
import json
import mimetypes
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from . import pipewire, profile, selftest, sources, state


class Engine:
    """Reconciles running filters with the assignments in the state file."""

    def __init__(self):
        self.filters: dict[str, pipewire.FilterProcess] = {}  # output key -> process
        self.errors: dict[str, str] = {}
        self.sinks: list[dict] = []
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self._state_mtime = 0.0

    def reconcile(self) -> None:
        with self.lock:
            st = state.load()
            self._state_mtime = state.mtime()
            self.sinks = pipewire.list_sinks()
            seen = {s["key"]: s["label"] for s in self.sinks if st["labels"].get(s["key"]) != s["label"]}
            if seen:
                state.update(labels=seen)
            wanted: dict[str, tuple] = {}
            if st["enabled"]:
                for s in self.sinks:
                    prof = profile.load(st["assignments"].get(s["key"], "")) if s["key"] in st["assignments"] else None
                    if prof:
                        wanted[s["key"]] = (s["node"], prof)
            for key in list(self.filters):
                proc = self.filters[key]
                keep = key in wanted and proc.alive() and proc.sink_node == wanted[key][0] and \
                    proc.signature == _signature(wanted[key][1])
                if not keep:
                    if not proc.alive():
                        self.errors[key] = proc.error() or "filter stopped unexpectedly"
                    proc.stop()
                    del self.filters[key]
            for key, (node, prof) in wanted.items():
                if key not in self.filters:
                    self.filters[key] = pipewire.FilterProcess(node, prof["name"], profile.filters_of(prof),
                                                               prof["preamp_db"])
                    self.errors.pop(key, None)

    def run(self) -> None:
        threading.Thread(target=self._watch_pipewire, daemon=True).start()
        while True:
            try:
                self.reconcile()
            except Exception as e:  # keep the service alive; the next round retries
                print(f"diamondeq: reconcile failed: {e!r}", flush=True)
            # wake on PipeWire events or API calls; poll slowly for edits to the state file
            self.wake.wait(timeout=3.0)
            self.wake.clear()
            time.sleep(0.3)  # let a burst of device events settle

    def _watch_pipewire(self) -> None:
        while True:
            try:
                p = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True)
                for line in p.stdout:
                    if "sink" in line and "sink-input" not in line or "server" in line:
                        self.wake.set()
            except OSError:
                pass
            time.sleep(2.0)

    def stop_all(self) -> None:
        with self.lock:
            for proc in self.filters.values():
                proc.stop()
            self.filters.clear()

    def snapshot(self) -> dict:
        st = state.load()
        online = {s["key"] for s in self.sinks}
        outputs = [dict(s, profile=st["assignments"].get(s["key"]), online=True,
                        active=s["key"] in self.filters and self.filters[s["key"]].alive(),
                        error=self.errors.get(s["key"], "")) for s in self.sinks]
        for key, pid in st["assignments"].items():
            if key not in online:
                outputs.append({"key": key, "label": st["labels"].get(key, key), "online": False, "active": False,
                                "kind": "bluetooth" if key.startswith("bt:") else "builtin", "profile": pid,
                                "default": False, "available": False, "error": ""})
        age = sources.index_age()
        return {
            "enabled": st["enabled"],
            "outputs": outputs,
            "profiles": [profile.summary(p) for p in profile.load_all()],
            "index": {"age_days": None if age is None else round(age / 86400, 1),
                      "models": len(sources.load_index()["entries"]) if age is not None else 0},
            "phone": {"port": st["port"], "lan": st["lan"], "addresses": lan_addresses()},
        }


def _signature(prof: dict) -> str:
    return pipewire.signature(prof["name"], profile.filters_of(prof), prof["preamp_db"])


def lan_addresses() -> list[str]:
    # Only the address used for outbound traffic: virtual bridges (Docker etc.)
    # are unreachable from the phone. Connecting a UDP socket sends no packet.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
            return [] if ip.startswith("127.") else [ip]
    except OSError:
        return []


def usb_phone_link(port: int) -> dict:
    """Forward the phone's localhost:<port> to this PC over USB (adb reverse)."""
    if not shutil.which("adb"):
        return {"ok": False, "message": "adb is not installed"}
    try:
        devs = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=15).stdout.splitlines()[1:]
        ready = [d.split()[0] for d in devs if d.strip().endswith("device")]
        if not ready:
            waiting = any("unauthorized" in d for d in devs)
            return {"ok": False, "message": "allow USB debugging on the phone" if waiting else "no phone connected"}
        for serial in ready:
            subprocess.run(["adb", "-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"],
                           capture_output=True, timeout=15, check=True)
        return {"ok": True, "message": f"linked {len(ready)} phone(s) over USB", "devices": ready}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "message": f"adb failed: {e}"}


class Handler(BaseHTTPRequestHandler):
    engine: Engine
    server_version = "diamondeq"

    def log_message(self, *args):  # quiet
        pass

    # -- helpers ---------------------------------------------------------
    def _local(self) -> bool:
        return ipaddress.ip_address(self.client_address[0]).is_loopback

    def _host_ok(self) -> bool:
        # Reject DNS-rebinding: a browser page may only reach us under a local name.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode())

    def _fail(self, code: int, message: str):
        self._json({"error": message}, code)

    def _phone_sync(self, query: dict):
        st = state.load()
        if not self._local():
            if not st["lan"] or query.get("token", [""])[0] != st["token"]:
                return self._fail(403, "forbidden")
        bt = {k: v for k, v in st["assignments"].items() if k.startswith("bt:")}
        self._json({
            "server": {"name": socket.gethostname(), "port": st["port"], "token": st["token"],
                       "addresses": lan_addresses() if st["lan"] else []},
            "profiles": [profile.summary(p) for p in profile.load_all()],
            "assignments": bt,
            "labels": {k: st["labels"].get(k, "") for k in bt},
        })

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/api/phone/sync":
            return self._phone_sync(query)
        if not self._local() or not self._host_ok():
            return self._fail(403, "forbidden")
        try:
            if url.path == "/api/state":
                return self._json(self.engine.snapshot())
            if url.path == "/api/search":
                return self._json(sources.search(query.get("q", [""])[0]))
            if url.path == "/api/profile":
                prof = profile.load(query.get("id", [""])[0])
                return self._json(prof) if prof else self._fail(404, "unknown profile")
            if url.path == "/api/profile/export":
                prof = profile.load(query.get("id", [""])[0])
                if not prof:
                    return self._fail(404, "unknown profile")
                fmt = query.get("format", ["apo"])[0]
                text, ext = {"apo": (profile.to_apo(prof), "txt"), "graphiceq": (profile.to_graphic_eq(prof), "txt"),
                             "json": (json.dumps(profile.summary(prof), indent=2), "json")}.get(fmt, (None, None))
                if text is None:
                    return self._fail(400, "unknown format")
                return self._send(200, text.encode(), "text/plain; charset=utf-8", {
                    "Content-Disposition": f'attachment; filename="{prof["id"]}-{fmt}.{ext}"'})
            return self._static(url.path)
        except Exception as e:
            self._fail(500, f"{type(e).__name__}: {e}")

    def _static(self, path: str):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        if "/" in name or ".." in name:
            return self._fail(404, "not found")
        res = resources.files("diamondeq").joinpath("web", name)
        if not res.is_file():
            return self._fail(404, "not found")
        self._send(200, res.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream")

    def do_POST(self):
        # JSON-only POSTs cannot be sent cross-site without a CORS preflight,
        # which we never answer; together with the Host check this blocks CSRF.
        if not self._local() or not self._host_ok() or \
                not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._fail(403, "forbidden")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            result = self._post(urllib.parse.urlparse(self.path).path, body)
        except (LookupError, ValueError) as e:
            return self._fail(400, str(e))
        except Exception as e:
            return self._fail(500, f"{type(e).__name__}: {e}")
        self.engine.wake.set()
        self._json(result)

    def _post(self, path: str, body: dict) -> dict:
        if path == "/api/enabled":
            state.update(enabled=bool(body["enabled"]))
        elif path == "/api/assign":
            pid = body.get("profile")
            if pid and not profile.load(pid):
                raise LookupError("unknown profile")
            state.update(assignments={body["output"]: pid or None})
        elif path == "/api/profile/create":
            prof = profile.create(body["name"], body.get("kind", "IEMs"), body["measurements"], body.get("options"))
            profile.save(prof)
            if body.get("output"):
                state.update(assignments={body["output"]: prof["id"]})
            return prof
        elif path == "/api/profile/refit":
            old = profile.load(body["id"])
            if not old or not old["sources"]:
                raise LookupError("profile has no measurement to refit")
            prof = profile.create(old["name"], old["kind"], old["sources"], body.get("options"))
            profile.save(prof)
            return prof
        elif path == "/api/profile/import":
            prof = profile.from_apo(body["name"], body["text"])
            profile.save(prof)
            return prof
        elif path == "/api/profile/delete":
            st = state.load()
            state.update(assignments={k: None for k, v in st["assignments"].items() if v == body["id"]})
            profile.delete(body["id"])
        elif path == "/api/index/refresh":
            return {"models": len(sources.load_index(refresh=True)["entries"])}
        elif path == "/api/selftest":
            prof = profile.load(body["id"])
            if not prof:
                raise LookupError("unknown profile")
            return selftest.run(profile.filters_of(prof), prof["preamp_db"])
        elif path == "/api/phone/usb":
            return usb_phone_link(state.load()["port"])
        else:
            raise LookupError("unknown endpoint")
        return {"ok": True}


def serve() -> None:
    st = state.load()
    engine = Engine()
    Handler.engine = engine
    server = ThreadingHTTPServer(("0.0.0.0" if st["lan"] else "127.0.0.1", st["port"]), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_keep_usb_link, args=(st["port"],), daemon=True).start()
    print(f"diamondeq: http://127.0.0.1:{st['port']}", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # run the cleanup below on `systemctl stop`
    try:
        engine.run()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop_all()
        server.shutdown()


def _keep_usb_link(port: int) -> None:
    """Re-create the USB link whenever a phone is plugged in."""
    while True:
        usb_phone_link(port)
        time.sleep(20.0)
