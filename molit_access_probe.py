#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import platform
import re
import socket
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TARGET_URL = os.getenv("MOLIT_TEST_URL", "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt=")
TARGET_HOST = os.getenv("MOLIT_TEST_HOST", "rt.molit.go.kr")
TARGET_PORT = int(os.getenv("MOLIT_TEST_PORT", "443"))
TIMEOUT = int(os.getenv("MOLIT_TEST_TIMEOUT", "30"))
RETRY = int(os.getenv("MOLIT_TEST_RETRY", "3"))
SLEEP = float(os.getenv("MOLIT_TEST_SLEEP", "5"))
REPORT_DIR = Path(os.getenv("MOLIT_TEST_REPORT_DIR", "debug")).resolve()
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36",
)


def log(msg):
    print(msg, flush=True)


def compact(text, limit=500):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def fetch_text(url, timeout=TIMEOUT, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(4096)
        return {
            "ok": True,
            "status": getattr(resp, "status", None),
            "url": resp.geturl(),
            "headers": dict(resp.headers.items()),
            "bytes": len(raw),
            "preview": compact(raw.decode("utf-8", errors="ignore"), 500),
        }


def get_public_ip():
    urls = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/ip",
    ]
    for url in urls:
        try:
            data = fetch_text(url, timeout=10)
            preview = data.get("preview", "")
            try:
                parsed = json.loads(preview)
                return parsed.get("ip", preview)
            except Exception:
                return preview
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
    return f"unknown ({last})"


def dns_lookup():
    infos = socket.getaddrinfo(TARGET_HOST, TARGET_PORT, proto=socket.IPPROTO_TCP)
    ips = sorted({info[4][0] for info in infos})
    return ips


def tcp_connect(ip_or_host):
    started = time.time()
    with socket.create_connection((ip_or_host, TARGET_PORT), timeout=TIMEOUT):
        elapsed = time.time() - started
    return elapsed


def tls_connect(ip_or_host):
    context = ssl.create_default_context()
    started = time.time()
    with socket.create_connection((ip_or_host, TARGET_PORT), timeout=TIMEOUT) as raw_sock:
        with context.wrap_socket(raw_sock, server_hostname=TARGET_HOST):
            elapsed = time.time() - started
    return elapsed


def http_get():
    return fetch_text(
        TARGET_URL,
        timeout=TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
            "Connection": "close",
        },
    )


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    result = {
        "ok": False,
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "runner_os": os.getenv("RUNNER_OS", ""),
        "runner_arch": os.getenv("RUNNER_ARCH", ""),
        "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "target_url": TARGET_URL,
        "target_host": TARGET_HOST,
        "target_port": TARGET_PORT,
        "timeout": TIMEOUT,
        "retry": RETRY,
        "public_ip": get_public_ip(),
        "attempts": [],
    }

    log("=== MOLIT RUNNER ACCESS MATRIX PROBE START ===")
    for key in [
        "time_utc",
        "runner_os",
        "runner_arch",
        "github_run_id",
        "python",
        "platform",
        "target_url",
        "public_ip",
        "timeout",
        "retry",
    ]:
        log(f"{key:14}: {result[key]}")

    for attempt_no in range(1, RETRY + 1):
        attempt = {
            "attempt": attempt_no,
            "dns_ok": False,
            "tcp_ok": False,
            "tls_ok": False,
            "http_ok": False,
            "errors": [],
        }
        log(f"--- ACCESS attempt {attempt_no}/{RETRY} ---")

        ips = []
        try:
            ips = dns_lookup()
            attempt["dns_ok"] = bool(ips)
            attempt["ips"] = ips
            log(f"DNS OK        : {', '.join(ips)}")
        except Exception as exc:
            msg = f"DNS FAIL: {type(exc).__name__}: {exc}"
            attempt["errors"].append(msg)
            log(msg)

        if ips:
            try:
                elapsed = tcp_connect(TARGET_HOST)
                attempt["tcp_ok"] = True
                attempt["tcp_elapsed_sec"] = round(elapsed, 3)
                log(f"TCP OK        : host:{TARGET_PORT} connected in {elapsed:.3f}s")
            except Exception as exc:
                msg = f"TCP FAIL      : {type(exc).__name__}: {exc}"
                attempt["errors"].append(msg)
                log(msg)

            try:
                elapsed = tls_connect(TARGET_HOST)
                attempt["tls_ok"] = True
                attempt["tls_elapsed_sec"] = round(elapsed, 3)
                log(f"TLS OK        : handshake in {elapsed:.3f}s")
            except Exception as exc:
                msg = f"TLS FAIL      : {type(exc).__name__}: {exc}"
                attempt["errors"].append(msg)
                log(msg)

            try:
                data = http_get()
                status = data.get("status")
                attempt["http_status"] = status
                attempt["http_bytes"] = data.get("bytes")
                attempt["http_url"] = data.get("url")
                attempt["http_content_type"] = data.get("headers", {}).get("Content-Type", "")
                attempt["http_preview"] = data.get("preview", "")
                attempt["http_ok"] = bool(status and 200 <= int(status) < 400 and data.get("bytes", 0) > 0)
                log(
                    "HTTP OK       : "
                    f"status={status}, bytes={data.get('bytes')}, "
                    f"content-type={attempt['http_content_type']}"
                )
            except urllib.error.HTTPError as exc:
                msg = f"HTTP FAIL     : HTTPError {exc.code} {exc.reason}"
                attempt["errors"].append(msg)
                log(msg)
            except urllib.error.URLError as exc:
                msg = f"HTTP FAIL     : URLError {exc.reason}"
                attempt["errors"].append(msg)
                log(msg)
            except Exception as exc:
                msg = f"HTTP FAIL     : {type(exc).__name__}: {exc}"
                attempt["errors"].append(msg)
                attempt["traceback"] = traceback.format_exc(limit=2)
                log(msg)

        result["attempts"].append(attempt)
        if attempt["dns_ok"] and attempt["tcp_ok"] and attempt["tls_ok"] and attempt["http_ok"]:
            result["ok"] = True
            break
        if attempt_no < RETRY:
            time.sleep(SLEEP)

    if result["ok"]:
        result["classification"] = "runner_can_access_molit"
    else:
        last = result["attempts"][-1] if result["attempts"] else {}
        if last.get("dns_ok") and not last.get("tcp_ok"):
            result["classification"] = "runner_tcp_block_or_routing_timeout"
        elif last.get("tcp_ok") and not last.get("tls_ok"):
            result["classification"] = "tls_handshake_failure"
        elif last.get("tls_ok") and not last.get("http_ok"):
            result["classification"] = "http_layer_failure"
        elif not last.get("dns_ok"):
            result["classification"] = "dns_failure"
        else:
            result["classification"] = "unknown_failure"

    log(f"RESULT         : {'OK' if result['ok'] else 'FAIL'}")
    log(f"CLASSIFICATION : {result['classification']}")
    log("=== MOLIT RUNNER ACCESS MATRIX PROBE END ===")

    json_path = REPORT_DIR / "molit_access_probe.json"
    txt_path = REPORT_DIR / "molit_access_probe_summary.txt"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(
        "\n".join(
            [
                f"ok={result['ok']}",
                f"classification={result['classification']}",
                f"runner_os={result['runner_os']}",
                f"public_ip={result['public_ip']}",
                f"target_url={result['target_url']}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
