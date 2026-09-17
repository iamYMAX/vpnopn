#!/usr/bin/env python3

from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import urllib.request
from pathlib import Path

API_URL = "https://www.vpngate.net/api/iphone/"
OUTPUT_DIR = Path("configs")
MANIFEST = Path("servers.json")
MAX_SERVERS = int(os.getenv("MAX_SERVERS", "20"))
EXCLUDED_COUNTRIES = {"RU"}


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return value.strip("._") or "server"


def number(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main() -> None:
    request = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "vpnopn-github-actions/1.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8-sig", errors="replace")

    raw_lines = [line for line in raw.splitlines() if line.strip()]\r\n\r\n    # VPN Gate CSV keeps its header as a comment beginning with #HostName.\r\n    header_index = next((i for i, line in enumerate(raw_lines) if line.lstrip().startswith("#HostName,")), None)\r\n    if header_index is None:\r\n        raise RuntimeError("VPN Gate API returned no CSV header")\r\n\r\n    header_line = raw_lines[header_index].lstrip()[1:]\r\n    data_lines = [line for line in raw_lines[header_index + 1:] if not line.lstrip().startswith("#")]\r\n\r\n    rows = list(csv.reader(io.StringIO("\n".join([header_line, *data_lines]))))\r\n    header = rows[0]

    records = [
        dict(zip(header, row))
        for row in rows[1:]
        if len(row) >= len(header)
    ]

    candidates = []

    for row in records:
        country = row.get("CountryShort", "").upper()
        config_b64 = row.get("OpenVPN_ConfigData_Base64", "").strip()

        if not config_b64 or country in EXCLUDED_COUNTRIES:
            continue

        try:
            config = base64.b64decode(config_b64, validate=True)
            config_text = config.decode("utf-8")
        except Exception:
            continue

        if "client" not in config_text or "remote " not in config_text:
            continue

        candidates.append(
            {
                "hostname": row.get("HostName", ""),
                "ip": row.get("IP", ""),
                "country": row.get("CountryLong", ""),
                "country_code": country,
                "score": number(row.get("Score")),
                "ping_ms": number(row.get("Ping"), -1),
                "speed": number(row.get("Speed")),
                "sessions": int(number(row.get("NumVpnSessions"), 0)),
                "config": config_text,
            }
        )

    candidates.sort(
        key=lambda server: (
            -server["score"],
            -server["speed"],
            server["ping_ms"] if server["ping_ms"] >= 0 else 10**9,
        )
    )

    selected = candidates[:MAX_SERVERS]

    if not selected:
        raise RuntimeError("No usable non-RU OpenVPN configurations found")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for old_file in OUTPUT_DIR.glob("*.ovpn"):
        old_file.unlink()

    manifest = []

    for server in selected:
        filename = f"{safe_name(server['hostname'])}.ovpn"

        (OUTPUT_DIR / filename).write_text(
            server["config"],
            encoding="utf-8",
        )

        manifest.append(
            {
                key: server[key]
                for key in server
                if key != "config"
            }
            | {"file": f"configs/{filename}"}
        )

    MANIFEST.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Saved {len(selected)} OpenVPN configurations")


if __name__ == "__main__":
    main()

