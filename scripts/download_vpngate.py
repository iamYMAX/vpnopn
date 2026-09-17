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

def decode_config(value: str) -> str | None:
    if not value:
        return None

    encoded = re.sub(r"\s+", "", value)
    encoded += "=" * (-len(encoded) % 4)

    try:
        config = base64.b64decode(encoded, validate=False)
        text = config.decode("utf-8", errors="replace")
    except Exception:
        return None

    normalized = text.lower()

    if "client" not in normalized:
        return None

    if not re.search(r"(?m)^\s*remote\s+", text):
        return None

    return text

def main() -> None:
    request = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "vpnopn-github-actions/1.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8-sig", errors="replace")

    raw_lines = [line for line in raw.splitlines() if line.strip()]

    header_index = next(
        (
            i
            for i, line in enumerate(raw_lines)
            if line.lstrip().startswith("#HostName,")
        ),
        None,
    )

    if header_index is None:
        raise RuntimeError("VPN Gate API returned no CSV header")

    header_line = raw_lines[header_index].lstrip()[1:]

    data_lines = [
        line
        for line in raw_lines[header_index + 1:]
        if not line.lstrip().startswith("#")
    ]

    rows = list(
        csv.reader(
            io.StringIO("\n".join([header_line, *data_lines]))
        )
    )

    header = rows[0]

    records = [
        dict(zip(header, row))
        for row in rows[1:]
        if len(row) >= len(header)
    ]

    candidates = []
    excluded = 0
    missing_config = 0
    invalid_config = 0

    for row in records:
        country = row.get("CountryShort", "").strip().upper()

        if country in EXCLUDED_COUNTRIES:
            excluded += 1
            continue

        config_value = row.get(
            "OpenVPN_ConfigData_Base64",
            ""
        ).strip()

        config_text = decode_config(config_value)

        if config_text is None:
            if config_value:
                invalid_config += 1
            else:
                missing_config += 1
            continue

        candidates.append(
            {
                "hostname": row.get("HostName", "").strip(),
                "ip": row.get("IP", "").strip(),
                "country": row.get("CountryLong", "").strip(),
                "country_code": country,
                "score": number(row.get("Score")),
                "ping_ms": number(row.get("Ping"), -1),
                "speed": number(row.get("Speed")),
                "sessions": int(
                    number(row.get("NumVpnSessions"), 0)
                ),
                "config": config_text,
            }
        )

    print(
        f"VPN Gate rows: {len(records)}; "
        f"excluded RU: {excluded}; "
        f"missing configs: {missing_config}; "
        f"invalid configs: {invalid_config}; "
        f"usable: {len(candidates)}"
    )

    candidates.sort(
        key=lambda server: (
            -server["score"],
            -server["speed"],
            server["ping_ms"]
            if server["ping_ms"] >= 0
            else 10**9,
        )
    )

    selected = candidates[:MAX_SERVERS]

    if not selected:
        raise RuntimeError(
            "No usable non-RU OpenVPN configurations found"
        )

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
            | {
                "file": f"configs/{filename}"
            }
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

    print(
        f"Saved {len(selected)} OpenVPN configurations"
    )

if __name__ == "__main__":
    main()
