import datetime as dt
import io
import json
import sys
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_lexio as cl  # noqa: E402

NOW = dt.datetime(2026, 11, 12, 18, 0, tzinfo=dt.timezone.utc)


class Fake(cl.Lexio):
    def __init__(self, routes):
        super().__init__("lck_test")
        self.routes, self.calls = routes, []

    def get(self, path, params=None):
        self.calls.append((path, {k: v for k, v in (params or {}).items() if v is not None}))
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        return value(params) if callable(value) else value


def run(argv, routes):
    out = io.StringIO()
    client = Fake(routes)
    with redirect_stdout(out):
        code = cl.main(argv, client=client, now=NOW)
    return code, out.getvalue(), client


def test_api_ok():
    code, out, _ = run(["-m", "api"], {"/organization": {"data": {"name": "Demo"}}})
    assert code == 0 and out.startswith("LEXIO OK - Lexio API answers for Demo") and "'time'=" in out


def test_auth_error_is_critical():
    code, out, _ = run(["-m", "api"], {"/organization": cl.ApiError(401, "API_KEY_INVALID", "unknown key", "rq1")})
    assert code == 2 and "API_KEY_INVALID" in out and "rq1" in out


def test_server_error_is_unknown():
    code, _, _ = run(["-m", "api"], {"/organization": cl.ApiError(500, "INTERNAL", "boom", None)})
    assert code == 3


def test_unreachable_is_critical():
    code, out, _ = run(["-m", "api"], {"/organization": urllib.error.URLError("no route")})
    assert code == 2 and "unreachable" in out


VEHICLES = {"data": [{"id": "v1", "plate": "AB-123-CD", "name": "Clio"}, {"id": "v2", "plate": "EF-456-GH", "name": "Jumpy"}], "nextCursor": None}


def test_maintenance_overdue_and_blocking():
    routes = {
        "/vehicles": VEHICLES,
        "/vehicles/v1/maintenance-alerts": {"data": [{"operationCode": "OIL", "status": "OVERDUE", "isBlockingRentals": True, "dueDate": "2026-11-01"}]},
        "/vehicles/v2/maintenance-alerts": {"data": [{"operationCode": "CT_INSPECTION", "status": "UPCOMING", "remainingKm": 400}]},
    }
    code, out, client = run(["-m", "maintenance"], routes)
    assert code == 2  # blocking rentals is always critical
    assert "1 overdue services (1 blocking rentals), 1 upcoming" in out
    assert "OVERDUE AB-123-CD Clio: OIL due 2026-11-01" in out
    assert "'overdue'=1;1;3;0;" in out
    assert ("/vehicles", {"isArchived": False, "limit": 100}) in client.calls


def test_maintenance_all_ok():
    routes = {"/vehicles": VEHICLES, "/vehicles/v1/maintenance-alerts": {"data": []}, "/vehicles/v2/maintenance-alerts": {"data": []}}
    code, out, _ = run(["-m", "maintenance"], routes)
    assert code == 0 and "0 overdue services" in out


def test_maintenance_warn_upcoming():
    routes = {"/vehicles": VEHICLES, "/vehicles/v1/maintenance-alerts": {"data": [{"operationCode": "TYRES", "status": "UPCOMING"}]}, "/vehicles/v2/maintenance-alerts": {"data": []}}
    assert run(["-m", "maintenance"], routes)[0] == 0
    assert run(["-m", "maintenance", "--warn-upcoming"], routes)[0] == 1


def rentals(*items):
    return {"data": list(items), "nextCursor": None}


def rental(number, end, status="IN_PROGRESS", start="2026-11-10T09:00:00+01:00"):
    return {"number": number, "status": status, "period": {"startAt": start, "endAt": end}, "vehicle": {"plate": "AB-123-CD", "name": "Clio"}}


def test_late_returns_with_grace():
    routes = {"/rentals": rentals(
        rental("LOC-1", "2026-11-12T17:00:00+01:00"),  # 16:00 UTC, 2 h late
        rental("LOC-2", "2026-11-12T18:45:00+01:00"),  # 17:45 UTC, 15 min: inside grace
        rental("LOC-3", "2026-11-13T09:00:00+01:00"),  # tomorrow
    )}
    code, out, client = run(["-m", "late-returns"], routes)
    assert code == 1
    assert "1 rental(s) with return late by more than 30 min" in out
    assert "LOC-1 AB-123-CD Clio: return due 2026-11-12T17:00:00+01:00 (120 min late)" in out
    path, params = client.calls[0]
    assert params["status"] == "IN_PROGRESS" and params["to"] == "2026-11-12"


def test_missed_pickups_critical_threshold():
    late = [rental(f"LOC-{i}", "2026-11-20T09:00:00+01:00", "RESERVED", "2026-11-12T10:00:00+01:00") for i in range(3)]
    code, out, client = run(["-m", "missed-pickups", "-w", "1", "-c", "3"], {"/rentals": rentals(*late)})
    assert code == 2 and "3 rental(s) with pick-up late" in out
    assert client.calls[0][1]["status"] == "RESERVED"


def test_pagination_follows_cursor():
    pages = {None: {"data": [{"id": i} for i in range(100)], "nextCursor": "c2"}, "c2": {"data": [{"id": 100}], "nextCursor": "c3"}}
    client = Fake({"/vehicles": lambda params: pages[params.get("cursor")]})
    assert len(client.list_all("/vehicles")) == 101


def test_missing_key_is_unknown(monkeypatch):
    monkeypatch.delenv("LEXIO_API_KEY", raising=False)
    out = io.StringIO()
    with redirect_stdout(out):
        assert cl.main(["-m", "api"]) == 3
    assert "no API key" in out.getvalue()


def test_maintenance_skips_untracked_vehicles():
    routes = {"/vehicles": VEHICLES,
              "/vehicles/v1/maintenance-alerts": cl.ApiError(409, "MAINTENANCE_TRACKING_NOT_STARTED", "x", None),
              "/vehicles/v2/maintenance-alerts": {"data": []}}
    code, out, _ = run(["-m", "maintenance"], routes)
    assert code == 0 and "1 without maintenance tracking" in out and "NOT TRACKED AB-123-CD Clio" in out


def test_maintenance_nothing_tracked_is_unknown():
    err = cl.ApiError(409, "MAINTENANCE_TRACKING_NOT_STARTED", "x", None)
    routes = {"/vehicles": VEHICLES, "/vehicles/v1/maintenance-alerts": err, "/vehicles/v2/maintenance-alerts": err}
    code, out, _ = run(["-m", "maintenance"], routes)
    assert code == 3 and "not started on any of the 2 vehicles" in out
