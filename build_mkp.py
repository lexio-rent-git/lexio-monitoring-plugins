"""Build the Checkmk extension package (MKP) for check_lexio.

Usage: python3 build_mkp.py  ->  dist/lexio-<version>.mkp
The MKP installs check_lexio under ~/local/lib/nagios/plugins of the site; use it with the
"Integrate Nagios plugins" rule ($USER2$/check_lexio ...).
"""
import io, json, pathlib, re, tarfile, time

ROOT = pathlib.Path(__file__).resolve().parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "check_lexio.py").read_text()).group(1)
info = {
    "title": "Lexio Rent: rental fleet and bookings monitoring (check_lexio)",
    "name": "lexio",
    "description": (
        "Active check for car and van rental agencies run on Lexio Rent "
        "(https://lexio.rent/logiciel-location-voiture), through the Lexio Rent API v1 "
        "(https://docs.lexio.rent). Modes: api (reachability, response time), maintenance "
        "(overdue and upcoming services, vehicles blocked for rental), late-returns and missed-pickups. "
        "Installs check_lexio in local/lib/nagios/plugins: create an 'Integrate Nagios plugins' rule with "
        "$USER2$/check_lexio -k <key file> -m maintenance. Requires a Lexio API key (lck_..., Business plan). "
        "Read-only, no dependency beyond Python 3."
    ),
    "version": VERSION,
    "version.packaged": "2.3.0",
    "version.min_required": "2.2.0",
    "version.usable_until": None,
    "author": "Lexio Rent <contact@lexio.rent>",
    "download_url": "https://github.com/lexio-rent-git/lexio-monitoring-plugins",
    "files": {"lib": ["nagios/plugins/check_lexio"]},
}


def add(tar, name, data, mode=0o644):
    ti = tarfile.TarInfo(name)
    ti.size, ti.mode, ti.mtime = len(data), mode, int(time.time())
    tar.addfile(ti, io.BytesIO(data))


inner = io.BytesIO()
with tarfile.open(fileobj=inner, mode="w") as t:
    add(t, "nagios/plugins/check_lexio", (ROOT / "check_lexio.py").read_bytes(), 0o755)
out = ROOT / "dist" / f"lexio-{VERSION}.mkp"
out.parent.mkdir(exist_ok=True)
with tarfile.open(out, mode="w:gz") as t:
    add(t, "info", repr(info).encode())
    add(t, "info.json", json.dumps(info).encode())
    add(t, "lib.tar", inner.getvalue())
print(out)
