# -*- coding: utf-8 -*-
"""국토부 실거래 다운로더 — DOM 디버그 덤프/아티팩트용 버전 (FIXED v3)

목적
- GitHub Actions(헤드리스)에서 일부 탭(예: 상업/업무용) 진입 실패 시,
  실제로 브라우저가 받은 화면이 무엇인지(차단/점검/에러/리다이렉트/로딩미완료/iframe 등) 확인 가능하게
  스크린샷 + HTML(page_source) + 콘솔/URL/타이틀 정보를 자동 저장합니다.

핵심 기능
- tab container(wait) 실패 시: debug_{tag}.png / debug_{tag}.html / debug_{tag}.json 저장
- nav 재시도 루프 각 try마다: 실패 즉시 덤프

GitHub Actions에서 덤프 파일 받기(권장)
- workflow에 다음 step 추가:
  - uses: actions/upload-artifact@v4
    with:
      name: molit-debug
      path: output/debug_*
"""

# --- runtime dep bootstrap (pinned) ---
import sys, subprocess
try:
    import pandas  # noqa: F401
    import numpy   # noqa: F401
    import openpyxl  # noqa: F401
    import selenium  # noqa: F401
    import webdriver_manager  # noqa: F401
    import googleapiclient  # noqa: F401
except ModuleNotFoundError:
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--upgrade",
        "pandas>=1.5,<3",
        "numpy>=1.23,<3",
        "openpyxl>=3.1.2,<4",
        "selenium>=4.15,<5",
        "webdriver-manager>=4,<5",
        "google-api-python-client>=2,<3",
        "google-auth>=2,<3",
        "google-auth-httplib2>=0.2,<1",
        "google-auth-oauthlib>=1,<2",
        "python-dateutil>=2.8",
        "pytz",
        "tzdata",
        "et-xmlfile",
    ])

from pathlib import Path
import pandas as pd
import numpy as np
import json, os, base64, time, re
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
from selenium.common.exceptions import TimeoutException, WebDriverException


def log(msg: str):
    print(msg, flush=True)


# ==================== 환경/출력 ====================
URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="
OUT_DIR = Path(os.getenv("OUT_DIR", "output")).resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR = (Path.cwd() / "_rt_downloads").resolve()
TMP_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "30"))
CLICK_RETRY_MAX = int(os.getenv("CLICK_RETRY_MAX", "15"))
CLICK_RETRY_WAIT = float(os.getenv("CLICK_RETRY_WAIT", "1"))
NAV_RETRY_MAX = int(os.getenv("NAV_RETRY_MAX", "6"))

PAGELOAD_TIMEOUT = int(os.getenv("PAGELOAD_TIMEOUT", "40"))
TAB_WAIT_SEC = int(os.getenv("TAB_WAIT_SEC", "25"))

DEBUG_DUMP = (os.getenv("DEBUG_DUMP", "1").strip() != "0")  # 기본 ON
DEBUG_MAX_BYTES = int(os.getenv("DEBUG_MAX_BYTES", "3000000"))  # HTML 저장 최대(기본 3MB)


# ==================== 구글드라이브 업로드 ====================
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


def load_sa() -> Credentials:
    raw = os.getenv("GCP_SERVICE_ACCOUNT_KEY", "").strip()
    if not raw:
        raise RuntimeError("Service account key missing (env: GCP_SERVICE_ACCOUNT_KEY)")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(base64.b64decode(raw).decode("utf-8"))
    return Credentials.from_service_account_info(data, scopes=["https://www.googleapis.com/auth/drive"])


def find_child_folder_id(svc, parent_id: str, name: str):
    q = f"name='{name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    resp = svc.files().list(
        q=q, spaces="drive", fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def resolve_path(svc, start_parent_id: str, path: str):
    current = start_parent_id
    if not path:
        return current
    for seg in [p for p in path.split("/") if p.strip()]:
        found = find_child_folder_id(svc, current, seg.strip())
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
    if not file_path.exists():
        log(f"  - drive: skip (file not found): {file_path}")
        return
    if not DRIVE_ROOT_ID:
        log("  - drive: skip (missing DRIVE_ROOT_ID/GDRIVE_FOLDER_ID)")
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
        log(f"  - drive: skip (category folder missing): {(GDRIVE_BASE_PATH or '자동탐지 베이스')}/{subfolder}")
        return

    name = file_path.name
    media = MediaFileUpload(file_path.as_posix(), mimetype=_guess_mimetype(file_path))

    q = f"name='{name}' and '{folder_id}' in parents and trashed=false"
    resp = svc.files().list(
        q=q, spaces="drive",
        fields="files(id,name,parents,webViewLink,modifiedTime)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])

    if files:
        fid = files[0]["id"]
        res = svc.files().update(
            fileId=fid, media_body=media,
            supportsAllDrives=True,
            fields="id,name,parents,webViewLink,modifiedTime",
        ).execute()
        log(f"  - drive: overwritten (update) -> {subfolder}/{name}")
    else:
        meta = {"name": name, "parents": [folder_id]}
        res = svc.files().create(
            body=meta, media_body=media,
            supportsAllDrives=True,
            fields="id,name,parents,webViewLink,modifiedTime",
        ).execute()
        log(f"  - drive: uploaded (create) -> {subfolder}/{name}")

    log(f"    · file id      = {res.get('id')}")
    log(f"    · webViewLink  = {res.get('webViewLink')}")
    log(f"    · modifiedTime = {res.get('modifiedTime')}")


# ==================== 디버그 덤프 ====================

def _safe_slug(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^\w\-\.\(\)가-힣 ]+", "_", s)
    s = re.sub(r"\s+", "_", s)
    return s[:120] if s else "na"


def dump_debug(driver: Optional[webdriver.Chrome], tag: str, extra: Optional[dict] = None):
    if not DEBUG_DUMP:
        return

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tag2 = _safe_slug(tag)
    base = OUT_DIR / f"debug_{ts}_{tag2}"

    meta = {
        "tag": tag,
        "timestamp_utc": ts,
        "current_url": None,
        "title": None,
        "readyState": None,
        "user_agent": None,
    }
    if extra:
        meta.update(extra)

    if driver is not None:
        try:
            meta["current_url"] = driver.current_url
        except Exception:
            pass
        try:
            meta["title"] = driver.title
        except Exception:
            pass
        try:
            meta["readyState"] = driver.execute_script("return document.readyState")
        except Exception:
            pass
        try:
            meta["user_agent"] = driver.execute_script("return navigator.userAgent")
        except Exception:
            pass

        try:
            driver.save_screenshot(str(base.with_suffix(".png")))
        except Exception as e:
            meta["screenshot_error"] = str(e)

        try:
            html = driver.page_source or ""
            if len(html.encode("utf-8", errors="ignore")) > DEBUG_MAX_BYTES:
                html = html[:DEBUG_MAX_BYTES]
                meta["html_truncated"] = True
            base.with_suffix(".html").write_text(html, encoding="utf-8", errors="ignore")
        except Exception as e:
            meta["html_error"] = str(e)

        try:
            logs = driver.get_log("browser")
            base.with_suffix(".browserlog.json").write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    base.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  - debug dumped: {base.name}.png/.html/.json")


# ==================== Selenium / 페이지 조작 ====================

def today_kst() -> date:
    return (datetime.utcnow() + timedelta(hours=9)).date()


def month_first(d: date) -> date:
    return date(d.year, d.month, 1)


def shift_months(d: date, k: int) -> date:
    y = d.year + (d.month - 1 + k) // 12
    m = (d.month - 1 + k) % 12 + 1
    return date(y, m, 1)


def build_driver(download_dir: Path) -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.page_load_strategy = os.getenv("PAGE_LOAD_STRATEGY", "normal")

    prefs = {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
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
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(download_dir), "eventsEnabled": True},
        )
    except Exception:
        pass
    return driver


def _try_accept_alert(driver: webdriver.Chrome, wait=1.5) -> bool:
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            Alert(driver).accept()
            return True
        except Exception:
            time.sleep(0.15)
    return False


def click_tab(driver: webdriver.Chrome, tab_id: str, wait_sec: int = TAB_WAIT_SEC, tab_label: Optional[str] = None) -> bool:
    try:
        WebDriverWait(driver, wait_sec).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "ul.quarter-tab-cover"))
        )
    except Exception as e:
        log(f"  - tab container wait failed: {e}")
        dump_debug(driver, f"tab_container_missing_{tab_id}", extra={"phase": "wait_container"})
        return False

    try:
        el = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.ID, tab_id)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        driver.execute_script("arguments[0].click();", el)
        time.sleep(0.2)
        active = driver.execute_script(
            "var e=document.getElementById(arguments[0]);return e&&e.parentElement&&e.parentElement.classList.contains('on');",
            tab_id,
        )
        if active:
            return True
    except Exception:
        pass

    try:
        clicked = driver.execute_script(
            "var el=document.getElementById(arguments[0]); if(el&&el.offsetParent!==null){el.scrollIntoView({block:'center'}); el.click(); return true;} return false;",
            tab_id,
        )
        if clicked:
            time.sleep(0.2)
            return True
    except Exception:
        pass

    try:
        if tab_label:
            js = (
                "var lbl=arguments[0]; var as=document.querySelectorAll('ul.quarter-tab-cover a'); "
                "for(var i=0;i<as.length;i++){var t=as[i].textContent.trim(); if(t===lbl){as[i].scrollIntoView({block:'center'}); as[i].click(); return true;}} return false;"
            )
            if driver.execute_script(js, tab_label):
                time.sleep(0.3)
                return True
    except Exception:
        pass

    log("  - tab click failed: all strategies")
    dump_debug(driver, f"tab_click_failed_{tab_id}", extra={"phase": "click_tab", "tab_label": tab_label})
    return False


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
    pairs = [("#srchBgnDe", "#srchEndDe"), ("input[name='srchBgnDe']", "input[name='srchEndDe']")]
    for sel_s, sel_e in pairs:
        try:
            s = driver.find_element(By.CSS_SELECTOR, sel_s)
            e = driver.find_element(By.CSS_SELECTOR, sel_e)
            return (s, e)
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
    dump_debug(driver, "date_inputs_not_found", extra={"phase": "find_date_inputs"})
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
        dump_debug(driver, "date_set_verify_failed", extra={"phase": "set_dates", "want": [s_val, e_val]})
    assert (s_el.get_attribute("value") or "").strip() == s_val
    assert (e_el.get_attribute("value") or "").strip() == e_val


def _click_by_locators(driver, label: str) -> bool:
    locators = [
        (By.XPATH, f"//button[normalize-space()='{label}']"),
        (By.XPATH, f"//a[normalize-space()='{label}']"),
        (By.XPATH, f"//input[@type='button' and @value='{label}']"),
        (By.XPATH, "//*[contains(@onclick,'excel') and (self::a or self::button or self::input)]"),
        (By.XPATH, "//*[@id='excelDown' or @id='btnExcel' or contains(@id,'excel')]"),
    ]
    for by, q in locators:
        try:
            els = driver.find_elements(by, q)
            for el in els:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.05)
                el.click()
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
    dump_debug(driver, f"download_click_failed_{kind}", extra={"phase": "click_download"})
    return False


def wait_download(dldir: Path, before: set, timeout: int) -> Path:
    endt = time.time() + timeout
    while time.time() < endt:
        allf = set(p for p in dldir.glob("*") if p.is_file())
        newf = [p for p in allf - before if not p.name.endswith(".crdownload")]
        if newf:
            return max(newf, key=lambda p: p.stat().st_mtime)
        time.sleep(0.5)
    raise TimeoutError("download not detected within timeout")


# ==================== 전처리/저장 ====================

def _read_excel_first_table(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, engine="openpyxl", header=None, dtype=str).fillna("")
    df = raw.iloc[12:].copy().reset_index(drop=True)
    if df.empty:
        return pd.DataFrame()
    if df.shape[1] >= 1:
        df = df.iloc[:, 1:].copy()
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
                df[col].astype(str)
                .str.replace(r"[^0-9.\-]", "", regex=True)
                .replace({"": np.nan})
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    target_order = [
        "광역", "구", "법정동", "리", "계약년", "계약월", "계약일",
        "시군구", "번지", "본번", "부번", "단지명", "전용면적(㎡)",
        "거래금액(만원)", "동", "층", "매수자", "매도자", "건축년도",
        "도로명", "해제사유발생일", "거래유형", "중개사소재지", "등기일자", "주택유형",
    ]
    cols = list(df.columns)
    ordered = [c for c in target_order if c in cols]
    others = [c for c in cols if c not in ordered]
    return df.reindex(columns=ordered + others)


def _assert_preprocessed(df: pd.DataFrame):
    cols = set(df.columns)
    if "계약년월" in cols:
        raise RuntimeError("전처리 실패: 금지 컬럼 잔존 ['계약년월']")
    for must in ["광역", "구", "법정동", "계약년", "계약월"]:
        if must not in cols:
            raise RuntimeError(f"전처리 실패: 필수 컬럼 누락 {must}")


def preprocess_df(df: pd.DataFrame) -> pd.DataFrame:
    return _reorder_columns(_normalize_numbers(_split_yymm(_split_sigungu(_drop_no_col(df)))))


def save_excel(path: Path, df: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="data")
        ws = writer.sheets["data"]
        from openpyxl.utils import get_column_letter
        for idx, col in enumerate(df.columns, start=1):
            series = df[col]
            try:
                max_len = max([len(str(col))] + [len(str(x)) if x is not None else 0 for x in series.tolist()])
            except Exception:
                max_len = len(str(col))
            width = min(80, max(8, max_len + 2))
            ws.column_dimensions[get_column_letter(idx)].width = width


def save_csv(path: Path, df: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


# ==================== 파이프라인 ====================

PROPERTY_TYPES = ["아파트", "연립다세대", "단독다가구", "오피스텔", "상업업무용", "토지", "공장창고등"]
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


def fetch_and_process(driver: webdriver.Chrome, prop_kind: str, start: date, end: date, outname: str):
    for nav_try in range(1, NAV_RETRY_MAX + 1):
        driver.switch_to.default_content()
        log(f"  - nav{nav_try}: opening page {URL}")
        try:
            driver.get(URL)
        except TimeoutException:
            log(f"  - nav{nav_try}: driver.get timeout -> window.stop()")
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
        except WebDriverException as e:
            log(f"  - nav{nav_try}: driver.get webdriver error: {e}")
            dump_debug(driver, f"driver_get_error_{prop_kind}", extra={"phase": "driver.get", "try": nav_try})
            if nav_try == NAV_RETRY_MAX:
                raise

        time.sleep(0.8)
        log(f"  - nav{nav_try}: clicking tab {prop_kind}")
        ok_tab = click_tab(driver, TAB_IDS.get(prop_kind, "xlsTab1"), tab_label=TAB_TEXT.get(prop_kind))
        if not ok_tab:
            log(f"  - nav{nav_try}: tab click failed, retrying...")
            if nav_try == NAV_RETRY_MAX:
                raise RuntimeError("탭 진입 실패")
            time.sleep(1.2)
            continue

        log(f"  - nav{nav_try}: setting dates {start} ~ {end}")
        try:
            set_dates(driver, start, end)
            log(f"  - nav{nav_try}: dates set OK")
            break
        except Exception as e:
            log(f"  - warn: navigate/tab/set_dates retry ({nav_try}/{NAV_RETRY_MAX}): {e}")
            dump_debug(driver, f"set_dates_failed_{prop_kind}", extra={"phase": "set_dates", "try": nav_try})
            if nav_try == NAV_RETRY_MAX:
                raise
            time.sleep(1.2)

    before = set(p for p in TMP_DIR.glob("*") if p.is_file())
    got = None
    for attempt in range(1, CLICK_RETRY_MAX + 1):
        ok = click_download(driver, "excel")
        log(f"  - [{prop_kind}] click_download(excel) / attempt {attempt}: {ok}")
        if not ok:
            time.sleep(CLICK_RETRY_WAIT)
            continue
        try:
            got = wait_download(TMP_DIR, before, timeout=DOWNLOAD_TIMEOUT)
            break
        except TimeoutError:
            log(f"  - warn: 다운로드 시작 감지 실패(시도 {attempt}/{CLICK_RETRY_MAX})")
            dump_debug(driver, f"download_timeout_{prop_kind}", extra={"phase": "wait_download", "attempt": attempt})
            time.sleep(CLICK_RETRY_WAIT)

    if not got:
        dump_debug(driver, f"download_failed_{prop_kind}", extra={"phase": "download"})
        raise RuntimeError("다운로드 실패")

    log(f"  - got file: {got}  size={got.stat().st_size:,}  ext={got.suffix}")

    df = _read_excel_first_table(got)
    df = preprocess_df(df)
    _assert_preprocessed(df)

    out_xlsx = OUT_DIR / outname
    out_csv = OUT_DIR / (outname[:-5] + ".csv" if outname.lower().endswith(".xlsx") else (outname + ".csv"))

    save_excel(out_xlsx, df)
    save_csv(out_csv, df)

    log(f"완료: [{prop_kind}] {out_xlsx}")
    log(f"완료: [{prop_kind}] {out_csv}")

    upload_processed(out_xlsx, prop_kind)
    upload_processed(out_csv, prop_kind)


def main():
    t = today_kst()
    bases = [shift_months(month_first(t), -i) for i in range(4, -1, -1)]  # 최근 5개월(당월 포함)

    driver = build_driver(TMP_DIR)
    try:
        for prop_kind in PROPERTY_TYPES:
            for base in bases:
                start = base
                end = t if (base.year, base.month) == (t.year, t.month) else (shift_months(base, 1) - timedelta(days=1))
                name = f"{prop_kind} {base:%Y%m}.xlsx"
                log(f"[전국/{prop_kind}] {start} ~ {end} → {name}")
                fetch_and_process(driver, prop_kind, start, end, name)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
