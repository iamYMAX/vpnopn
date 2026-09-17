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

def safe_name(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return value.strip("._") or "server"

def number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def decode_config(value):
    if not value:
        return None

    encoded = re.sub(r"\s+", "", value)
    encoded += "=" * (-len(encoded) % 4)

    try:
        decoded = base64.b64decode(encoded, validate=False)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return None

def main():
    request = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "vpnopn-github-actions/1.0"
        },
    )

    print("Downloading VPN Gate API...")

    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode("utf-8-sig", errors="replace")

    print(f"Downloaded bytes: {len(raw)}")

    lines = [line for line in raw.splitlines() if line.strip()]

    print(f"Non-empty lines: {len(lines)}")

    header_index = None

    for i, line in enumerate(lines):
        if line.lstrip().startswith("#HostName,"):
            header_index = i
            break

    if header_index is None:
        print("ERROR: CSV header #HostName was not found")
        print("First 10 lines:")
        for line in lines[:10]:
            print(repr(line))
        raise RuntimeError("VPN Gate API returned no CSV header")

    header_line = lines[header_index].lstrip()[1:]

    print("CSV header found")
    print(f"Header fields: {len(next(csv.reader([header_line])))}")

    data_lines = lines[header_index + 1:]

    print(f"Data lines: {len(data_lines)}")

    csv_text = "\n".join([header_line] + data_lines)

    rows = list(csv.reader(io.StringIO(csv_text)))

    header = rows[0]

    print("Columns:")
    for index, column in enumerate(header):
        print(f"  {index}: {column}")

    records = []

    for row in rows[1:]:
        if len(row) >= len(header):
            records.append(dict(zip(header, row)))

    print(f"Parsed records: {len(records)}")

    if not records:
        raise RuntimeError("VPN Gate returned zero parsed server records")

    country_counts = {}

    for record in records:
        country = record.get("CountryShort", "").strip().upper()
        country_counts[country] = country_counts.get(country, 0) + 1

    print("Countries:")
    for country, count in sorted(country_counts.items(), key=lambda x: -x[1])[:30]:
        print(f"  {country or '<EMPTY>'}: {count}")

    excluded_ru = 0
    missing_base64 = 0
    invalid_base64 = 0
    no_client_remote = 0
    usable = []

    for record in records:

        country = record.get("CountryShort", "").strip().upper()

        if country == "RU":
            excluded_ru += 1
            continue

        config_b64 = record.get(
            "OpenVPN_ConfigData_Base64",
            ""
        ).strip()

        if not config_b64:
            missing_base64 += 1
            continue

        config = decode_config(config_b64)

        if config is None:
            invalid_base64 += 1
            continue

        normalized = config.lower()

        has_client = "client" in normalized
        has_remote = bool(
            re.search(r"(?m)^\s*remote\s+", config)
        )

        if not has_client or not has_remote:
            no_client_remote += 1

            if no_client_remote <= 3:
                print()
                print("Decoded config rejected:")
                print(f"Host: {record.get('HostName')}")
                print(f"IP: {record.get('IP')}")
                print(f"Country: {country}")
                print(f"Has client: {has_client}")
                print(f"Has remote: {has_remote}")
                print("Config beginning:")
                print(config[:500])

            continue

        usable.append(
            {
                "hostname": record.get("HostName", "").strip(),
                "ip": record.get("IP", "").strip(),
                "country": record.get("CountryLong", "").strip(),
                "country_code": country,
                "score": number(record.get("Score")),
                "ping_ms": number(record.get("Ping"), -1),
                "speed": number(record.get("Speed")),
                "sessions": int(number(record.get("NumVpnSessions"), 0)),
                "config": config,
            }
        )

    print()
    print("========== VPN GATE DIAGNOSTICS ==========")
    print(f"Total records:       {len(records)}")
    print(f"Excluded RU:         {excluded_ru}")
    print(f"Missing Base64:      {missing_base64}")
    print(f"Invalid Base64:      {invalid_base64}")
    print(f"No client/remote:    {no_client_remote}")
    print(f"Usable configs:      {len(usable)}")
    print("===========================================")

    if not usable:
        raise RuntimeError(
            "No usable non-RU OpenVPN configurations found"
        )

    usable.sort(
        key=lambda server: (
            -server["score"],
            -server["speed"],
            server["ping_ms"]
            if server["ping_ms"] >= 0
            else 10**9,
        )
    )

    selected = usable[:MAX_SERVERS]

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
                key: value
                for key, value in server.items()
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
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Saved {len(selected)} OpenVPN configurations")

if __name__ == "__main__":
    main()
