# -*- coding: utf-8 -*-
"""
국토부 실거래 다운로더 — 안정화 전체본

주요 보강:
1) 국토부 페이지가 느리거나 headless에서 DOM 생성이 늦을 때를 대비해 대기 로직 완화
2) 탭 컨테이너 ul.quarter-tab-cover 고정 의존 제거
3) 실패 시 debug/*.html / debug/*.png 저장
4) 필요 시 HEADLESS=0 으로 실제 크롬창을 띄워 확인 가능
5) Google Drive 업로드 함수의 중복 list/update/create 호출 정리
6) ERR_EMPTY_RESPONSE / 빈 응답 / 크롬 오류 페이지 감지 후 backoff 재시도
7) 종목·월별 요청 사이 지연을 넣어 공공사이트 반복 접속 안정성 개선
8) 국토부 사이트 접속 가능 여부를 HTTP/DNS/Socket/Chrome 프리플라이트로 선검사
9) SOCKET FAIL / HTTP timeout 발생 시 즉시 backoff 후 재시도
"""

# --- runtime dep bootstrap ---
import sys
import subprocess

REQUIRED_PACKAGES = [
    "pandas",
    "numpy",
    "openpyxl",
    "google-api-python-client",
    "google-auth",
    "google-auth-httplib2",
    "google-auth-oauthlib",
    "python-dateutil",
    "pytz",
    "tzdata",
    "et-xmlfile",
    "selenium",
    "webdriver-manager",
]

def _ensure_packages():
    missing = []
    for pkg in REQUIRED_PACKAGES:
        import_name = pkg.replace("-", "_")
        if pkg == "google-api-python-client":
            import_name = "googleapiclient"
        elif pkg == "google-auth":
            import_name = "google.auth"
        elif pkg == "webdriver-manager":
            import_name = "webdriver_manager"
        try:
            __import__(import_name)
        except ModuleNotFoundError:
            missing.append(pkg)

    if missing:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--upgrade", *missing
        ])

_ensure_packages()

from pathlib import Path
import pandas as pd
import numpy as np
import json
import os
import base64
import re
import time
import random
import socket
import urllib.request
import urllib.error
import traceback
import platform
from datetime import date, timedelta, datetime
from typing import Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


# ==================== 기본 설정 ====================

URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="

OUT_DIR = Path(os.getenv("OUT_DIR", "output")).resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)

TMP_DIR = (Path.cwd() / "_rt_downloads").resolve()
TMP_DIR.mkdir(parents=True, exist_ok=True)

DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "debug")).resolve()
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "30"))
CLICK_RETRY_MAX = int(os.getenv("CLICK_RETRY_MAX", "15"))
CLICK_RETRY_WAIT = float(os.getenv("CLICK_RETRY_WAIT", "1"))
NAV_RETRY_MAX = int(os.getenv("NAV_RETRY_MAX", "10"))
PAGELOAD_TIMEOUT = int(os.getenv("PAGELOAD_TIMEOUT", "120"))

# 국토부 서버가 반복 접속 중 ERR_EMPTY_RESPONSE를 내는 경우가 있어 요청 사이에 간격을 둠
NAV_BACKOFF_BASE = float(os.getenv("NAV_BACKOFF_BASE", "8"))      # 페이지 진입 실패 시 기본 대기초
MONTH_SLEEP = float(os.getenv("MONTH_SLEEP", "2"))                # 월별 다운로드 후 대기초
CATEGORY_SLEEP = float(os.getenv("CATEGORY_SLEEP", "5"))          # 종목 변경 시 대기초
JITTER_SLEEP = float(os.getenv("JITTER_SLEEP", "1.5"))            # 랜덤 지터 최대초

# 접속 테스트 옵션
# RUN_ACCESS_TEST=1: Selenium 실행 전 DNS/Socket/HTTP로 국토부 접속 가능성 테스트
# MOLIT_ACCESS_TEST_ONLY=1: 다운로드는 하지 않고 접속 테스트만 수행
# BROWSER_PREFLIGHT=1: Chrome/Selenium으로 실제 페이지 진입 테스트
# STRICT_PREFLIGHT=1: 프리플라이트 실패 시 전체 작업 중단
# ACCESS_FAIL_ACTION:
#   auto     = DNS OK + SOCKET FAIL 반복이면 GitHub runner 접속 차단으로 보고 성공 종료
#   fail     = 접속 테스트 실패 시 exit 1
#   skip     = 접속 테스트 실패 시 작업을 건너뛰고 exit 0
#   continue = 접속 테스트 실패 후에도 Selenium 단계 진행
RUN_ACCESS_TEST = os.getenv("RUN_ACCESS_TEST", "1").strip() not in ("0", "false", "False", "NO", "no")
MOLIT_ACCESS_TEST_ONLY = os.getenv("MOLIT_ACCESS_TEST_ONLY", "0").strip() in ("1", "true", "True", "YES", "yes")
BROWSER_PREFLIGHT = os.getenv("BROWSER_PREFLIGHT", "1").strip() not in ("0", "false", "False", "NO", "no")
STRICT_PREFLIGHT = os.getenv("STRICT_PREFLIGHT", "1").strip() not in ("0", "false", "False", "NO", "no")
ACCESS_TEST_RETRY = int(os.getenv("ACCESS_TEST_RETRY", "10"))
ACCESS_TEST_TIMEOUT = int(os.getenv("ACCESS_TEST_TIMEOUT", "60"))
ACCESS_SOCKET_RETRY_BASE = float(os.getenv("ACCESS_SOCKET_RETRY_BASE", "20"))
ACCESS_FAIL_ACTION = os.getenv("ACCESS_FAIL_ACTION", "auto").strip().lower()
ACCESS_SOCKET_FAIL_SKIP_AFTER = int(os.getenv("ACCESS_SOCKET_FAIL_SKIP_AFTER", "3"))

# HEADLESS=0 으로 실행하면 실제 크롬창이 뜸
HEADLESS = os.getenv("HEADLESS", "1").strip() not in ("0", "false", "False", "NO", "no")
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36",
)

PROPERTY_TYPES = [
    "아파트",
    "연립다세대",
    "단독다가구",
    "오피스텔",
    "상업업무용",
    "토지",
    "공장창고등",
]

TAB_IDS = {
    "아파트": "xlsTab1",
    "연립다세대": "xlsTab2",
    "단독다가구": "xlsTab3",
    "오피스텔": "xlsTab4",
    "상업업무용": "xlsTab6",
    "토지": "xlsTab7",
    "공장창고등": "xlsTab8",
}

TAB_TEXT = {
    "아파트": "아파트",
    "연립다세대": "연립/다세대",
    "단독다가구": "단독/다가구",
    "오피스텔": "오피스텔",
    "상업업무용": "상업/업무용",
    "토지": "토지",
    "공장창고등": "공장/창고 등",
}

FOLDER_MAP = {
    "아파트": "아파트",
    "단독다가구": "단독다가구",
    "연립다세대": "연립다세대",
    "오피스텔": "오피스텔",
    "상업업무용": "상업업무용",
    "토지": "토지",
    "공장창고등": "공장창고등",
}

DRIVE_ROOT_ID = os.getenv("GDRIVE_FOLDER_ID", "").strip()
GDRIVE_BASE_PATH = os.getenv("GDRIVE_BASE_PATH", "").strip()


def log(msg):
    print(msg, flush=True)


# ==================== 국토부 접속 테스트 ====================

def _write_access_test_report(lines):
    """
    GitHub Actions/로컬 실행환경에서 국토부 사이트 접속 가능 여부를
    debug/molit_access_test.txt 에 저장.
    """
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        report_path = DEBUG_DIR / "molit_access_test.txt"
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"  - access test report saved: {report_path}")
    except Exception as e:
        log(f"  - access test report save failed: {e}")


def _access_retry_sleep(reason: str, attempt: int):
    """
    DNS/Socket/HTTP 사전 접속 테스트 실패 시 재시도 대기.
    GitHub runner와 국토부 서버 사이 연결이 일시적으로 막히는 경우를 대비한다.
    """
    sec = ACCESS_SOCKET_RETRY_BASE + (attempt - 1) * 30 + random.uniform(0, JITTER_SLEEP)
    log(f"{reason} retry sleep : {sec:.1f}s")
    time.sleep(sec)


def _new_access_result() -> dict:
    return {
        "ok": False,
        "dns": False,
        "socket": False,
        "http": False,
        "fail_stage": "",
        "fail_reason": "",
        "classification": "",
    }


def should_skip_for_network_block(result: dict) -> bool:
    """
    GitHub Actions runner에서 국토부 서버까지 TCP 연결 자체가 막힌 경우를 자동 분류.
    DNS는 되지만 SOCKET이 반복 실패하면 스크립트/셀레니움 문제가 아니라 실행환경 문제로 본다.
    """
    return (
        bool(result.get("dns"))
        and not bool(result.get("socket"))
        and result.get("fail_stage") == "socket"
    )


def test_molit_access() -> dict:
    """
    Selenium을 띄우기 전에 국토부 서버가 현재 실행환경에서 열리는지 확인.

    수정 포인트:
    - SOCKET FAIL: TimeoutError 가 나오면 HTTP 단계로 억지 진행하지 않고 바로 재시도한다.
    - DNS/Socket/HTTP 세 단계를 하나의 attempt로 묶어 ACCESS_TEST_RETRY 횟수만큼 반복한다.
    - GitHub Actions runner IP/라우팅이 일시적으로 막히는 상황을 흡수하기 위해 기본값은 5회, 60초 timeout.

    확인 단계:
    1) DNS 조회
    2) 443 포트 TCP 연결
    3) urllib HTTPS GET
    """
    lines = []

    def rec(msg):
        lines.append(msg)
        log(msg)

    rec("=== MOLIT ACCESS TEST START ===")
    rec(f"time_utc      : {datetime.utcnow().isoformat()}Z")
    rec(f"python        : {sys.version.replace(chr(10), ' ')}")
    rec(f"platform      : {platform.platform()}")
    rec(f"url           : {URL}")
    rec(f"user_agent    : {USER_AGENT}")
    rec(f"timeout       : {ACCESS_TEST_TIMEOUT}s")
    rec(f"retry         : {ACCESS_TEST_RETRY}")

    host = "rt.molit.go.kr"
    result = _new_access_result()
    last_dns = False
    last_socket = False
    last_http = False
    last_fail_stage = ""
    last_fail_reason = ""
    socket_fail_count = 0

    for attempt in range(1, ACCESS_TEST_RETRY + 1):
        rec(f"--- ACCESS attempt {attempt}/{ACCESS_TEST_RETRY} ---")

        ok_dns = False
        ok_socket = False
        ok_http = False

        # 1) DNS 조회
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            ips = sorted({info[4][0] for info in infos})
            ok_dns = bool(ips)
            rec(f"DNS OK        : {', '.join(ips[:10])}")
        except Exception as e:
            rec(f"DNS FAIL      : {type(e).__name__}: {e}")
            last_fail_stage = "dns"
            last_fail_reason = f"{type(e).__name__}: {e}"
            last_dns, last_socket, last_http = ok_dns, ok_socket, ok_http
            if attempt < ACCESS_TEST_RETRY:
                _access_retry_sleep("DNS FAIL", attempt)
                continue
            break

        # 2) TCP 443 포트 연결
        # 여기서 TimeoutError가 나면 국토부 페이지/셀레니움 문제가 아니라 네트워크 연결 문제다.
        # 요구사항: SOCKET FAIL 발생 시 바로 재시도.
        try:
            with socket.create_connection((host, 443), timeout=ACCESS_TEST_TIMEOUT):
                ok_socket = True
            rec("SOCKET OK     : TCP 443 connected")
        except Exception as e:
            rec(f"SOCKET FAIL   : {type(e).__name__}: {e}")
            last_fail_stage = "socket"
            last_fail_reason = f"{type(e).__name__}: {e}"
            last_dns, last_socket, last_http = ok_dns, ok_socket, ok_http
            socket_fail_count += 1
            if (
                ACCESS_FAIL_ACTION == "auto"
                and socket_fail_count >= ACCESS_SOCKET_FAIL_SKIP_AFTER
            ):
                rec(
                    "SOCKET FAIL auto stop: "
                    f"{socket_fail_count} consecutive socket failures"
                )
                break
            if attempt < ACCESS_TEST_RETRY:
                _access_retry_sleep("SOCKET FAIL", attempt)
                continue
            break

        # 3) HTTPS GET
        try:
            req = urllib.request.Request(
                URL,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
                    "Connection": "close",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=ACCESS_TEST_TIMEOUT) as resp:
                status = getattr(resp, "status", None)
                final_url = resp.geturl()
                content_type = resp.headers.get("content-type", "")
                raw = resp.read(4096)
                text = raw.decode("utf-8", errors="ignore")

            preview = re.sub(r"\s+", " ", text[:500]).strip()
            ok_http = bool(status and 200 <= int(status) < 400 and len(raw) > 0)
            rec(f"HTTP OK       : status={status}, bytes={len(raw)}, content-type={content_type}")
            rec(f"HTTP final_url: {final_url}")
            rec(f"HTTP preview  : {preview[:300]}")

            if ok_http:
                last_fail_stage = ""
                last_fail_reason = ""
                last_dns, last_socket, last_http = ok_dns, ok_socket, ok_http
                rec("ACCESS attempt result: OK")
                break

        except urllib.error.HTTPError as e:
            rec(f"HTTP FAIL     : HTTPError {e.code} {e.reason}")
            last_fail_stage = "http"
            last_fail_reason = f"HTTPError {e.code} {e.reason}"
        except urllib.error.URLError as e:
            rec(f"HTTP FAIL     : URLError {e.reason}")
            last_fail_stage = "http"
            last_fail_reason = f"URLError {e.reason}"
        except Exception as e:
            rec(f"HTTP FAIL     : {type(e).__name__}: {e}")
            rec(traceback.format_exc(limit=2).strip())
            last_fail_stage = "http"
            last_fail_reason = f"{type(e).__name__}: {e}"

        last_dns, last_socket, last_http = ok_dns, ok_socket, ok_http
        if attempt < ACCESS_TEST_RETRY:
            _access_retry_sleep("HTTP FAIL", attempt)

    rec(f"RESULT DNS     : {'OK' if last_dns else 'FAIL'}")
    rec(f"RESULT SOCKET  : {'OK' if last_socket else 'FAIL'}")
    rec(f"RESULT HTTP    : {'OK' if last_http else 'FAIL'}")
    rec("=== MOLIT ACCESS TEST END ===")
    _write_access_test_report(lines)

    result.update({
        "ok": last_dns and last_socket and last_http,
        "dns": last_dns,
        "socket": last_socket,
        "http": last_http,
        "fail_stage": last_fail_stage,
        "fail_reason": last_fail_reason,
    })

    if result["ok"]:
        result["classification"] = "ok"
    elif should_skip_for_network_block(result):
        result["classification"] = "runner_network_block"
    elif result["dns"] and result["socket"] and not result["http"]:
        result["classification"] = "molit_http_unstable"
    elif not result["dns"]:
        result["classification"] = "dns_failure"
    else:
        result["classification"] = "unknown_access_failure"

    return result

def browser_preflight(driver: webdriver.Chrome) -> bool:
    """
    Chrome/Selenium 기준으로 국토부 페이지가 실제로 열리고,
    아파트 탭까지 클릭 가능한지 확인.
    """
    log("=== BROWSER PREFLIGHT START ===")

    ok_page = open_rt_page(driver, 0)
    if not ok_page:
        log("=== BROWSER PREFLIGHT FAIL: page open failed ===")
        return False

    ok_tab = click_tab(
        driver,
        TAB_IDS.get("아파트", "xlsTab1"),
        tab_label=TAB_TEXT.get("아파트"),
        wait_sec=30,
    )
    if not ok_tab:
        log("=== BROWSER PREFLIGHT FAIL: apartment tab click failed ===")
        return False

    # 날짜 입력칸까지 보이는지 가볍게 확인
    try:
        find_date_inputs(driver)
        driver.switch_to.default_content()
        log("=== BROWSER PREFLIGHT OK: page/tab/date inputs available ===")
        return True
    except Exception as e:
        driver.switch_to.default_content()
        log(f"=== BROWSER PREFLIGHT FAIL: date inputs unavailable: {e} ===")
        return False


# ==================== 디버그 저장 ====================

def save_debug(driver: webdriver.Chrome, name: str):
    """
    실패 순간의 HTML/스크린샷 저장.
    debug 폴더에 name.html / name.png 생성.
    """
    safe = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", name).strip("_")
    html_path = DEBUG_DIR / f"{safe}.html"
    png_path = DEBUG_DIR / f"{safe}.png"

    try:
        html_path.write_text(driver.page_source or "", encoding="utf-8")
        log(f"  - debug html saved: {html_path}")
    except Exception as e:
        log(f"  - debug html save failed: {e}")

    try:
        driver.save_screenshot(str(png_path))
        log(f"  - debug screenshot saved: {png_path}")
    except Exception as e:
        log(f"  - debug screenshot save failed: {e}")

    try:
        title = driver.title
        current_url = driver.current_url
        body_text = driver.execute_script(
            "return document.body ? document.body.innerText.slice(0, 1000) : '';"
        )
        log(f"  - debug title: {title}")
        log(f"  - debug current_url: {current_url}")
        log("  - debug body text preview:")
        log(body_text)
    except Exception:
        pass


# ==================== Google Drive 업로드 ====================

def load_sa():
    raw = os.getenv("GCP_SERVICE_ACCOUNT_KEY", "").strip()
    if not raw:
        raise RuntimeError("Service account key missing")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(base64.b64decode(raw).decode("utf-8"))

    return Credentials.from_service_account_info(
        data,
        scopes=["https://www.googleapis.com/auth/drive"],
    )


def find_child_folder_id(svc, parent_id: str, name: str):
    safe_name = name.replace("'", "\\'")
    q = (
        f"name='{safe_name}' and '{parent_id}' in parents "
        "and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    resp = (
        svc.files()
        .list(
            q=q,
            spaces="drive",
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def resolve_path(svc, start_parent_id: str, path: str):
    current = start_parent_id
    if not path:
        return current

    for seg in [p.strip() for p in path.split("/") if p.strip()]:
        found = find_child_folder_id(svc, current, seg)
        if not found:
            return None
        current = found

    return current


def detect_base_parent_id(svc):
    if GDRIVE_BASE_PATH:
        bp = resolve_path(svc, DRIVE_ROOT_ID, GDRIVE_BASE_PATH)
        if bp:
            return bp

    guess = find_child_folder_id(svc, DRIVE_ROOT_ID, "부동산 실거래자료")
    return guess or DRIVE_ROOT_ID


def _guess_mimetype(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext == ".csv":
        return "text/csv"
    return "application/octet-stream"


def upload_processed(file_path: Path, prop_kind: str):
    """
    전처리된 파일(xlsx/csv)을 Google Drive 기존 폴더에 업로드 또는 덮어쓰기.
    폴더는 새로 만들지 않음.
    """
    if not file_path.exists():
        log(f"  - drive: skip (file not found): {file_path}")
        return

    if not DRIVE_ROOT_ID:
        log("  - drive: skip (missing GDRIVE_FOLDER_ID)")
        return

    try:
        creds = load_sa()
    except Exception as e:
        log(f"  - drive: skip (SA load error): {e}")
        return

    svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    base_parent_id = detect_base_parent_id(svc)
    if not base_parent_id:
        log(f"  - drive: skip (base path not found): {GDRIVE_BASE_PATH}")
        return

    subfolder = FOLDER_MAP.get(prop_kind, prop_kind)
    folder_id = find_child_folder_id(svc, base_parent_id, subfolder)
    if not folder_id:
        log(
            "  - drive: skip (category folder missing): "
            f"{GDRIVE_BASE_PATH or '자동탐지 베이스'}/{subfolder}"
        )
        return

    name = file_path.name
    mimetype = _guess_mimetype(file_path)
    media = MediaFileUpload(file_path.as_posix(), mimetype=mimetype, resumable=True)

    try:
        root_meta = svc.files().get(fileId=DRIVE_ROOT_ID, fields="id,name").execute()
        base_meta = svc.files().get(fileId=base_parent_id, fields="id,name,parents").execute()
        root_name = root_meta.get("name", "")
        base_name = base_meta.get("name", "")
    except Exception:
        root_name = ""
        base_name = GDRIVE_BASE_PATH or ""

    safe_name = name.replace("'", "\\'")
    q = f"name='{safe_name}' and '{folder_id}' in parents and trashed=false"

    resp = (
        svc.files()
        .list(
            q=q,
            spaces="drive",
            fields="files(id,name,parents,webViewLink,modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )

    files = resp.get("files", [])
    path_parts = [p for p in [root_name, base_name, subfolder, name] if p]
    full_path_for_log = "/".join(path_parts) if path_parts else f"{subfolder}/{name}"

    log(
        f"  - drive target: {full_path_for_log} "
        f"(https://drive.google.com/drive/folders/{folder_id})"
    )

    if files:
        fid = files[0]["id"]
        res = (
            svc.files()
            .update(
                fileId=fid,
                media_body=media,
                supportsAllDrives=True,
                fields="id,name,parents,webViewLink,modifiedTime",
            )
            .execute()
        )
        log(f"  - drive: overwritten (update) -> {full_path_for_log}")
    else:
        meta = {"name": name, "parents": [folder_id]}
        res = (
            svc.files()
            .create(
                body=meta,
                media_body=media,
                fields="id,name,parents,webViewLink,modifiedTime",
                supportsAllDrives=True,
            )
            .execute()
        )
        log(f"  - drive: uploaded (create) -> {full_path_for_log}")

    log(f"    · file id      = {res.get('id')}")
    log(f"    · webViewLink  = {res.get('webViewLink')}")
    log(f"    · modifiedTime = {res.get('modifiedTime')}")


# ==================== 날짜 유틸 ====================

def today_kst() -> date:
    return (datetime.utcnow() + timedelta(hours=9)).date()


def month_first(d: date) -> date:
    return date(d.year, d.month, 1)


def shift_months(d: date, k: int) -> date:
    y = d.year + (d.month - 1 + k) // 12
    m = (d.month - 1 + k) % 12 + 1
    return date(y, m, 1)


# ==================== 크롬 드라이버 ====================

def build_driver(download_dir: Path) -> webdriver.Chrome:
    opts = Options()

    if HEADLESS:
        opts.add_argument("--headless=new")
    else:
        log("  - HEADLESS=0: 실제 크롬창 표시 모드")

    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument(f"--user-agent={USER_AGENT}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--remote-allow-origins=*")
    opts.add_argument("--disable-quic")

    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    # complete까지 기다리지 않고 DOMInteractive 수준에서 반환
    opts.page_load_strategy = "eager"

    prefs = {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    opts.add_experimental_option("prefs", prefs)

    if os.getenv("CHROME_BIN"):
        opts.binary_location = os.getenv("CHROME_BIN")

    chromedriver_bin = os.getenv("CHROMEDRIVER_BIN")
    if chromedriver_bin and Path(chromedriver_bin).exists():
        service = Service(chromedriver_bin)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(PAGELOAD_TIMEOUT)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko']});
                Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                """
            },
        )
    except Exception:
        pass

    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(download_dir),
                "eventsEnabled": True,
            },
        )
    except Exception:
        pass

    return driver


# ==================== 페이지 조작 ====================

def _try_accept_alert(driver: webdriver.Chrome, wait=1.5) -> bool:
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            Alert(driver).accept()
            return True
        except Exception:
            time.sleep(0.15)
    return False


def wait_page_has_body(driver: webdriver.Chrome, wait_sec=30) -> bool:
    """
    readyState complete에 과도하게 의존하지 않고 body 텍스트 또는 링크/버튼 출현을 기다림.
    """
    try:
        WebDriverWait(driver, wait_sec).until(
            lambda d: d.execute_script(
                """
                if (!document.body) return false;
                const txt = document.body.innerText || '';
                const controls = document.querySelectorAll('a,button,input,select').length;
                return txt.length > 20 || controls > 5;
                """
            )
        )
        return True
    except Exception as e:
        log(f"  - body/control wait failed: {e}")
        return False


def get_body_text(driver: webdriver.Chrome, limit: int = 2000) -> str:
    try:
        return driver.execute_script(
            "return document.body ? (document.body.innerText || '').slice(0, arguments[0]) : '';",
            limit,
        ) or ""
    except Exception:
        return ""


def page_has_empty_response(driver: webdriver.Chrome) -> bool:
    """
    Chrome 오류 페이지/국토부 빈 응답을 감지.
    이 상태에서는 탭 DOM이 없으므로 클릭을 시도하지 말고 재접속해야 함.
    """
    txt = get_body_text(driver, 3000)
    markers = [
        "ERR_EMPTY_RESPONSE",
        "didn’t send any data",
        "didn't send any data",
        "This page isn’t working",
        "This page isn't working",
        "사이트에 연결할 수 없음",
        "페이지가 작동하지 않습니다",
    ]
    return any(m in txt for m in markers)


def page_looks_unusable(driver: webdriver.Chrome) -> bool:
    """
    빈 응답뿐 아니라 body가 지나치게 짧고 컨트롤이 거의 없는 오류 상태도 감지.
    """
    if page_has_empty_response(driver):
        return True
    try:
        txt_len, controls = driver.execute_script(
            """
            const txt = document.body ? (document.body.innerText || '') : '';
            const controls = document.querySelectorAll('a,button,input,select').length;
            return [txt.length, controls];
            """
        )
        return txt_len < 20 and controls < 3
    except Exception:
        return True


def backoff_sleep(reason: str, attempt: int):
    sec = NAV_BACKOFF_BASE + (attempt * 4) + random.uniform(0, JITTER_SLEEP)
    log(f"  - backoff: {reason} -> sleep {sec:.1f}s")
    time.sleep(sec)


def click_tab(driver: webdriver.Chrome, tab_id: str, wait_sec=30, tab_label: Optional[str] = None) -> bool:
    """
    탭 클릭 보강 버전.
    기존처럼 ul.quarter-tab-cover가 반드시 있어야 한다고 보지 않음.
    1) ID 클릭
    2) href/id/onclick에 tab_id가 들어간 요소 클릭
    3) 텍스트 라벨 클릭
    4) 유사 텍스트 클릭
    실패 시 debug 저장
    """
    driver.switch_to.default_content()
    _try_accept_alert(driver, 1.0)

    if not wait_page_has_body(driver, wait_sec=wait_sec):
        save_debug(driver, f"tab_body_wait_failed_{tab_id}")
        return False

    if page_has_empty_response(driver):
        save_debug(driver, f"tab_empty_response_{tab_id}_{tab_label or ''}")
        log("  - tab click skipped: ERR_EMPTY_RESPONSE page")
        return False

    lbl = tab_label or ""

    def _try_click_in_current_context(context_name: str) -> bool:
        # 기존 컨테이너가 있으면 로그만 남김. 없어도 실패 처리하지 않음.
        try:
            has_old_container = driver.execute_script(
                "return !!document.querySelector('ul.quarter-tab-cover');"
            )
            log(f"  - {context_name}: tab container quarter-tab-cover exists: {has_old_container}")
        except Exception:
            pass

        # 1) ID로 직접 클릭
        try:
            el = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.ID, tab_id))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.1)
            driver.execute_script(
                """
                const el = arguments[0];
                const target = el.closest('a,button,li,[role="tab"],[onclick]') || el;
                target.click();
                """,
                el,
            )
            time.sleep(0.5)
            log(f"  - tab clicked by id: {tab_id} ({context_name})")
            return True
        except Exception as e:
            log(f"  - {context_name}: tab id click failed: {tab_id} / {e}")

        # 2) href/id/onclick 속성에 tab_id가 들어간 요소
        try:
            js = """
            const tabId = arguments[0];
            const els = [...document.querySelectorAll('a,button,input,li,span,div')];
            const target = els.find(e => {
                const id = e.id || '';
                const href = e.getAttribute('href') || '';
                const onclick = e.getAttribute('onclick') || '';
                return id === tabId || href.includes(tabId) || onclick.includes(tabId);
            });
            if (target) {
                const clickable = target.closest('a,button,li,[role="tab"],[onclick]') || target;
                clickable.scrollIntoView({block:'center'});
                clickable.click();
                return true;
            }
            return false;
            """
            if driver.execute_script(js, tab_id):
                time.sleep(0.5)
                log(f"  - tab clicked by attribute: {tab_id} ({context_name})")
                return True
        except Exception as e:
            log(f"  - {context_name}: tab attribute click failed: {e}")

        # 3) 정확한 텍스트 라벨
        if lbl:
            try:
                js = """
                const lbl = arguments[0].trim();
                const els = [...document.querySelectorAll('a,button,input,li,span,div')];
                const target = els.find(e => {
                    const style = window.getComputedStyle(e);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    const txt = (e.innerText || e.value || e.textContent || '').trim();
                    return txt === lbl;
                });
                if (target) {
                    const clickable = target.closest('a,button,li,[role="tab"],[onclick]') || target;
                    clickable.scrollIntoView({block:'center'});
                    clickable.click();
                    return true;
                }
                return false;
                """
                if driver.execute_script(js, lbl):
                    time.sleep(0.5)
                    log(f"  - tab clicked by exact text: {lbl} ({context_name})")
                    return True
            except Exception as e:
                log(f"  - {context_name}: tab exact text click failed: {e}")

        # 4) 유사 텍스트
        if lbl:
            try:
                key = lbl.replace("/", "").replace(" ", "")
                js = """
                const key = arguments[0];
                const els = [...document.querySelectorAll('a,button,input,li,span,div')];
                const target = els.find(e => {
                    const style = window.getComputedStyle(e);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    const raw = (e.innerText || e.value || e.textContent || '');
                    const txt = raw.replaceAll('/', '').replaceAll(' ', '').trim();
                    return txt.includes(key);
                });
                if (target) {
                    const clickable = target.closest('a,button,li,[role="tab"],[onclick]') || target;
                    clickable.scrollIntoView({block:'center'});
                    clickable.click();
                    return true;
                }
                return false;
                """
                if driver.execute_script(js, key):
                    time.sleep(0.5)
                    log(f"  - tab clicked by fuzzy text: {lbl} ({context_name})")
                    return True
            except Exception as e:
                log(f"  - {context_name}: tab fuzzy text click failed: {e}")

        return False

    # 기본 문서에서 먼저 시도
    driver.switch_to.default_content()
    if _try_click_in_current_context("default"):
        return True

    # 탭이 iframe/frame 안에 들어간 경우도 시도
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
    for idx, fr in enumerate(frames, start=1):
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(fr)
            if _try_click_in_current_context(f"frame{idx}"):
                return True
        except Exception as e:
            log(f"  - frame{idx}: tab search failed: {e}")

    driver.switch_to.default_content()
    save_debug(driver, f"tab_click_failed_{tab_id}_{tab_label or ''}")
    log("  - tab click failed: all strategies")
    return False


# ==================== 날짜 입력 찾기/설정 ====================

def _looks_like_date_input(el) -> bool:
    typ = (el.get_attribute("type") or "").lower()
    ph = (el.get_attribute("placeholder") or "").lower()
    val = (el.get_attribute("value") or "").lower()
    name = (el.get_attribute("name") or "").lower()
    id_ = (el.get_attribute("id") or "").lower()
    txt = " ".join([ph, val, name, id_])

    return (
        typ in ("date", "text", "")
        and (
            re.search(r"\d{4}-\d{2}-\d{2}", ph)
            or re.search(r"\d{4}-\d{2}-\d{2}", val)
            or "yyyy" in ph
            or "yyyy-mm-dd" in ph
            or any(k in txt for k in ["start", "end", "from", "to", "srchbgnde", "srchendde"])
        )
    )


def _find_inputs_current_context(driver) -> Optional[Tuple]:
    pairs = [
        ("#srchBgnDe", "#srchEndDe"),
        ("input[name='srchBgnDe']", "input[name='srchEndDe']"),
        ("#startDate", "#endDate"),
        ("input[name='startDate']", "input[name='endDate']"),
    ]

    for sel_s, sel_e in pairs:
        try:
            s = driver.find_element(By.CSS_SELECTOR, sel_s)
            e = driver.find_element(By.CSS_SELECTOR, sel_e)
            return s, e
        except Exception:
            pass

    inputs = driver.find_elements(By.CSS_SELECTOR, "input")
    cands = [el for el in inputs if _looks_like_date_input(el)]
    if len(cands) >= 2:
        return cands[0], cands[1]

    dates = [e for e in inputs if (e.get_attribute("type") or "").lower() == "date"]
    if len(dates) >= 2:
        return dates[0], dates[1]

    return None


def find_date_inputs(driver) -> Tuple:
    driver.switch_to.default_content()
    _try_accept_alert(driver, 1.0)

    pair = _find_inputs_current_context(driver)
    if pair:
        return pair

    frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
    for fr in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(fr)
            pair = _find_inputs_current_context(driver)
            if pair:
                return pair
        except Exception:
            continue

    driver.switch_to.default_content()
    save_debug(driver, "date_inputs_not_found")
    raise RuntimeError("날짜 입력 박스를 찾지 못했습니다.")


def _type_and_verify(el, val: str) -> bool:
    try:
        el.click()
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.DELETE)
        el.send_keys(val)
        time.sleep(0.1)
        el.send_keys(Keys.TAB)
        time.sleep(0.1)
        return (el.get_attribute("value") or "").strip() == val
    except Exception:
        return False


def _ensure_value_with_js(driver, el, val: str) -> bool:
    try:
        driver.execute_script(
            """
            const el = arguments[0], v = arguments[1];
            el.value = v;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            el.blur();
            """,
            el,
            val,
        )
        time.sleep(0.1)
        return (el.get_attribute("value") or "").strip() == val
    except Exception:
        return False


def set_dates(driver, start: date, end: date):
    _try_accept_alert(driver, 1.0)

    s_el, e_el = find_date_inputs(driver)
    s_val = start.isoformat()
    e_val = end.isoformat()

    ok_s = _type_and_verify(s_el, s_val) or _ensure_value_with_js(driver, s_el, s_val)
    ok_e = _type_and_verify(e_el, e_val) or _ensure_value_with_js(driver, e_el, e_val)

    if not ok_s or not ok_e:
        sv = (s_el.get_attribute("value") or "").strip()
        ev = (e_el.get_attribute("value") or "").strip()
        log(f"  - warn: date fill verify failed. want=({s_val},{e_val}) got=({sv},{ev})")

    assert (s_el.get_attribute("value") or "").strip() == s_val
    assert (e_el.get_attribute("value") or "").strip() == e_val


# ==================== 다운로드 클릭/대기 ====================

def _click_by_locators(driver, label: str) -> bool:
    locators = [
        (By.XPATH, f"//button[normalize-space()='{label}']"),
        (By.XPATH, f"//a[normalize-space()='{label}']"),
        (By.XPATH, f"//input[@type='button' and @value='{label}']"),
        (By.XPATH, f"//*[contains(normalize-space(),'{label}') and (self::a or self::button or self::input or self::span)]"),
        (By.XPATH, "//*[contains(@onclick,'excel') and (self::a or self::button or self::input or self::span)]"),
        (By.XPATH, "//*[@id='excelDown' or @id='btnExcel' or contains(@id,'excel') or contains(@class,'excel')]"),
    ]

    for by, q in locators:
        try:
            els = driver.find_elements(by, q)
            for el in els:
                if not el.is_displayed():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.05)
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                _try_accept_alert(driver, 2.0)
                return True
        except Exception:
            continue

    return False


def click_download(driver, kind="excel") -> bool:
    label = "EXCEL 다운" if kind == "excel" else "CSV 다운"
    _try_accept_alert(driver, 1.0)

    if _click_by_locators(driver, label):
        _try_accept_alert(driver, 3.0)
        return True

    # 함수명 직접 호출 fallback
    fn_names = [
        "excelDown",
        "xlsDown",
        "excelDownload",
        "fnExcel",
        "fnExcelDown",
        "fncExcel",
        "csvDown",
        "fnCsv",
        "fnCsvDown",
    ]

    for fn in fn_names:
        try:
            ok = driver.execute_script(
                """
                const fn = arguments[0];
                if (typeof window[fn] === 'function') {
                    window[fn]();
                    return true;
                }
                return false;
                """,
                fn,
            )
            if ok:
                _try_accept_alert(driver, 3.0)
                return True
        except Exception:
            continue

    save_debug(driver, f"download_click_failed_{kind}")
    return False


def wait_download(dldir: Path, before: set, timeout: int) -> Path:
    endt = time.time() + timeout

    while time.time() < endt:
        allf = set(p for p in dldir.glob("*") if p.is_file())
        newf = [
            p
            for p in allf - before
            if not p.name.endswith(".crdownload")
            and not p.name.endswith(".tmp")
            and p.stat().st_size > 0
        ]

        if newf:
            # 다운로드 완료 직후 파일 크기가 아직 변할 수 있어 0.5초 안정화
            latest = max(newf, key=lambda p: p.stat().st_mtime)
            size1 = latest.stat().st_size
            time.sleep(0.5)
            size2 = latest.stat().st_size
            if size1 == size2:
                return latest

        time.sleep(0.5)

    raise TimeoutError("download not detected within timeout")


# ==================== 전처리 ====================

def _read_excel_first_table(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, engine="openpyxl", header=None, dtype=str).fillna("")
    df = raw.iloc[12:].copy().reset_index(drop=True)

    if df.empty:
        return pd.DataFrame()

    if df.shape[1] >= 1:
        df = df.iloc[:, 1:].copy()  # A열 제거

    header = df.iloc[0].astype(str).str.strip().tolist()
    df = df.iloc[1:].copy()
    df.columns = [str(c).strip() for c in header]
    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]

    return df.reset_index(drop=True)


def _drop_no_col(df: pd.DataFrame) -> pd.DataFrame:
    for c in list(df.columns):
        if str(c).strip().upper() == "NO":
            df = df[df[c].astype(str).str.strip() != ""]
            df = df.drop(columns=[c])
            break
    return df


def _split_sigungu(df: pd.DataFrame) -> pd.DataFrame:
    if "시군구" not in df.columns:
        return df

    parts = df["시군구"].astype(str).str.split(expand=True, n=3)

    for i, name in enumerate(["광역", "구", "법정동", "리"]):
        if name not in df.columns:
            df[name] = parts[i] if parts.shape[1] > i else ""

    return df


def _split_yymm(df: pd.DataFrame) -> pd.DataFrame:
    if "계약년월" not in df.columns:
        return df

    s = df["계약년월"].astype(str).str.replace(r"\D", "", regex=True)
    df["계약년"] = s.str.slice(0, 4)
    df["계약월"] = s.str.slice(4, 6)

    return df.drop(columns=["계약년월"])


def _normalize_numbers(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["거래금액(만원)", "전용면적(㎡)", "면적(㎡)"]:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"[^0-9.\-]", "", regex=True)
                .replace({"": np.nan})
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    target_order = [
        "광역",
        "구",
        "법정동",
        "리",
        "계약년",
        "계약월",
        "계약일",
        "시군구",
        "번지",
        "본번",
        "부번",
        "단지명",
        "전용면적(㎡)",
        "거래금액(만원)",
        "동",
        "층",
        "매수자",
        "매도자",
        "건축년도",
        "도로명",
        "해제사유발생일",
        "거래유형",
        "중개사소재지",
        "등기일자",
        "주택유형",
    ]

    cols = list(df.columns)
    ordered = [c for c in target_order if c in cols]
    others = [c for c in cols if c not in ordered]

    return df.reindex(columns=ordered + others)


def _assert_preprocessed(df: pd.DataFrame):
    cols = set(df.columns)

    banned = [c for c in ["계약년월"] if c in cols]
    if banned:
        raise RuntimeError(f"전처리 실패: 금지 컬럼 잔존 {banned}")

    for must in ["광역", "구", "법정동", "계약년", "계약월"]:
        if must not in cols:
            raise RuntimeError(f"전처리 실패: 필수 컬럼 누락 {must}")


def preprocess_df(df: pd.DataFrame) -> pd.DataFrame:
    df = _drop_no_col(df)
    df = _split_sigungu(df)
    df = _split_yymm(df)
    df = _normalize_numbers(df)
    df = _reorder_columns(df)
    return df


def save_excel(path: Path, df: pd.DataFrame):
    from openpyxl.utils import get_column_letter

    path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="data")
        ws = writer.sheets["data"]

        for idx, col in enumerate(df.columns, start=1):
            series = df[col]
            try:
                max_len = max(
                    [len(str(col))]
                    + [len(str(x)) if x is not None else 0 for x in series.tolist()]
                )
            except Exception:
                max_len = len(str(col))

            width = min(80, max(8, max_len + 2))
            ws.column_dimensions[get_column_letter(idx)].width = width


def save_csv(path: Path, df: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


# ==================== 파이프라인 ====================

def open_rt_page(driver: webdriver.Chrome, nav_try: int) -> bool:
    """
    국토부 페이지 진입.
    ERR_EMPTY_RESPONSE나 크롬 오류 페이지가 뜨면 False를 반환해 상위 루프가 backoff 후 재시도하게 함.
    """
    driver.switch_to.default_content()
    log(f"  - nav{nav_try}: opening page {URL}")

    try:
        driver.get("about:blank")
        time.sleep(0.5)
        driver.get(URL)
    except TimeoutException:
        log(f"  - nav{nav_try}: driver.get timeout -> keep waiting for late DOM")
    except Exception as e:
        log(f"  - nav{nav_try}: driver.get error: {e}")
        return False

    # Timeout 직후 바로 window.stop()을 호출하면 국토부 페이지 스크립트가 끊겨
    # 탭 DOM이 생성되지 않는 경우가 있다. 그래서 late DOM을 한 번 더 기다린다.
    time.sleep(2.0 + random.uniform(0, JITTER_SLEEP))
    wait_page_has_body(driver, wait_sec=45)

    try:
        current_url = driver.current_url
    except Exception:
        current_url = ""

    if current_url.startswith("data:") or current_url in ("about:blank", ""):
        log(f"  - nav{nav_try}: browser stayed on blank/data url: {current_url}")
        save_debug(driver, f"blank_or_data_url_nav{nav_try}")
        return False

    if page_has_empty_response(driver):
        log(f"  - nav{nav_try}: ERR_EMPTY_RESPONSE / empty response detected")
        save_debug(driver, f"empty_response_nav{nav_try}")
        return False

    if page_looks_unusable(driver):
        log(f"  - nav{nav_try}: page still looks thin, wait once more")
        wait_page_has_body(driver, wait_sec=30)
        if page_looks_unusable(driver):
            log(f"  - nav{nav_try}: page looks unusable")
            save_debug(driver, f"unusable_page_nav{nav_try}")
            return False

    return True


def recover_page_and_set_dates(
    driver: webdriver.Chrome,
    prop_kind: str,
    start: date,
    end: date,
):
    """
    다운로드 재시도 중 페이지를 새로 열고 탭/날짜를 다시 세팅.
    """
    if not open_rt_page(driver, 0):
        raise RuntimeError("페이지 재진입 실패")
    if not click_tab(
        driver,
        TAB_IDS.get(prop_kind, "xlsTab1"),
        tab_label=TAB_TEXT.get(prop_kind),
        wait_sec=30,
    ):
        raise RuntimeError("탭 재진입 실패")

    set_dates(driver, start, end)


def fetch_and_process(
    driver: webdriver.Chrome,
    prop_kind: str,
    start: date,
    end: date,
    outname: str,
):
    # 진입/탭/날짜 세팅
    for nav_try in range(1, NAV_RETRY_MAX + 1):
        if not open_rt_page(driver, nav_try):
            if nav_try == NAV_RETRY_MAX:
                raise RuntimeError("국토부 페이지 진입 실패")
            backoff_sleep("page open failed", nav_try)
            continue

        log(f"  - nav{nav_try}: clicking tab {prop_kind}")
        ok_tab = click_tab(
            driver,
            TAB_IDS.get(prop_kind, "xlsTab1"),
            tab_label=TAB_TEXT.get(prop_kind),
            wait_sec=30,
        )

        if not ok_tab:
            log(f"  - nav{nav_try}: tab click failed, retrying...")
            if nav_try == NAV_RETRY_MAX:
                raise RuntimeError("탭 진입 실패")
            backoff_sleep("tab click failed", nav_try)
            continue

        log(f"  - nav{nav_try}: setting dates {start} ~ {end}")
        try:
            set_dates(driver, start, end)
            log(f"  - nav{nav_try}: dates set OK")
            break
        except Exception as e:
            log(f"  - warn: navigate/tab/set_dates retry ({nav_try}/{NAV_RETRY_MAX}): {e}")
            save_debug(driver, f"set_dates_failed_{prop_kind}_{nav_try}")

            if nav_try == NAV_RETRY_MAX:
                raise

            backoff_sleep("set_dates failed", nav_try)

    # 다운로드
    before = set(p for p in TMP_DIR.glob("*") if p.is_file())
    got = None

    for attempt in range(1, CLICK_RETRY_MAX + 1):
        ok = click_download(driver, "excel")
        log(f"  - [{prop_kind}] click_download(excel) / attempt {attempt}: {ok}")

        if not ok:
            time.sleep(CLICK_RETRY_WAIT)
            if attempt % 5 == 0:
                log("  - refresh page for retry")
                recover_page_and_set_dates(driver, prop_kind, start, end)
            continue

        try:
            got = wait_download(TMP_DIR, before, timeout=DOWNLOAD_TIMEOUT)
            break
        except TimeoutError:
            log(f"  - warn: 다운로드 시작 감지 실패(시도 {attempt}/{CLICK_RETRY_MAX})")
            save_debug(driver, f"download_wait_failed_{prop_kind}_{attempt}")

            if attempt % 5 == 0:
                log("  - refresh page for retry")
                recover_page_and_set_dates(driver, prop_kind, start, end)

            continue

    if not got:
        raise RuntimeError("다운로드 실패")

    log(f"  - got file: {got}  size={got.stat().st_size:,}  ext={got.suffix}")

    # 전처리
    df = _read_excel_first_table(got)
    df = preprocess_df(df)

    log("  - 헤더(전처리 후): " + " | ".join([str(c) for c in df.columns.tolist()]))
    log(f"  - 행/열 크기: {df.shape[0]} rows × {df.shape[1]} cols")

    _assert_preprocessed(df)

    # 동일 이름의 xlsx/csv 저장
    out_xlsx = OUT_DIR / outname
    out_csv = OUT_DIR / (
        outname[:-5] + ".csv" if outname.lower().endswith(".xlsx") else outname + ".csv"
    )

    save_excel(out_xlsx, df)
    save_csv(out_csv, df)

    log(f"완료: [{prop_kind}] {out_xlsx}")
    log(f"완료: [{prop_kind}] {out_csv}")

    # Google Drive 업로드
    upload_processed(out_xlsx, prop_kind)
    upload_processed(out_csv, prop_kind)


# ==================== 메인 ====================

def main():
    t = today_kst()

    # 최근 5개월: 4개월 전 ~ 당월
    bases = [shift_months(month_first(t), -i) for i in range(4, -1, -1)]

    # 1차 접속 테스트: Selenium 실행 전 HTTP/DNS/Socket 기준 확인
    if RUN_ACCESS_TEST:
        access_result = test_molit_access()
        if not access_result["ok"]:
            log("!!! MOLIT ACCESS TEST FAILED: 현재 실행환경에서 국토부 사이트 접속이 불안정합니다.")
            log(f"!!! access classification: {access_result.get('classification')}")
            log(f"!!! fail stage          : {access_result.get('fail_stage')}")
            log(f"!!! fail reason         : {access_result.get('fail_reason')}")
            log("!!! GitHub Actions라면 runner IP 차단/빈 응답/공공사이트 제한 가능성을 먼저 의심하세요.")

            if MOLIT_ACCESS_TEST_ONLY:
                raise RuntimeError("국토부 HTTP/DNS/Socket 접속 테스트 실패")

            if ACCESS_FAIL_ACTION in ("skip", "success", "exit0"):
                log("!!! ACCESS_FAIL_ACTION=skip -> 오늘 실행은 건너뛰고 성공 종료합니다.")
                log("!!! 기존 산출물은 갱신되지 않았습니다.")
                return

            if ACCESS_FAIL_ACTION == "continue":
                log("!!! ACCESS_FAIL_ACTION=continue -> 접속 테스트 실패에도 Selenium 단계로 진행합니다.")
            elif ACCESS_FAIL_ACTION == "auto" and should_skip_for_network_block(access_result):
                log("!!! 자동판단: DNS는 성공했지만 SOCKET 연결이 반복 실패했습니다.")
                log("!!! 이는 코드 오류보다 GitHub Actions runner <-> 국토부 서버 간 접속 차단/라우팅 문제로 봅니다.")
                log("!!! 오늘 실행은 건너뛰고 성공 종료합니다. 기존 산출물은 갱신되지 않았습니다.")
                return
            elif STRICT_PREFLIGHT:
                raise RuntimeError("국토부 HTTP/DNS/Socket 접속 테스트 실패")

    if MOLIT_ACCESS_TEST_ONLY:
        log("MOLIT_ACCESS_TEST_ONLY=1 -> 접속 테스트만 수행하고 종료합니다.")
        return

    driver = build_driver(TMP_DIR)

    try:
        # 2차 접속 테스트: Chrome/Selenium 기준 실제 페이지/탭/날짜 입력칸 확인
        if BROWSER_PREFLIGHT:
            preflight_ok = browser_preflight(driver)
            if not preflight_ok:
                log("!!! BROWSER PREFLIGHT FAILED: Chrome/Selenium에서 국토부 페이지 사용 불가")
                if ACCESS_FAIL_ACTION in ("skip", "success", "exit0"):
                    log("!!! ACCESS_FAIL_ACTION=skip -> 오늘 실행은 건너뛰고 성공 종료합니다.")
                    log("!!! 기존 산출물은 갱신되지 않았습니다.")
                    return
                if ACCESS_FAIL_ACTION == "continue":
                    log("!!! ACCESS_FAIL_ACTION=continue -> Chrome 프리플라이트 실패에도 다운로드 루프로 진행합니다.")
                elif STRICT_PREFLIGHT:
                    raise RuntimeError("Chrome/Selenium 프리플라이트 실패")

        for prop_kind in PROPERTY_TYPES:
            log(f"=== category start: {prop_kind} ===")
            time.sleep(CATEGORY_SLEEP + random.uniform(0, JITTER_SLEEP))
            for base in bases:
                start = base
                if (base.year, base.month) == (t.year, t.month):
                    end = t
                else:
                    end = shift_months(base, 1) - timedelta(days=1)

                name = f"{prop_kind} {base:%Y%m}.xlsx"
                log(f"[전국/{prop_kind}] {start} ~ {end} → {name}")

                fetch_and_process(driver, prop_kind, start, end, name)
                time.sleep(MONTH_SLEEP + random.uniform(0, JITTER_SLEEP))

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
