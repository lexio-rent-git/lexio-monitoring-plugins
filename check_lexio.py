#!/usr/bin/env python3
# check_lexio - Nagios / Icinga / Naemon / Checkmk plugin for the Lexio Rent API
# Copyright (C) 2026 Lexio Rent (https://lexio.rent)
#
# This program is free software; you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version. See the LICENSE file.
"""Monitor a car rental agency run on Lexio Rent through its public API (v1).

Modes:
  api             API reachable with this key; response time.
  maintenance     Fleet maintenance: overdue and upcoming services, vehicles blocked for rental.
  late-returns    Rentals in progress whose return time has passed.
  missed-pickups  Reservations whose pick-up time has passed without a pick-up.

Exit codes follow the monitoring plugins guidelines: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
The API key (lck_...) is read from --api-key-file or the LEXIO_API_KEY environment variable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "0.1.0"
DEFAULT_BASE_URL = "https://api.lexio.rent/api/external/v1"
USER_AGENT = f"check_lexio/{__version__} (+https://github.com/lexio-rent-git/lexio-monitoring-plugins)"
OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3
STATE = {OK: "OK", WARNING: "WARNING", CRITICAL: "CRITICAL", UNKNOWN: "UNKNOWN"}


class ApiError(Exception):
    def __init__(self, status: int, code: str | None, detail: str | None, request_id: str | None):
        super().__init__(f"HTTP {status} {code or ''} {detail or ''}".strip())
        self.status, self.code, self.detail, self.request_id = status, code, detail, request_id


class Lexio:
    """Minimal read-only client (standard library only)."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 20.0, opener=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener()

    def get(self, path: str, params: dict | None = None) -> dict:
        query = {k: ("true" if v is True else "false" if v is False else v) for k, v in (params or {}).items() if v is not None}
        url = self.base_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                problem = json.loads(e.read() or b"{}")
            except ValueError:
                problem = {}
            raise ApiError(e.code, problem.get("code"), problem.get("detail"), problem.get("requestId")) from None

    def list_all(self, path: str, params: dict | None = None, limit: int = 100) -> list:
        items, cursor = [], None
        while True:
            page = self.get(path, {**(params or {}), "limit": limit, "cursor": cursor})
            data = page.get("data") or []
            items.extend(data)
            nxt = page.get("nextCursor")
            if not nxt or len(data) < limit or nxt == cursor:
                return items
            cursor = nxt


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def status_for(value: float, warning: float | None, critical: float | None) -> int:
    if critical is not None and value >= critical:
        return CRITICAL
    if warning is not None and value >= warning:
        return WARNING
    return OK


def perf(label: str, value, warning=None, critical=None, uom: str = "") -> str:
    w = "" if warning is None else warning
    c = "" if critical is None else critical
    return f"'{label}'={value}{uom};{w};{c};0;"


def label_vehicle(v: dict | None) -> str:
    v = v or {}
    return " ".join(str(x) for x in (v.get("plate"), v.get("name")) if x) or str(v.get("id", "?"))


# --- modes -------------------------------------------------------------------

def check_api(client: Lexio, args) -> tuple[int, str, list[str], list[str]]:
    start = time.monotonic()
    org = client.get("/organization").get("data") or {}
    elapsed = time.monotonic() - start
    state = status_for(elapsed, args.warning, args.critical)
    return state, f"Lexio API answers for {org.get('name', 'the organization')} in {elapsed:.2f}s", [], [perf("time", f"{elapsed:.3f}", args.warning, args.critical, "s")]


def check_maintenance(client: Lexio, args) -> tuple[int, str, list[str], list[str]]:
    vehicles = client.list_all("/vehicles", {"isArchived": False})
    overdue, upcoming, blocking, untracked = [], [], [], []
    for v in vehicles:
        try:
            alerts = client.get(f"/vehicles/{urllib.parse.quote(str(v['id']), safe='')}/maintenance-alerts", {"status": "OVERDUE,UPCOMING"}).get("data") or []
        except ApiError as e:
            if e.status == 409 and e.code == "MAINTENANCE_TRACKING_NOT_STARTED":
                untracked.append(label_vehicle(v))
                continue
            raise
        for a in alerts:
            line = f"{label_vehicle(v)}: {a.get('operationCode')}"
            if a.get("dueDate"):
                line += f" due {a['dueDate']}"
            km = a.get("remainingKm")
            if km is not None:
                line += f" ({-km} km over)" if km < 0 else f" ({km} km left)"
            if a.get("status") == "OVERDUE":
                overdue.append(line)
                if a.get("isBlockingRentals"):
                    blocking.append(line)
            else:
                upcoming.append(line)
    state = status_for(len(overdue), args.warning, args.critical)
    if blocking and state < CRITICAL:
        state = CRITICAL
    if upcoming and state == OK and args.warn_upcoming:
        state = WARNING
    if untracked and len(untracked) == len(vehicles):
        return UNKNOWN, f"maintenance tracking is not started on any of the {len(vehicles)} vehicles (start it in Lexio)", [], [perf("untracked", len(untracked))]
    summary = f"{len(vehicles)} vehicles, {len(overdue)} overdue services ({len(blocking)} blocking rentals), {len(upcoming)} upcoming"
    if untracked:
        summary += f", {len(untracked)} without maintenance tracking"
    details = [f"OVERDUE {x}" for x in overdue] + [f"UPCOMING {x}" for x in upcoming] + [f"NOT TRACKED {x}" for x in untracked]
    return state, summary, details, [
        perf("overdue", len(overdue), args.warning, args.critical),
        perf("blocking", len(blocking)),
        perf("upcoming", len(upcoming)),
        perf("vehicles", len(vehicles)),
        perf("untracked", len(untracked)),
    ]


def _late(client: Lexio, args, status: str, field: str, what: str) -> tuple[int, str, list[str], list[str]]:
    now = args.now or dt.datetime.now(dt.timezone.utc)
    grace = dt.timedelta(minutes=args.grace)
    window_from = (now - dt.timedelta(days=args.days)).date().isoformat()
    rentals = client.list_all("/rentals", {"status": status, "from": window_from, "to": now.date().isoformat()})
    late = []
    for r in rentals:
        due = parse_time((r.get("period") or {}).get(field))
        if due and due.tzinfo and due + grace < now:
            minutes = int((now - due).total_seconds() // 60)
            late.append((minutes, f"{r.get('number')} {label_vehicle(r.get('vehicle'))}: {what} due {due.isoformat()} ({minutes} min late)"))
    late.sort(reverse=True)
    state = status_for(len(late), args.warning, args.critical)
    summary = f"{len(late)} rental(s) with {what} late by more than {args.grace} min"
    return state, summary, [line for _, line in late], [perf("late", len(late), args.warning, args.critical)]


def check_late_returns(client: Lexio, args):
    return _late(client, args, "IN_PROGRESS", "endAt", "return")


def check_missed_pickups(client: Lexio, args):
    return _late(client, args, "RESERVED", "startAt", "pick-up")


MODES = {
    "api": (check_api, 2.0, 5.0),
    "maintenance": (check_maintenance, 1, 3),
    "late-returns": (check_late_returns, 1, 3),
    "missed-pickups": (check_missed_pickups, 1, 3),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="check_lexio", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-m", "--mode", choices=sorted(MODES), required=True)
    p.add_argument("-k", "--api-key-file", help="file containing the lck_... key (default: LEXIO_API_KEY)")
    p.add_argument("-u", "--base-url", default=os.environ.get("LEXIO_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("-w", "--warning", type=float, help="warning threshold (count, or seconds for mode api)")
    p.add_argument("-c", "--critical", type=float, help="critical threshold (count, or seconds for mode api)")
    p.add_argument("-g", "--grace", type=int, default=30, help="minutes of tolerance before a return or pick-up is late (default 30)")
    p.add_argument("-d", "--days", type=int, default=60, help="how many days back to look for rentals (default 60)")
    p.add_argument("--warn-upcoming", action="store_true", help="maintenance: WARNING when a service is due soon")
    p.add_argument("-t", "--timeout", type=float, default=20.0)
    p.add_argument("-V", "--version", action="version", version=f"check_lexio {__version__}")
    return p


def main(argv=None, client: Lexio | None = None, now: dt.datetime | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.now = now
    fn, default_w, default_c = MODES[args.mode]
    if args.warning is None:
        args.warning = default_w
    if args.critical is None:
        args.critical = default_c
    try:
        if client is None:
            key = os.environ.get("LEXIO_API_KEY", "")
            if args.api_key_file:
                with open(args.api_key_file, encoding="utf-8") as fh:
                    key = fh.read().strip()
            if not key:
                print("LEXIO UNKNOWN - no API key: use --api-key-file or LEXIO_API_KEY")
                return UNKNOWN
            client = Lexio(key, args.base_url, args.timeout)
        state, summary, details, perfdata = fn(client, args)
    except ApiError as e:
        state = CRITICAL if e.status in (401, 403) else UNKNOWN
        print(f"LEXIO {STATE[state]} - API error {e.status} {e.code or ''}: {e.detail or ''} (requestId {e.request_id})".rstrip())
        return state
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"LEXIO CRITICAL - API unreachable: {e}")
        return CRITICAL
    print(f"LEXIO {STATE[state]} - {summary} | {' '.join(perfdata)}")
    for line in details[:50]:
        print(line)
    return state


if __name__ == "__main__":
    sys.exit(main())
