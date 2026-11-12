# check_lexio: monitoring plugin for Lexio Rent

`check_lexio` is a Nagios-compatible plugin (Nagios, Icinga, Naemon, Shinken, Checkmk, Zabbix with external checks) that watches a car or van rental agency run on [Lexio Rent](https://docs.lexio.rent), through the public Lexio Rent API v1.

<a href="https://lexio.rent/logiciel-location-voiture">Lexio Rent</a> is rental management software for professional car, van and two-wheeler rental companies. This plugin turns its operational data into alerts your monitoring already knows how to route: a van is overdue for its service, a customer has not brought a car back, a reservation was never picked up.

Single file, Python 3.9+, **no dependency** beyond the standard library. Read-only.

## Modes

| Mode | What it checks | Default thresholds (`-w` / `-c`) |
|---|---|---|
| `api` | The API answers with this key; response time | 2 s / 5 s |
| `maintenance` | Overdue and upcoming services on active vehicles (oil, tyres, technical inspection...). Any overdue service that **blocks rentals** is CRITICAL. | 1 / 3 overdue services |
| `late-returns` | Rentals in progress whose return time has passed by more than `--grace` minutes | 1 / 3 rentals |
| `missed-pickups` | Reservations whose pick-up time has passed by more than `--grace` minutes without a pick-up | 1 / 3 rentals |

Output follows the [Monitoring Plugins guidelines](https://www.monitoring-plugins.org/doc/guidelines.html): one status line with performance data, then one line per vehicle or rental, and exit codes 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.

```text
$ check_lexio -m maintenance -k /etc/lexio/api-key
LEXIO CRITICAL - 10 vehicles, 13 overdue services (0 blocking rentals), 0 upcoming | 'overdue'=13;1;3;0; 'blocking'=0;;;0; 'upcoming'=0;;;0; 'vehicles'=10;;;0; 'untracked'=0;;;0;
OVERDUE DM-401-LX Master 12 m³: OIL_CHANGE due 2023-03-15 (41800 km over)
...

$ check_lexio -m missed-pickups -k /etc/lexio/api-key --grace 30
LEXIO WARNING - 2 rental(s) with pick-up late by more than 30 min | 'late'=2;1;3;0;
LOC-2026.11-0003 DM-401-LX Master 12 m³: pick-up due 2026-11-11T08:00:00+01:00 (2383 min late)
```

## Requirements

- A Lexio Rent organization on a plan that includes the API (Business plan).
- An API key: in Lexio, **Settings → Developer → API tokens**. Keys start with `lck_`. The plugin only reads, so give it read scopes: `vehicles.read`, `maintenance.read`, `rentals.read`. See [Authentication](https://docs.lexio.rent/authentification).
- For `maintenance`, maintenance tracking must be started on the vehicles in Lexio; vehicles without tracking are listed as `NOT TRACKED`.

## Installation

```sh
sudo install -m 755 check_lexio.py /usr/lib/nagios/plugins/check_lexio
sudo install -m 600 -o nagios /dev/null /etc/lexio/api-key && echo 'lck_...' | sudo tee /etc/lexio/api-key
```

### Nagios / Icinga 1 / Naemon

```cfg
define command {
    command_name    check_lexio
    command_line    $USER1$/check_lexio -k /etc/lexio/api-key -m $ARG1$ $ARG2$
}

define service {
    use                 generic-service
    host_name           lexio-api
    service_description Lexio maintenance
    check_command       check_lexio!maintenance!-w 1 -c 3
    check_interval      60
}

define service {
    use                 generic-service
    host_name           lexio-api
    service_description Lexio late returns
    check_command       check_lexio!late-returns!--grace 30
    check_interval      15
}
```

### Icinga 2

```icinga
object CheckCommand "lexio" {
  command = [ PluginDir + "/check_lexio" ]
  arguments = {
    "-m" = "$lexio_mode$"
    "-k" = "$lexio_key_file$"
    "-w" = "$lexio_warning$"
    "-c" = "$lexio_critical$"
    "--grace" = "$lexio_grace$"
  }
  vars.lexio_key_file = "/etc/icinga2/lexio-api-key"
}
```

### Checkmk

Install the MKP from the [Checkmk Exchange](https://exchange.checkmk.com) (or `mkp add lexio-*.mkp && mkp enable lexio`), then create a rule **Setup → Services → Other services → Integrate Nagios plugins** with the command line `$USER2$/check_lexio -k /omd/sites/<site>/etc/lexio-api-key -m maintenance`.

## Options

```text
-m, --mode {api,maintenance,late-returns,missed-pickups}
-k, --api-key-file FILE   file containing the lck_... key (default: LEXIO_API_KEY)
-u, --base-url URL        API base URL (default https://api.lexio.rent/api/external/v1)
-w, --warning N           warning threshold (count, or seconds for mode api)
-c, --critical N          critical threshold
-g, --grace MIN           tolerance before a return or pick-up is late (default 30)
-d, --days N              how many days back to look for rentals (default 60)
    --warn-upcoming       maintenance: WARNING when a service is due soon
-t, --timeout SEC         HTTP timeout (default 20)
```

## API usage

`maintenance` makes one call per active vehicle, so run it hourly rather than every minute on a large fleet. The other modes make one call per page of 100 rentals. Lexio rate limits are per key, see [Rate limits](https://docs.lexio.rent/limites).

## Development

```sh
python -m pytest tests   # no network, no API key needed
```

## About

`check_lexio` is maintained by the team behind <a href="https://lexio.rent/logiciel-location-voiture">Lexio Rent, car rental management software</a>. The `CT_INSPECTION` line of the maintenance check is the French roadworthiness test; to budget it across a fleet, Lexio publishes a free <a href="https://lexio.rent/outils/prix-controle-technique">technical inspection price comparator</a> (in French).

- API documentation: <https://docs.lexio.rent>
- Python client: <https://github.com/lexio-rent-git/lexio-python>
- Contact: contact@lexio.rent

License: GNU GPL v2 or later.
