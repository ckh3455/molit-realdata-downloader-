# --- runtime dep bootstrap: install pandas/numpy/openpyxl etc. if missing ---
import sys, subprocess
try:
    import pandas  # noqa: F401
    import numpy   # noqa: F401
    import openpyxl  # noqa: F401
except ModuleNotFoundError:
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--upgrade",
        "pandas", "numpy", "openpyxl",
        "google-api-python-client", "google-auth", "google-auth-httplib2", "google-auth-oauthlib",
        "python-dateutil", "pytz", "tzdata", "et-xmlfile"
    ])
# ---------------------------------------------------------------------------
# 공유드라이브 업로드 개선 — 전처리된 파일을 종목별 폴더에 덮어쓰기
# - 각 종목(아파트, 단독다가구 등)은 동일 이름의 하위 폴더로 분류됨
# - 전처리 후 파일은 해당 폴더에 동일 이름으로 덮어쓰기됨

from pathlib import Path
import pandas as pd
import numpy as np
import json, os, base64
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials

def log(msg):
    print(msg, flush=True)

# 폴더 매핑 (공유드라이브 내부 구조)
FOLDER_MAP = {
    '아파트': '아파트',
    '단독다가구': '단독다가구',
    '연립다세대': '연립다세대',
    '오피스텔': '오피스텔',
    '상업업무용': '상업업무용',
    '토지': '토지',
    '공장창고등': '공장창고등'
}

DRIVE_ROOT_ID = os.getenv('GDRIVE_FOLDER_ID', '').strip()
GDRIVE_BASE_PATH = os.getenv('GDRIVE_BASE_PATH', '').strip()  # 예: "부동산 실거래자료" 또는 "부동산자료/부동산 실거래자료"


def load_sa():
    raw = os.getenv('GCP_SERVICE_ACCOUNT_KEY', '').strip()
    if not raw:
        raise RuntimeError('Service account key missing')
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(base64.b64decode(raw).decode('utf-8'))
    return Credentials.from_service_account_info(data, scopes=['https://www.googleapis.com/auth/drive'])

# ----- 폴더 탐색 유틸(생성하지 않고 '찾기만') -----

def find_child_folder_id(svc, parent_id: str, name: str):
    q = (
        f"name='{name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    resp = (
        svc.files()
        .list(q=q, spaces='drive', fields='files(id,name)', supportsAllDrives=True, includeItemsFromAllDrives=True)
        .execute()
    )
    files = resp.get('files', [])
    return files[0]['id'] if files else None


def resolve_path(svc, start_parent_id: str, path: str):
    current = start_parent_id
    if not path:
        return current
    for seg in [p for p in path.split('/') if p.strip()]:
        found = find_child_folder_id(svc, current, seg.strip())
        if not found:
            return None
        current = found
    return current


def detect_base_parent_id(svc):
    # 1) 환경변수 기준
    if GDRIVE_BASE_PATH:
        bp = resolve_path(svc, DRIVE_ROOT_ID, GDRIVE_BASE_PATH)
        if bp:
            return bp
    # 2) 기본 폴더명 추정
    guess = find_child_folder_id(svc, DRIVE_ROOT_ID, "부동산 실거래자료")
    return guess or DRIVE_ROOT_ID

def upload_processed(file_path: Path, prop_kind: str):
    """전처리된 파일을 기존 공유드라이브 경로로 덮어쓰기 업로드.
    - 폴더는 새로 만들지 않음(경로 없으면 스킵하고 로그 남김).
    경로 규칙: DRIVE_ROOT_ID /(GDRIVE_BASE_PATH 또는 자동탐지)/ <종목폴더>
    """
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

    svc = build('drive', 'v3', credentials=creds, cache_discovery=False)

    # ① 베이스 경로 자동 결정
    base_parent_id = detect_base_parent_id(svc)
    if not base_parent_id:
        log(f"  - drive: skip (base path not found): {GDRIVE_BASE_PATH}")
        return

    # ② 종목 폴더 찾기 (새로 만들지 않음)
    subfolder = FOLDER_MAP.get(prop_kind, prop_kind)
    folder_id = find_child_folder_id(svc, base_parent_id, subfolder)
    if not folder_id:
        log(f"  - drive: skip (category folder missing): {GDRIVE_BASE_PATH or '자동탐지 베이스'}/{subfolder}")
        return

    name = file_path.name
    media = MediaFileUpload(
        file_path.as_posix(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

    # 현재 베이스 경로/루트의 '이름'을 로깅에 포함 (가독성 향상)
    try:
        root_meta = svc.files().get(fileId=DRIVE_ROOT_ID, fields='id,name').execute()
        base_meta = svc.files().get(fileId=base_parent_id, fields='id,name,parents').execute()
        base_name = base_meta.get('name','')
        root_name = root_meta.get('name','')
    except Exception:
        base_name = GDRIVE_BASE_PATH or ''
        root_name = ''

    q = f"name='{name}' and '{folder_id}' in parents and trashed=false"
    resp = svc.files().list(
        q=q, spaces='drive', fields='files(id,name)',
        supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    files = resp.get('files', [])

    # 풀 경로 형태 로그: [루트]/[베이스]/[종목]/파일명
    path_parts = [p for p in [root_name, base_name, subfolder, name] if p]
    full_path_for_log = "/".join(path_parts) if path_parts else f"{subfolder}/{name}"
    log(f"  - drive target: {full_path_for_log} (https://drive.google.com/drive/folders/{folder_id})")

    if files:
        fid = files[0]['id']
        svc.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
        log(f"  - drive: overwritten (update) -> {full_path_for_log}")
    else:
        meta = {'name': name, 'parents': [folder_id]}
        svc.files().create(body=meta, media_body=media, fields='id', supportsAllDrives=True).execute()
        log(f"  - drive: uploaded (create) -> {full_path_for_log}")

# ==================== 아래부터 다운로드/전처리/셀레니움 로직 ====================
import re, time
from datetime import date, timedelta, datetime
from typing import Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="
OUT_DIR = Path(os.getenv("OUT_DIR", "output")).resolve(); OUT_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR = (Path.cwd() / "_rt_downloads").resolve(); TMP_DIR.mkdir(parents=True, exist_ok=True)

IS_CI = os.getenv("CI", "") == "1"
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "30"))
CLICK_RETRY_MAX  = int(os.getenv("CLICK_RETRY_MAX", "15"))
CLICK_RETRY_WAIT = float(os.getenv("CLICK_RETRY_WAIT", "1"))

PROPERTY_TYPES = ["아파트","연립다세대","단독다가구","오피스텔","상업업무용","토지","공장창고등"]
TAB_IDS = {"아파트":"xlsTab1","연립다세대":"xlsTab2","단독다가구":"xlsTab3","오피스텔":"xlsTab4","상업업무용":"xlsTab6","토지":"xlsTab7","공장창고등":"xlsTab8"}
TAB_TEXT = {"아파트":"아파트","연립다세대":"연립/다세대","단독다가구":"단독/다가구","오피스텔":"오피스텔","상업업무용":"상업/업무용","토지":"토지","공장창고등":"공장/창고 등"}

# ---------- 날짜 유틸 ----------

def today_kst() -> date:
    return (datetime.utcnow() + timedelta(hours=9)).date()


def month_first(d: date) -> date:
    return date(d.year, d.month, 1)


def shift_months(d: date, k: int) -> date:
    y = d.year + (d.month - 1 + k) // 12
    m = (d.month - 1 + k) % 12 + 1
    return date(y, m, 1)

# ---------- 크롬 ----------

def build_driver(download_dir: Path) -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu"); opts.add_argument("--disable-notifications"); opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    prefs = {"download.default_directory": str(download_dir), "download.prompt_for_download": False, "download.directory_upgrade": True, "safebrowsing.enabled": True}
    opts.add_experimental_option("prefs", prefs)
    if os.getenv("CHROME_BIN"): opts.binary_location = os.getenv("CHROME_BIN")
    chromedriver_bin = os.getenv("CHROMEDRIVER_BIN")
    if chromedriver_bin and Path(chromedriver_bin).exists():
        service = Service(chromedriver_bin)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    try:
        driver.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior":"allow","downloadPath": str(download_dir),"eventsEnabled": True})
    except Exception:
        pass
    return driver

# ---------- 페이지 조작 ----------

def _try_accept_alert(driver: webdriver.Chrome, wait=1.5) -> bool:
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            Alert(driver).accept(); return True
        except Exception:
            time.sleep(0.15)
    return False


def click_tab(driver: webdriver.Chrome, tab_id: str, wait_sec=12, tab_label: Optional[str]=None) -> bool:
    try:
        WebDriverWait(driver, wait_sec).until(lambda d: d.execute_script("return document.readyState") == "complete")
        WebDriverWait(driver, wait_sec).until(EC.presence_of_element_located((By.CSS_SELECTOR, "ul.quarter-tab-cover")))
    except Exception as e:
        log(f"  - tab container wait failed: {e}"); return False
    # 1) 표준 클릭
    try:
        el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, tab_id)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        driver.execute_script("arguments[0].click();", el); time.sleep(0.2)
        active = driver.execute_script("var e=document.getElementById(arguments[0]);return e&&e.parentElement&&e.parentElement.classList.contains('on');", tab_id)
        if active: return True
    except Exception:
        pass
    # 2) JS 직접 클릭
    try:
        clicked = driver.execute_script("var el=document.getElementById(arguments[0]); if(el&&el.offsetParent!==null){el.scrollIntoView({block:'center'}); el.click(); return true;} return false;", tab_id)
        if clicked:
            time.sleep(0.2)
            active = driver.execute_script("var e=document.getElementById(arguments[0]);return e&&e.parentElement&&e.parentElement.classList.contains('on');", tab_id)
            if active: return True
    except Exception:
        pass
    # 3) 라벨로 매칭
    try:
        lbl = tab_label or next((TAB_TEXT[k] for k,v in TAB_IDS.items() if v==tab_id), None)
        if lbl:
            js = "var lbl=arguments[0]; var as=document.querySelectorAll('ul.quarter-tab-cover a'); for(var i=0;i<as.length;i++){var t=as[i].textContent.trim(); if(t===lbl){as[i].scrollIntoView({block:'center'}); as[i].click(); return true;}} return false;"
            if driver.execute_script(js, lbl):
                time.sleep(0.2); return True
    except Exception:
        pass
    log("  - tab click failed: all strategies"); return False

# ---------- 날짜 입력 찾기/설정 ----------
import re
from typing import Tuple, Optional
from selenium.webdriver.common.keys import Keys


def _looks_like_date_input(el) -> bool:
    typ = (el.get_attribute("type") or "").lower()
    ph  = (el.get_attribute("placeholder") or "").lower()
    val = (el.get_attribute("value") or "").lower()
    name= (el.get_attribute("name") or "").lower()
    id_ = (el.get_attribute("id") or "").lower()
    txt = " ".join([ph, val, name, id_])
    return (
        typ in ("date", "text", "") and (
            re.search(r"\d{4}-\d{2}-\d{2}", ph) or
            re.search(r"\d{4}-\d{2}-\d{2}", val) or
            "yyyy" in ph or "yyyy-mm-dd" in ph or
            any(k in txt for k in ["start","end","from","to","srchbgnde","srchendde"])
        )
    )


def _find_inputs_current_context(driver) -> Optional[Tuple]:
    pairs = [
        ("#srchBgnDe", "#srchEndDe"),
        ("input[name='srchBgnDe']", "input[name='srchEndDe']"),
    ]
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
    raise RuntimeError("날짜 입력 박스를 찾지 못했습니다.")


def _type_and_verify(el, val: str) -> bool:
    try:
        el.click()
        el.send_keys(Keys.CONTROL, 'a')
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
        driver.execute_script("""
            const el = arguments[0], v = arguments[1];
        el.value = v;
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        el.blur();
        """, el, val)
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

# ---------- 다운로드 클릭/대기 ----------

def _click_by_locators(driver, label: str) -> bool:
    locators = [
        (By.XPATH, f"//button[normalize-space()='{label}']"),
        (By.XPATH, f"//a[normalize-space()='{label}']"),
        (By.XPATH, f"//input[@type='button' and @value='{label}']"),
        (By.XPATH, "//*[contains(@onclick,'excel') and (self::a or self::button or self::input)]"),
        (By.XPATH, "//*[@id='excelDown' or @id='btnExcel' or contains(@id,'excel')]")
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
    for fn in ["excelDown","xlsDown","excelDownload","fnExcel","fnExcelDown","fncExcel"]:
        try:
            driver.execute_script(f"if (typeof {fn}==='function') {fn}();")
            _try_accept_alert(driver, 3.0)
            return True
        except Exception:
            continue
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

# ---------- 전처리 ----------
from openpyxl.utils import get_column_letter


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
    for i, name in enumerate(["광역","구","법정동","리"]):
        df[name] = parts[i] if parts.shape[1] > i else ""
    return df.drop(columns=["시군구"])  # 원본 제거


def _split_yymm(df: pd.DataFrame) -> pd.DataFrame:
    if "계약년월" not in df.columns:
        return df
    s = df["계약년월"].astype(str).str.replace(r"\D", "", regex=True)
    df["계약년"] = s.str.slice(0, 4)
    df["계약월"] = s.str.slice(4, 6)
    return df.drop(columns=["계약년월"])  # 원본 제거


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
    cols = list(df.columns)
    left = [c for c in ["광역","구","법정동","리"] if c in cols]
    others = [c for c in cols if c not in left]

    for it in ["계약년","계약월"]:
        if it in others:
            others.remove(it)
    if "계약일" in others:
        idx = others.index("계약일")
        others[idx:idx] = [c for c in ["계약년","계약월"] if c in cols]
    else:
        others = [c for c in ["계약년","계약월"] if c in cols] + others

    new_cols = left + others
    return df.reindex(columns=[c for c in new_cols if c in cols])


def preprocess_df(df: pd.DataFrame) -> pd.DataFrame:
    return _reorder_columns(
        _normalize_numbers(
            _split_yymm(
                _split_sigungu(
                    _drop_no_col(df)
                )
            )
        )
    )


def _assert_preprocessed(df: pd.DataFrame):
    cols = set(df.columns)
    banned = [c for c in ["시군구","계약년월"] if c in cols]
    if banned:
        raise RuntimeError(f"전처리 실패: 금지 컬럼 잔존 {banned}")
    for must in ["광역","구","법정동","계약년","계약월"]:
        if must not in cols:
            raise RuntimeError(f"전처리 실패: 필수 컬럼 누락 {must}")


def save_excel(path: Path, df: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="data")
        ws = writer.sheets["data"]
        for idx, col in enumerate(df.columns, start=1):
            series = df[col]
            try:
                max_len = max(
                    [len(str(col))] +
                    [len(str(x)) if x is not None else 0 for x in series.tolist()]
                )
            except Exception:
                max_len = len(str(col))
            width = min(80, max(8, max_len + 2))
            ws.column_dimensions[get_column_letter(idx)].width = width

# ---------- 파이프라인 ----------

def fetch_and_process(driver: webdriver.Chrome, prop_kind: str, start: date, end: date, outname: str):
    # 진입/세팅(최대 3회 재시도)
    for nav_try in range(1, 4):
        driver.switch_to.default_content()
        driver.get(URL)
        time.sleep(0.6)
        if not click_tab(driver, TAB_IDS.get(prop_kind, "xlsTab1"), tab_label=TAB_TEXT.get(prop_kind)):
            if nav_try == 3:
                raise RuntimeError("탭 진입 실패")
            continue
        try:
            set_dates(driver, start, end)
            break
        except Exception as e:
            log(f"  - warn: navigate/tab/set_dates retry ({nav_try}/3): {e}")
            if nav_try == 3:
                raise
            time.sleep(0.6)

    before = set(p for p in TMP_DIR.glob("*") if p.is_file())
    got = None
    for attempt in range(1, CLICK_RETRY_MAX + 1):
        ok = click_download(driver, "excel")
        log(f"  - [{prop_kind}] click_download(excel) / attempt {attempt}: {ok}")
        if not ok:
            time.sleep(CLICK_RETRY_WAIT)
            if attempt % 5 == 0:
                driver.switch_to.default_content()
                driver.get(URL)
                time.sleep(0.6)
                click_tab(driver, TAB_IDS.get(prop_kind, "xlsTab1"), tab_label=TAB_TEXT.get(prop_kind))
                set_dates(driver, start, end)
            continue
        try:
            got = wait_download(TMP_DIR, before, timeout=DOWNLOAD_TIMEOUT)
            break
        except TimeoutError:
            log(f"  - warn: 다운로드 시작 감지 실패(시도 {attempt}/{CLICK_RETRY_MAX})")
            if attempt % 5 == 0:
                driver.switch_to.default_content()
                driver.get(URL)
                time.sleep(0.6)
                click_tab(driver, TAB_IDS.get(prop_kind, "xlsTab1"), tab_label=TAB_TEXT.get(prop_kind))
                set_dates(driver, start, end)
            continue

    if not got:
        raise RuntimeError("다운로드 실패")
    log(f"  - got file: {got}  size={got.stat().st_size:,}  ext={got.suffix}")

    # 전처리 → 저장 → 업로드
    df = _read_excel_first_table(got)
    df = preprocess_df(df)
    log("  - 헤더(전처리 후): " + " | ".join([str(c) for c in df.columns.tolist()]))
    log(f"  - 행/열 크기: {df.shape[0]} rows × {df.shape[1]} cols")
    _assert_preprocessed(df)
    out = OUT_DIR / outname
    save_excel(out, df)
    log(f"완료: [{prop_kind}] {out}")
    upload_processed(out, prop_kind)

# ---------- 메인 ----------

def main():
    t = today_kst()
    bases = [shift_months(month_first(t), -i) for i in range(2, -1, -1)]  # 최근 3개월(당월 포함)
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
