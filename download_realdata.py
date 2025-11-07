# -*- coding: utf-8 -*-
"""
국토부 실거래가 Excel 자동화 — 최근 3개월(당월 포함), 공유드라이브 덮어쓰기 업로드
- 탭 고정 ID 1회 클릭 → 날짜 입력 → 다운로드 감지 → 전처리 → 저장 → Drive 덮어쓰기
- 파일명: "{종목} YYYYMM.xlsx" (예: 아파트 202509.xlsx)
- GitHub Secrets 자동 인식: GCP_SERVICE_ACCOUNT_KEY / GDRIVE_FOLDER_ID
- 아티팩트 모드(ARTIFACTS_ONLY=1)일 땐 Drive 업로드 스킵
"""

from __future__ import annotations

import os, re, sys, time, json, shutil, tempfile, base64, subprocess
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

# --- 런타임 의존성 부트스트랩 ---
try:
    import pandas as pd  # type: ignore
    import numpy as np  # type: ignore
    import openpyxl  # type: ignore
except ModuleNotFoundError:
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--upgrade",
        "pandas", "numpy", "openpyxl", "python-dateutil", "pytz", "tzdata"
    ])
    import pandas as pd  # type: ignore
    import numpy as np  # type: ignore
    import openpyxl  # type: ignore

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------- 상수/환경 ----------
URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="
TMP_DL = (Path.cwd() / "_rt_downloads").resolve(); TMP_DL.mkdir(exist_ok=True)
OUT = Path(os.getenv("OUT_DIR", "output")).resolve(); OUT.mkdir(exist_ok=True)

PROP_KIND = os.getenv("PROP_KIND", "아파트").strip()
ARTIFACTS_ONLY = os.getenv("ARTIFACTS_ONLY", "") == "1"
DRIVE_FOLDER_ID = (os.getenv("DRIVE_FOLDER_ID") or os.getenv("GDRIVE_FOLDER_ID") or "").strip()

# 탭 고정 ID
TAB_IDS = {
    "아파트": "xlsTab1",
    "연립다세대": "xlsTab2",
    "단독다가구": "xlsTab3",
    "오피스텔": "xlsTab4",
    # 5 비어 있음
    "상업업무용": "xlsTab6",
    "토지": "xlsTab7",
    "공장창고등": "xlsTab8",
}

# ---------- 유틸 ----------

def log(msg: str):
    print(msg, flush=True)

def today_kst() -> date:
    return (datetime.utcnow() + timedelta(hours=9)).date()

def month_first(d: date) -> date:
    return date(d.year, d.month, 1)

def shift_months(d: date, k: int) -> date:
    y = d.year + (d.month - 1 + k) // 12
    m = (d.month - 1 + k) % 12 + 1
    return date(y, m, 1)

# ---------- Chrome ----------

def build_driver(download_dir: Path) -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu"); opts.add_argument("--disable-notifications"); opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    prefs = {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    opts.add_experimental_option("prefs", prefs)

    chromedriver_bin = os.getenv("CHROMEDRIVER_BIN")
    if chromedriver_bin and Path(chromedriver_bin).exists():
        service = Service(chromedriver_bin)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)

    try:
        driver.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(download_dir), "eventsEnabled": True})
    except Exception:
        pass
    return driver

# ---------- 페이지 조작 ----------

def _try_alert(driver: webdriver.Chrome, wait=1.5):
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            Alert(driver).accept(); return True
        except Exception:
            time.sleep(0.2)
    return False

def click_tab(driver: webdriver.Chrome, tab_id: str, wait_sec=12) -> bool:
    try:
        WebDriverWait(driver, wait_sec).until(lambda d: d.execute_script("return document.readyState") == "complete")
        el = WebDriverWait(driver, wait_sec).until(EC.element_to_be_clickable((By.ID, tab_id)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        driver.execute_script("arguments[0].click();", el)
        time.sleep(0.3)
        active = driver.execute_script("var e=document.getElementById(arguments[0]);return e&&e.parentElement&&e.parentElement.className.includes('on');", tab_id)
        if not active:
            el.click(); time.sleep(0.2)
        return True
    except Exception as e:
        log(f"  - tab click failed: {e}"); return False


def _looks_like_date_input(el) -> bool:
    typ = (el.get_attribute("type") or "").lower(); ph=(el.get_attribute("placeholder") or "").lower()
    val=(el.get_attribute("value") or "").lower(); nm=(el.get_attribute("name") or "").lower(); i=(el.get_attribute("id") or "").lower()
    txt = " ".join([ph,val,nm,i])
    return (typ in ("date","text","") and (re.search(r"\d{4}-\d{2}-\d{2}", ph) or re.search(r"\d{4}-\d{2}-\d{2}", val) or "yyyy" in ph or "yyyy-mm-dd" in ph or any(k in txt for k in ["srchbgnde","srchendde","start","end"])) )


def _find_inputs_ctx(driver) -> Optional[Tuple]:
    # 명시적 선택자 먼저
    for s,e in [("#srchBgnDe","#srchEndDe"),("input[name='srchBgnDe']","input[name='srchEndDe']")]:
        try:
            return driver.find_element(By.CSS_SELECTOR,s), driver.find_element(By.CSS_SELECTOR,e)
        except Exception:
            pass
    # 휴리스틱
    ins = driver.find_elements(By.CSS_SELECTOR, "input")
    cands=[el for el in ins if _looks_like_date_input(el)]
    if len(cands)>=2: return cands[0], cands[1]
    dates=[e for e in ins if (e.get_attribute("type") or "").lower()=="date"]
    if len(dates)>=2: return dates[0], dates[1]
    texts=[e for e in ins if (e.get_attribute("type") or "").lower() in ("text","")]
    if len(texts)>=2: return texts[0], texts[1]
    return None


def find_date_inputs(driver: webdriver.Chrome) -> Tuple:
    driver.switch_to.default_content(); _try_alert(driver,1.0)
    pair = _find_inputs_ctx(driver)
    if pair: return pair
    # 프레임도 탐색
    for fr in driver.find_elements(By.CSS_SELECTOR, "iframe,frame"):
        try:
            driver.switch_to.default_content(); driver.switch_to.frame(fr)
            pair = _find_inputs_ctx(driver)
            if pair: return pair
        except Exception:
            pass
    driver.switch_to.default_content();
    raise RuntimeError("날짜 입력 박스를 찾지 못했습니다.")


def _type_and_verify(el, val: str) -> bool:
    try:
        el.click(); el.send_keys(Keys.CONTROL, "a"); el.send_keys(Keys.DELETE); el.send_keys(val); time.sleep(0.2); el.send_keys(Keys.TAB); time.sleep(0.2)
        return (el.get_attribute("value") or "").strip()==val
    except Exception:
        return False


def _ensure_value_with_js(driver, el, val: str) -> bool:
    try:
        driver.execute_script("const e=arguments[0],v=arguments[1];e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));e.blur();", el, val)
        time.sleep(0.2); return (el.get_attribute("value") or "").strip()==val
    except Exception:
        return False


def set_dates(driver: webdriver.Chrome, start: date, end: date):
    _try_alert(driver,1.0)
    s,e = find_date_inputs(driver)
    sv, ev = start.isoformat(), end.isoformat()
    ok1 = _type_and_verify(s, sv) or _ensure_value_with_js(driver, s, sv)
    ok2 = _type_and_verify(e, ev) or _ensure_value_with_js(driver, e, ev)
    if not (ok1 and ok2):
        log(f"  - warn: date verify failed → want=({sv},{ev}) got=({s.get_attribute('value')},{e.get_attribute('value')})")
    assert (s.get_attribute('value') or '').strip()==sv
    assert (e.get_attribute('value') or '').strip()==ev


def _click_by_locators(driver: webdriver.Chrome, label: str) -> bool:
    locs=[
        (By.XPATH, f"//button[normalize-space()='{label}']"),
        (By.XPATH, f"//a[normalize-space()='{label}']"),
        (By.XPATH, f"//input[@type='button' and @value='{label}']"),
        (By.XPATH, "//*[@id='excelDown' or @id='btnExcel' or contains(@id,'excel')]")
    ]
    for by,q in locs:
        try:
            for el in driver.find_elements(by,q):
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el); time.sleep(0.1); el.click(); _try_alert(driver,3.0); return True
        except Exception:
            pass
    return False


def click_download(driver: webdriver.Chrome) -> bool:
    if _click_by_locators(driver, "EXCEL 다운"): return True
    for fn in ["excelDown","xlsDown","excelDownload","fnExcel","fnExcelDown","fncExcel"]:
        try:
            driver.execute_script(f"if (typeof {fn}==='function') {fn}();"); _try_alert(driver,3.0); return True
        except Exception:
            pass
    return False


def wait_download(dldir: Path, before: set, timeout: int=30) -> Path:
    endt=time.time()+timeout
    while time.time()<endt:
        allf=set(p for p in dldir.glob('*') if p.is_file())
        newf=[p for p in allf-before if not p.name.endswith('.crdownload')]
        if newf:
            return max(newf, key=lambda p: p.stat().st_mtime)
        time.sleep(1.0)
    raise TimeoutError("download not detected within timeout")

# ---------- 전처리 ----------

def _read_excel_first_table(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, engine="openpyxl", dtype=str).fillna("")
    hdr=None
    for i,row in df.iterrows():
        up=[str(x).strip().upper() for x in row.tolist()]
        if "NO" in up or "계약년월" in up: hdr=i; break
    if hdr is None:
        df.columns=df.iloc[0].astype(str).str.strip(); df=df.iloc[1:].copy()
    else:
        df.columns=df.iloc[hdr].astype(str).str.strip(); df=df.iloc[hdr+1:].copy()
    df=df.loc[:,[c for c in df.columns if str(c).strip()!=""]]
    return df.reset_index(drop=True)


def _drop_no_col(df: pd.DataFrame) -> pd.DataFrame:
    for c in list(df.columns):
        if str(c).strip().upper()=="NO":
            df=df[df[c].astype(str).str.strip()!=""]; df=df.drop(columns=[c]); break
    return df

def _split_sigungu(df: pd.DataFrame) -> pd.DataFrame:
    if "시군구" not in df.columns: return df
    parts=df["시군구"].astype(str).str.split(expand=True, n=3)
    cols=["광역","구","법정동","리"]
    for i,name in enumerate(cols): df[name]=parts[i] if parts.shape[1]>i else ""
    return df.drop(columns=["시군구"])

def _split_yymm(df: pd.DataFrame) -> pd.DataFrame:
    if "계약년월" not in df.columns: return df
    s=df["계약년월"].astype(str).str.replace(r"\D","",regex=True)
    df["계약년"]=s.str.slice(0,4); df["계약월"]=s.str.slice(4,6)
    return df.drop(columns=["계약년월"])

def _normalize_numbers(df: pd.DataFrame) -> pd.DataFrame:
    for c in ["거래금액(만원)","전용면적(㎡)"]:
        if c in df.columns:
            df[c]=(df[c].astype(str).str.replace(r"[^0-9.\-]","",regex=True).replace({"":np.nan}))
            df[c]=pd.to_numeric(df[c], errors="coerce")
    return df

def _reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols=list(df.columns)
    left=[c for c in ["광역","구","법정동","리"] if c in cols]
    others=[c for c in cols if c not in left]
    for it in ["계약년","계약월"]:
        if it in others: others.remove(it)
    if "계약일" in others:
        idx=others.index("계약일"); others[idx:idx]=[c for c in ["계약년","계약월"] if c in cols]
    else:
        others=[c for c in ["계약년","계약월"] if c in cols]+others
    return df.reindex(columns=[c for c in left+others if c in cols])

def preprocess_df(df: pd.DataFrame) -> pd.DataFrame:
    return _reorder_columns(_normalize_numbers(_split_yymm(_split_sigungu(_drop_no_col(df)))))

# ---------- Drive 업로드(덮어쓰기) ----------

def load_sa_credentials(sa_path: Path):
    try:
        from google.oauth2.service_account import Credentials
        scopes=["https://www.googleapis.com/auth/drive","https://www.googleapis.com/auth/spreadsheets"]
        # 1) GCP_SERVICE_ACCOUNT_KEY 시크릿 자동 인식(원문/BASE64 모두 지원)
        raw=os.getenv("GCP_SERVICE_ACCOUNT_KEY"," ").strip()
        if raw:
            try:
                data=json.loads(raw)
            except json.JSONDecodeError:
                data=json.loads(base64.b64decode(raw).decode("utf-8"))
            log("  - SA loaded from GCP_SERVICE_ACCOUNT_KEY"); return Credentials.from_service_account_info(data, scopes=scopes)
        # 2) 파일/기타 변수
        if sa_path.exists():
            data=json.loads(sa_path.read_text(encoding="utf-8")); return Credentials.from_service_account_info(data, scopes=scopes)
        sa_json=os.getenv("SA_JSON","" ).strip()
        if sa_json:
            return Credentials.from_service_account_info(json.loads(sa_json), scopes=scopes)
        sa_b64=os.getenv("SA_JSON_BASE64","" ).strip()
        if sa_b64:
            return Credentials.from_service_account_info(json.loads(base64.b64decode(sa_b64).decode("utf-8")), scopes=scopes)
        log("  ! service account not provided (no file/env).")
        return None
    except Exception as e:
        log(f"  ! service account load failed: {e}"); return None


def drive_upload_and_cleanup(creds, file_path: Path):
    if ARTIFACTS_ONLY or not creds or not DRIVE_FOLDER_ID:
        log("  - skip Drive upload (Artifacts mode or missing creds/folder)." ); return
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        svc=build("drive","v3",credentials=creds, cache_discovery=False)
        name=file_path.name
        media=MediaFileUpload(file_path.as_posix(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", resumable=False)
        # 같은 이름 탐색
        q=f"name='{name}' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        resp=svc.files().list(q=q, spaces="drive", fields="files(id,name)", includeItemsFromAllDrives=True, supportsAllDrives=True).execute()
        files=resp.get("files",[])
        if files:
            fid=files[0]["id"]
            svc.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
            log(f"  - drive: overwritten (update) → {name}")
            for dup in files[1:]:
                try: svc.files().delete(fileId=dup["id"]).execute(); log(f"  - drive: removed duplicate → {dup['name']}")
                except Exception: pass
        else:
            meta={"name":name, "parents":[DRIVE_FOLDER_ID]}
            svc.files().create(body=meta, media_body=media, fields="id,name", supportsAllDrives=True).execute()
            log(f"  - drive: uploaded (create) → {name}")
    except Exception as e:
        log(f"  ! drive error: {e}")

# ---------- 다운로드 + 전처리 파이프라인 ----------

def fetch_and_process(driver: webdriver.Chrome, start: date, end: date, outname: str, creds) -> None:
    # 네비게이션 + 탭 1회 선택 + 날짜 입력 (최대 3회 재시도)
    for nav_try in range(1,4):
        driver.switch_to.default_content(); driver.get(URL); time.sleep(0.8)
        try:
            click_tab(driver, TAB_IDS.get(PROP_KIND, "xlsTab1"))
            set_dates(driver, start, end)
            break
        except Exception as e:
            if nav_try==3: raise
            log(f"  - warn: navigate/tab/set_dates retry ({nav_try}/3): {e}")
            time.sleep(0.8)

    # 다운로드 시도
    got_file=None
    for attempt in range(1,16):
        before=set(p for p in TMP_DL.glob('*') if p.is_file())
        ok=click_download(driver)
        log(f"  - click_download(excel) / attempt {attempt}: {ok}")
        if not ok:
            time.sleep(1.0)
            if attempt%5==0:
                driver.switch_to.default_content(); driver.get(URL); time.sleep(0.8)
                click_tab(driver, TAB_IDS.get(PROP_KIND, "xlsTab1")); set_dates(driver, start, end)
            continue
        try:
            got=wait_download(TMP_DL, before, timeout=30)
            got_file=got; log(f"  - got file: {got_file}  size={got_file.stat().st_size:,}  ext={got_file.suffix}")
            break
        except TimeoutError:
            log(f"  - warn: 다운로드 시작 감지 실패(시도 {attempt}/15)")
            if attempt%5==0:
                driver.switch_to.default_content(); driver.get(URL); time.sleep(0.8)
                click_tab(driver, TAB_IDS.get(PROP_KIND, "xlsTab1")); set_dates(driver, start, end)
            continue

    if not got_file:
        # 월초 1일 데이터 없음 케이스는 빈 파일 저장
        tk=today_kst();
        if tk.day==1 and start==end==tk and start==month_first(tk):
            out=OUT/outname
            note=pd.DataFrame([{ "메시지":"자료 없음(월초)", "기간":f"{start}~{end}", "기준일":tk.isoformat()}])
            with pd.ExcelWriter(out, engine="openpyxl") as w: note.to_excel(w, index=False, sheet_name="data")
            log(f"  - no data for first day of month; wrote empty: {out}")
            drive_upload_and_cleanup(creds, out); return
        raise RuntimeError("다운로드 시작 감지 실패(최대 시도 초과)")

    # 전처리 → 저장 → 업로드
    df=_read_excel_first_table(got_file)
    df=preprocess_df(df)
    out=OUT/outname
    with pd.ExcelWriter(out, engine="openpyxl") as w: df.to_excel(w, index=False, sheet_name="data")
    log(f"완료: {out}")
    drive_upload_and_cleanup(creds, out)

# ---------- 메인 ----------

def main():
    # SA 로드
    sa_path=Path(os.getenv("SA_PATH","sa.json"))
    creds=load_sa_credentials(sa_path)

    # 최근 3개월(당월 포함)
    t=today_kst()
    bases=[shift_months(month_first(t), -i) for i in range(2,-1,-1)]

    driver=build_driver(TMP_DL)
    try:
        for base in bases:
            start=base
            end = t if (base.year,base.month)==(t.year,t.month) else (shift_months(base,1)-timedelta(days=1))
            name=f"{PROP_KIND} {base:%Y%m}.xlsx"
            log(f"[전국/{PROP_KIND}] {start} ~ {end} → {name}")
            fetch_and_process(driver, start, end, name, creds)
    finally:
        try: driver.quit()
        except Exception: pass

if __name__ == "__main__":
    main()
