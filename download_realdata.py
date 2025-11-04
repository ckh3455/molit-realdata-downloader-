# -*- coding: utf-8 -*-

"""
국토부 실거래가 데이터 월별 대량 다운로드
- 재시도 로직 (15초 대기, 최대 3회)
- 진행 상황 저장 및 재개
- 100회 제한 대응 (다음날 자동 재개)
- 업데이트 모드 (최근 1년만 갱신)
- Microsoft Graph API를 사용한 OneDrive 통합
파일명: download_realdata.py
"""

import os
import re
import sys
import json
import time
import argparse
import stat
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from selenium.common.exceptions import UnexpectedAlertPresentException

# config.py에서 설정 가져오기
import config

# CI 환경에서 OneDrive 클라이언트 초기화
onedrive_client = None
if config.IS_CI:
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")
    tenant_id = os.getenv("AZURE_TENANT_ID")
    
    if client_id and client_secret and tenant_id:
        try:
            from onedrive_client import OneDriveClient
            onedrive_client = OneDriveClient(client_id, client_secret, tenant_id)
            print(f"✅ OneDrive 클라이언트 초기화 완료")
        except Exception as e:
            print(f"⚠️  OneDrive 클라이언트 초기화 실패: {e}")
            onedrive_client = None

# ==================== 설정 (config.py에서 가져옴) ====================

IS_CI = config.IS_CI
DOWNLOAD_DIR = config.DOWNLOAD_DIR
TEMP_DOWNLOAD_DIR = config.TEMP_DOWNLOAD_DIR
MOLIT_URL = config.MOLIT_URL
PROPERTY_TYPES = config.PROPERTY_TYPES

# CI 환경에서 OneDrive 기본 폴더
ONEDRIVE_BASE_FOLDER = os.getenv("ONEDRIVE_BASE_FOLDER", "office work/부동산 실거래 데이터")

# 진행 상황 파일
PROGRESS_FILE = Path("download_progress.json")

# 임시 다운로드 폴더 생성
TEMP_DOWNLOAD_DIR.mkdir(exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

def log(msg: str, end="\n"):
    """로그 출력"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}", end=end, flush=True)

def sanitize_folder_name(name: str) -> str:
    """폴더명에서 특수문자 제거"""
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def upload_to_onedrive(local_path: Path, remote_path: str) -> bool:
    """Microsoft Graph API를 사용하여 OneDrive에 업로드"""
    if not IS_CI or not onedrive_client:
        return True  # 로컬에서는 불필요 (이미 로컬 OneDrive 폴더에 저장됨)
    
    try:
        log(f"  ☁️  OneDrive 업로드 시작: {remote_path}")
        
        # 전체 경로 구성
        full_remote_path = f"{ONEDRIVE_BASE_FOLDER}/{remote_path}"
        
        success = onedrive_client.upload_file(local_path, full_remote_path)
        
        if success:
            log(f"  ✅ OneDrive 업로드 완료")
        else:
            log(f"  ❌ OneDrive 업로드 실패")
        
        return success
            
    except Exception as e:
        log(f"  ❌ 업로드 오류: {e}")
        return False

def sync_progress_to_onedrive() -> bool:
    """진행 상황 파일을 OneDrive에 동기화"""
    if not IS_CI or not onedrive_client:
        return True  # 로컬에서는 불필요
    
    try:
        log("  ☁️  진행 상황 파일 동기화 중...")
        
        remote_path = f"{ONEDRIVE_BASE_FOLDER}/{PROGRESS_FILE.name}"
        success = onedrive_client.upload_file(PROGRESS_FILE, remote_path)
        
        return success
    except Exception as e:
        log(f"  ⚠️  진행 상황 동기화 실패: {e}")
        return False

def download_progress_from_onedrive() -> dict:
    """OneDrive에서 진행 상황 파일 다운로드"""
    if not IS_CI or not onedrive_client:
        return {}
    
    try:
        log("  ☁️  진행 상황 파일 다운로드 중...")
        
        remote_path = f"{ONEDRIVE_BASE_FOLDER}/{PROGRESS_FILE.name}"
        success = onedrive_client.download_file(remote_path, PROGRESS_FILE)
        
        if success and PROGRESS_FILE.exists():
            log("  ✅ 진행 상황 파일 다운로드 완료")
        else:
            log("  ℹ️  진행 상황 파일이 없습니다 (처음 실행)")
    except Exception as e:
        log(f"  ⚠️  진행 상황 다운로드 실패: {e}")
    
    return load_progress()

def list_files_in_onedrive_folder(property_type: str) -> set:
    """OneDrive 폴더의 파일 목록 가져오기"""
    if not IS_CI or not onedrive_client:
        # 로컬 환경에서는 로컬 파일 시스템에서 확인
        folder_name = sanitize_folder_name(property_type)
        folder_path = DOWNLOAD_DIR / folder_name
        if folder_path.exists():
            files = {f.name for f in folder_path.iterdir() if f.is_file()}
            log(f"  📁 로컬에서 {len(files)}개 파일 발견: {property_type}")
            return files
        return set()
    
    try:
        folder_name = sanitize_folder_name(property_type)
        remote_path = f"{ONEDRIVE_BASE_FOLDER}/{folder_name}"
        
        log(f"  📁 OneDrive 폴더 목록 조회: {property_type}")
        files = onedrive_client.list_files(remote_path)
        
        log(f"  📁 OneDrive에서 {len(files)}개 파일 발견: {property_type}")
        if len(files) > 0 and len(files) <= 10:
            log(f"  📋 파일 목록: {list(files)[:10]}")
        
        return files
    except Exception as e:
        log(f"  ⚠️  OneDrive 파일 목록 확인 실패: {e}")
        return set()

def check_file_exists_in_onedrive(property_type: str, year: int, month: int, onedrive_files: set = None) -> bool:
    """OneDrive에서 파일 존재 여부 확인"""
    if not IS_CI or not onedrive_client:
        # 로컬 환경에서는 로컬 파일 시스템에서 확인
        folder_name = sanitize_folder_name(property_type)
        filename = f"{property_type} {year:04d}{month:02d}.xlsx"
        local_path = DOWNLOAD_DIR / folder_name / filename
        return local_path.exists()
    
    # 파일명 생성
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    
    # 파일 목록이 제공된 경우 사용
    if onedrive_files is not None:
        return filename in onedrive_files
    
    # 직접 확인
    try:
        folder_name = sanitize_folder_name(property_type)
        remote_path = f"{ONEDRIVE_BASE_FOLDER}/{folder_name}/{filename}"
        return onedrive_client.file_exists(remote_path)
    except Exception as e:
        log(f"  ⚠️  OneDrive 파일 확인 실패: {e}")
        return False

# ... (나머지 함수들은 동일하므로 생략) ...

def build_driver():
    """크롬 드라이버 생성 - 간소화된 버전"""
    opts = Options()
    if IS_CI:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    
    # 로컬 실행 시 안정성 개선
    if not IS_CI:
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option('useAutomationExtension', False)
    
    # 다운로드 설정
    prefs = {
        "download.default_directory": str(TEMP_DOWNLOAD_DIR.absolute()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    opts.add_experimental_option("prefs", prefs)
    
    # CI 환경
    chromedriver_bin = os.getenv("CHROMEDRIVER_BIN")
    
    if chromedriver_bin and Path(chromedriver_bin).exists():
        driver_path = chromedriver_bin
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        
        try:
            driver_path = ChromeDriverManager().install()
        except Exception as e:
            log(f"  ❌ ChromeDriverManager().install() 실패: {e}")
            raise
        
        driver_path_obj = Path(driver_path)
        
        # 디렉토리인 경우 실행 파일 찾기
        if driver_path_obj.is_dir():
            candidates = [
                driver_path_obj / "chromedriver",
                driver_path_obj / "chromedriver.exe",
            ]
            
            found = False
            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    try:
                        is_executable = os.access(candidate, os.X_OK)
                        if is_executable or candidate.suffix == '.exe':
                            driver_path = str(candidate.absolute())
                            found = True
                            break
                    except:
                        pass
            
            if not found:
                all_files = list(driver_path_obj.iterdir())
                executable_files = []
                
                for f in all_files:
                    if not f.is_file():
                        continue
                    
                    if 'NOTICES' in f.name.upper():
                        continue
                    
                    if f.suffix in ['.txt', '.sh', '.md', '.pdf', '.json']:
                        continue
                    
                    if f.name == "chromedriver" or f.name == "chromedriver.exe":
                        executable_files.insert(0, f)
                        continue
                    
                    if f.name.lower().startswith("chromedriver"):
                        executable_files.append(f)
                        continue
                
                if executable_files:
                    selected = executable_files[0]
                    driver_path = str(selected.absolute())
                    found = True
                else:
                    parent_chromedriver = driver_path_obj.parent / "chromedriver"
                    if parent_chromedriver.exists() and parent_chromedriver.is_file():
                        driver_path = str(parent_chromedriver.absolute())
                        found = True
                    else:
                        raise RuntimeError(f"ChromeDriver executable not found in {driver_path}")
        else:
            if not driver_path_obj.exists():
                raise RuntimeError(f"ChromeDriver not found at {driver_path}")
            
            file_name = driver_path_obj.name
            
            if 'NOTICES' in file_name.upper():
                parent_dir = driver_path_obj.parent
                if parent_dir.exists() and parent_dir.is_dir():
                    try:
                        parent_files = list(parent_dir.iterdir())
                        for item in parent_files:
                            if item.is_file() and 'NOTICES' not in item.name.upper():
                                if item.name == "chromedriver" or item.name.lower().startswith("chromedriver"):
                                    driver_path = str(item.absolute())
                                    driver_path_obj = Path(driver_path)
                                    break
                    except Exception as e:
                        pass
                
                if 'NOTICES' in driver_path_obj.name.upper():
                    raise RuntimeError(f"ChromeDriver path points to NOTICES file: {driver_path}")
            
            driver_path = str(driver_path_obj.absolute())
        
        # 실행 권한 부여 (Linux/Unix - CI 환경)
        if sys.platform != 'win32':
            try:
                current_perms = os.stat(driver_path).st_mode
                os.chmod(driver_path, current_perms | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception as e:
                pass
        
        service = Service(driver_path)
    
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        opts.binary_location = chrome_bin
    
    try:
        driver = webdriver.Chrome(service=service, options=opts)
        log(f"  ✅ Chrome 드라이버 생성 성공")
    except Exception as e:
        log(f"  ❌ Chrome 드라이버 생성 실패: {e}")
        raise
    
    return driver

def try_accept_alert(driver, timeout=3.0) -> bool:
    """Alert 자동 수락 - 100건 제한 감지"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            alert = Alert(driver)
            text = alert.text
            log(f"  🔔 Alert: {text}")
            
            # 100건 제한 감지
            if "100건" in text or "100" in text:
                alert.accept()
                log(f"  ⛔ 일일 다운로드 100건 제한 도달!")
                log(f"  💾 진행 상황이 저장되었습니다.")
                log(f"  ⏰ 내일 다시 실행하면 이어서 진행됩니다.")
                raise Exception("DOWNLOAD_LIMIT_100")
            
            alert.accept()
            time.sleep(0.5)
            return True
        except Exception as e:
            if str(e) == "DOWNLOAD_LIMIT_100":
                raise
            time.sleep(0.2)
    return False

def select_property_tab(driver, tab_name: str) -> bool:
    """부동산 종목 탭 선택 - 강화 버전"""
    log(f"  탭 선택: {tab_name}")
    
    # xls.do 페이지인지 확인
    if "xls.do" not in driver.current_url:
        log(f"  🔄 페이지 로딩...")
        driver.get(MOLIT_URL)
        time.sleep(5)
        try_accept_alert(driver, 2.0)
    
    time.sleep(2)
    
    selectors = [
        f"//ul[@class='quarter-tab-cover']//a[contains(text(), '{tab_name}')]",
        f"//a[contains(text(), '{tab_name}')]",
        f"//a[text()='{tab_name}']"
    ]
    
    for idx, selector in enumerate(selectors, 1):
        try:
            log(f"  🔍 탭 찾기 시도 {idx}/{len(selectors)}")
            elem = driver.find_element(By.XPATH, selector)
            
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            time.sleep(0.5)
            elem.click()
            time.sleep(2)
            try_accept_alert(driver, 2.0)
            
            log(f"  ✅ 탭 선택 완료: {tab_name}")
            return True
        except Exception as e:
            if idx == len(selectors):
                log(f"  ❌ 탭 선택 실패: {e}")
            else:
                log(f"  ⏭️  다음 선택자 시도...")
            continue
    
    return False

def find_date_inputs(driver) -> Tuple[object, object]:
    """시작일/종료일 입력 박스 찾기"""
    try:
        start = driver.find_element(By.CSS_SELECTOR, "#srchBgnDe")
        end = driver.find_element(By.CSS_SELECTOR, "#srchEndDe")
        return start, end
    except:
        pass
    
    try:
        start = driver.find_element(By.CSS_SELECTOR, "input[name='srchBgnDe']")
        end = driver.find_element(By.CSS_SELECTOR, "input[name='srchEndDe']")
        return start, end
    except:
        pass
    
    dates = driver.find_elements(By.CSS_SELECTOR, "input[type='date']")
    if len(dates) >= 2:
        return dates[0], dates[1]
    
    raise RuntimeError("날짜 입력 박스를 찾을 수 없습니다")

def set_dates(driver, start_date: date, end_date: date) -> bool:
    """날짜 입력"""
    try:
        start_el, end_el = find_date_inputs(driver)
        
        start_val = start_date.isoformat()
        end_val = end_date.isoformat()
        
        driver.execute_script("""
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', {bubbles:true}));
            arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
        """, start_el, start_val)
        
        driver.execute_script("""
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('input', {bubbles:true}));
            arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
        """, end_el, end_val)
        
        time.sleep(0.5)
        
        actual_start = start_el.get_attribute("value")
        actual_end = end_el.get_attribute("value")
        
        if actual_start == start_val and actual_end == end_val:
            log(f"  ✅ 날짜 설정: {start_val} ~ {end_val}")
            return True
        else:
            log(f"  ⚠️  날짜 검증 실패: 기대={start_val}~{end_val}, 실제={actual_start}~{actual_end}")
            return False
    except Exception as e:
        log(f"  ❌ 날짜 설정 실패: {e}")
        return False

def click_excel_download(driver) -> bool:
    """EXCEL 다운 버튼 클릭"""
    try:
        btn = driver.find_element(
            By.XPATH,
            "//button[contains(text(), 'EXCEL 다운')]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.3)
        btn.click()
        time.sleep(1.0)
        
        try:
            try_accept_alert(driver, 3.0)
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e):
                raise
        
        log(f"  ✅ EXCEL 다운 버튼 클릭")
        return True
    except Exception as e:
        if "DOWNLOAD_LIMIT_100" in str(e):
            raise
        log(f"  ❌ 다운 버튼 클릭 실패: {e}")
        return False

def wait_for_download(timeout: int = 30, baseline_files: set = None) -> Optional[Path]:
    """다운로드 완료 대기 - 개선된 감지 로직"""
    start_time = time.time()
    
    if baseline_files is None:
        baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
    
    log(f"  ⏳ 다운로드 대기 중... (최대 {timeout}초)")
    log(f"  📁 감시 폴더: {TEMP_DOWNLOAD_DIR.absolute()}")
    log(f"  📊 기존 파일: {len(baseline_files)}개")
    
    found_crdownload = False
    last_check_time = start_time
    
    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)
        current_time = time.time()
        
        if current_time - last_check_time < 0.3:
            time.sleep(0.1)
            continue
        last_check_time = current_time
        
        current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
        
        crdownloads = [f for f in current_files if f.suffix == '.crdownload']
        if crdownloads:
            found_crdownload = True
            if elapsed % 3 == 0 and elapsed > 0:
                sizes = [f.stat().st_size for f in crdownloads]
                log(f"  ⏳ 진행중... ({elapsed}초, {sizes[0]:,} bytes)")
            continue
        
        excel_files = [
            f for f in current_files 
            if f.is_file() 
            and f.suffix.lower() in ['.xls', '.xlsx']
            and f not in baseline_files
        ]
        
        if excel_files:
            latest = max(excel_files, key=lambda p: p.stat().st_mtime)
            size = latest.stat().st_size
            
            if size > 1000:
                time.sleep(0.5)
                new_size = latest.stat().st_size
                
                if new_size == size:
                    log(f"  ✅ 다운로드 완료: {latest.name} ({size:,} bytes)")
                    return latest
                else:
                    if elapsed % 2 == 0:
                        log(f"  📝 파일 쓰기 중... ({new_size:,} bytes)")
    
    log(f"  ⏱️  타임아웃 ({timeout}초)")
    
    all_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
    new_files = [f for f in all_files if f not in baseline_files]
    
    if new_files:
        log(f"  🆕 새 파일 발견: {len(new_files)}개")
        for f in new_files:
            log(f"     - {f.name} ({f.stat().st_size:,} bytes)")
    else:
        log(f"  ⚠️  새 파일 없음 (전체 {len(all_files)}개)")
    
    return None

def move_and_rename_file(downloaded_file: Path, property_type: str, year: int, month: int) -> Path:
    """다운로드 파일을 목적지로 이동 및 이름 변경"""
    folder_name = sanitize_folder_name(property_type)
    dest_dir = DOWNLOAD_DIR / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    dest_path = dest_dir / filename
    
    downloaded_file.rename(dest_path)
    log(f"  📁 저장: {dest_path}")
    
    # CI 환경에서 OneDrive 업로드
    if IS_CI:
        remote_path = f"{folder_name}/{filename}"
        upload_to_onedrive(dest_path, remote_path)
    
    return dest_path

def generate_monthly_dates(start_year: int = 2006, start_month: int = 1) -> List[Tuple[date, date]]:
    """2006년 1월부터 현재까지 월별 (시작일, 종료일) 생성"""
    today = date.today()
    current = date(start_year, start_month, 1)
    dates = []
    
    while current <= today:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        
        last_day = next_month - timedelta(days=1)
        
        if current.year == today.year and current.month == today.month:
            last_day = today
        
        dates.append((current, last_day))
        current = next_month
    
    return dates

def load_progress() -> dict:
    """진행 상황 로드"""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_progress(progress: dict):
    """진행 상황 저장"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)
    
    # CI 환경에서 OneDrive 동기화
    if IS_CI:
        sync_progress_to_onedrive()

def is_already_downloaded(property_type: str, year: int, month: int, onedrive_files: set = None) -> bool:
    """이미 다운로드된 파일인지 확인 - 로컬과 OneDrive 모두 확인"""
    folder_name = sanitize_folder_name(property_type)
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    
    # 로컬 파일 확인 (항상 먼저 확인)
    local_path = DOWNLOAD_DIR / folder_name / filename
    if local_path.exists():
        return True
    
    # CI 환경에서 OneDrive 확인
    if IS_CI:
        return check_file_exists_in_onedrive(property_type, year, month, onedrive_files)
    
    return False

def check_if_all_historical_complete(progress: dict) -> bool:
    """모든 과거 데이터가 완료되었는지 확인 (2006-01 ~ 작년 12월)"""
    last_year = date.today().year - 1
    last_historical_month = f"{last_year}12"
    
    for prop in PROPERTY_TYPES:
        prop_key = sanitize_folder_name(prop)
        last_month = progress.get(prop_key, {}).get("last_month", "")
        
        if not last_month or last_month < last_historical_month:
            return False
    
    return True

def download_single_month_with_retry(driver, property_type: str, start_date: date, end_date: date, max_retries: int = 3, onedrive_files: set = None) -> bool:
    """단일 월 다운로드 - 재시도 포함"""
    year = start_date.year
    month = start_date.month
    
    log(f"\n{'='*60}")
    log(f"📅 {property_type} {year}년 {month}월")
    log(f"{'='*60}")
    
    # 이미 다운로드됨?
    if is_already_downloaded(property_type, year, month, onedrive_files):
        log(f"  ⏭️  이미 존재함, 스킵")
        return True
    
    # temp 폴더 정리
    try:
        for old_file in TEMP_DOWNLOAD_DIR.glob("*.xlsx"):
            old_file.unlink()
        for old_file in TEMP_DOWNLOAD_DIR.glob("*.xls"):
            old_file.unlink()
    except Exception as e:
        log(f"  🧹 temp 폴더 정리 실패: {e}")
    
    # 재시도 로직
    for attempt in range(1, max_retries + 1):
        log(f"  🔄 시도 {attempt}/{max_retries}")
        
        if not set_dates(driver, start_date, end_date):
            if attempt < max_retries:
                log(f"  ⏳ 15초 대기 후 재시도...")
                time.sleep(15)
                continue
            return False
        
        baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        
        try:
            if not click_excel_download(driver):
                if attempt < max_retries:
                    log(f"  ⏳ 15초 대기 후 재시도...")
                    time.sleep(15)
                    continue
                return False
        except Exception as e:
            if "alert" in str(e).lower():
                log(f"  ⚠️  Alert 발생 가능성 감지: {e}")
                try:
                    try_accept_alert(driver, 3.0)
                    continue
                except Exception as alert_e:
                    if str(alert_e) == "DOWNLOAD_LIMIT_100":
                        raise
                    log(f"  ❌ Alert 처리 실패: {alert_e}")
            if attempt < max_retries:
                log(f"  ⏳ 15초 대기 후 재시도...")
                time.sleep(15)
                continue
            return False
        
        downloaded = wait_for_download(timeout=30, baseline_files=baseline_files)
        
        if downloaded:
            try:
                move_and_rename_file(downloaded, property_type, year, month)
                return True
            except Exception as e:
                log(f"  ❌ 파일 이동 실패: {e}")
                if attempt < max_retries:
                    log(f"  ⏳ 15초 대기 후 재시도...")
                    time.sleep(15)
                    continue
                return False
        else:
            if attempt < max_retries:
                log(f"  ⏳ 15초 대기 후 재시도...")
                time.sleep(15)
            else:
                log(f"  ❌ {max_retries}회 시도 모두 실패")
                return False
    
    return False

def main():
    """메인 함수"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-mode", action="store_true", help="테스트 모드")
    parser.add_argument("--max-months", type=int, default=2, help="테스트 모드에서 최대 다운로드 개월 수")
    parser.add_argument("--update-mode", action="store_true", help="업데이트 모드 (최근 1년만)")
    args = parser.parse_args()
    
    log("="*70)
    log("🚀 국토부 실거래가 데이터 다운로드")
    log("="*70)
    log(f"🖥️  실행 환경: {'GitHub Actions (CI)' if IS_CI else '로컬 PC'}")
    log(f"📂 저장 경로: {DOWNLOAD_DIR}")
    log(f"📊 종목 수: {len(PROPERTY_TYPES)}")
    if args.test_mode:
        log(f"🧪 테스트 모드: 최근 {args.max_months}개월")
    log("")
    
    # 진행 상황 로드
    if IS_CI:
        progress = download_progress_from_onedrive()
    else:
        progress = load_progress()
    
    # 모드 결정
    if args.update_mode:
        update_mode = True
        log("🔄 업데이트 모드: 최근 1년치만 갱신")
    else:
        update_mode = check_if_all_historical_complete(progress)
        if update_mode:
            log("✅ 과거 데이터 완료 확인")
            log("🔄 업데이트 모드로 전환: 최근 1년치만 갱신")
        else:
            log("📥 전체 다운로드 모드: 2006-01부터 현재까지")
    
    log("")
    
    # 날짜 범위 생성
    if update_mode:
        today = date.today()
        start_year = today.year - 1
        start_month = today.month
        monthly_dates = generate_monthly_dates(start_year, start_month)
        log(f"📅 다운로드 기간: {start_year}-{start_month:02d} ~ {today.strftime('%Y-%m')} ({len(monthly_dates)}개월)")
    else:
        monthly_dates = generate_monthly_dates(2006, 1)
        log(f"📅 다운로드 기간: 2006-01 ~ {date.today().strftime('%Y-%m')} ({len(monthly_dates)}개월)")
    
    if args.test_mode:
        monthly_dates = monthly_dates[-args.max_months:]
        log(f"🧪 테스트 모드: 최근 {len(monthly_dates)}개월만")
    
    log("")
    
    driver = build_driver()
    
    try:
        log("🌐 사이트 접속 중...")
        driver.get(MOLIT_URL)
        time.sleep(5)
        try_accept_alert(driver, 2.0)
        log(f"✅ 접속 완료: {driver.current_url}\n")
        
        log(f"📄 페이지 제목: {driver.title}")
        log("")
        
        total_success = 0
        total_fail = 0
        
        for prop_idx, property_type in enumerate(PROPERTY_TYPES, 1):
            log("="*70)
            log(f"📊 [{prop_idx}/{len(PROPERTY_TYPES)}] {property_type}")
            log("="*70)
            
            # 파일 목록 가져오기 (로컬 또는 OneDrive)
            onedrive_files = None
            if IS_CI:
                onedrive_files = list_files_in_onedrive_folder(property_type)
            elif not IS_CI:
                # 로컬 환경에서는 로컬 파일 시스템에서 확인
                onedrive_files = list_files_in_onedrive_folder(property_type)
            
            # 탭 선택
            if not select_property_tab(driver, property_type):
                log(f"⚠️  탭 선택 실패, 다음 종목으로...")
                continue
            
            # 진행 상황 확인
            prop_key = sanitize_folder_name(property_type)
            last_completed = progress.get(prop_key, {}).get("last_month", "")
            
            if last_completed:
                log(f"📌 마지막 완료: {last_completed}")
                log(f"🔄 이어서 진행합니다...")
            else:
                log(f"🆕 처음 시작합니다")
            
            # 각 월별로
            success_count = 0
            fail_count = 0
            consecutive_fails = 0
            skipped_count = 0
            
            for month_idx, (start_date, end_date) in enumerate(monthly_dates, 1):
                year = start_date.year
                month = start_date.month
                month_key = f"{year:04d}{month:02d}"
                
                if last_completed and month_key <= last_completed:
                    skipped_count += 1
                    if skipped_count == 1:
                        log(f"\n⏭️  이미 완료된 월들을 건너뜁니다...")
                    continue
                
                log(f"\n[{month_idx}/{len(monthly_dates)}]", end=" ")
                
                try:
                    success = download_single_month_with_retry(driver, property_type, start_date, end_date, max_retries=3, onedrive_files=onedrive_files)
                except Exception as e:
                    if str(e) == "DOWNLOAD_LIMIT_100":
                        raise
                    log(f"❌ 예외 발생: {e}")
                    success = False
                
                if success:
                    success_count += 1
                    consecutive_fails = 0
                    
                    if prop_key not in progress:
                        progress[prop_key] = {}
                    progress[prop_key]["last_month"] = month_key
                    progress[prop_key]["last_update"] = datetime.now().isoformat()
                    save_progress(progress)
                else:
                    fail_count += 1
                    consecutive_fails += 1
                    log(f"⚠️  실패 카운트: {fail_count} (연속: {consecutive_fails})")
                    
                    if consecutive_fails >= 3:
                        log(f"\n⛔ 연속 {consecutive_fails}회 실패 - 다운로드 제한 가능성")
                        log(f"💾 진행 상황 저장됨: {PROGRESS_FILE}")
                        log(f"📌 다음 실행시 {month_key}부터 재개됩니다")
                        log(f"⏰ 100회 제한일 경우 내일 다시 실행하세요")
                        driver.quit()
                        return
                
                time.sleep(2)
            
            log(f"\n✅ {property_type} 완료")
            log(f"   성공: {success_count}, 실패: {fail_count}, 스킵: {skipped_count}")
            total_success += success_count
            total_fail += fail_count
            
            if args.test_mode:
                log("\n🧪 테스트 모드 - 첫 종목만 완료")
                break
            
            log("")
        
        log("="*70)
        log("🎉 다운로드 완료!")
        log(f"📊 전체 통계: 성공 {total_success}, 실패 {total_fail}")
        log("="*70)
        
    except Exception as e:
        if str(e) == "DOWNLOAD_LIMIT_100":
            log("\n" + "="*70)
            log("⛔ 일일 다운로드 100건 제한 도달")
            log("="*70)
            log(f"📊 오늘 통계: 성공 {total_success}, 실패 {total_fail}")
            log(f"💾 진행 상황 저장됨: {PROGRESS_FILE}")
            log("⏰ 내일 같은 명령어로 실행하면 이어서 진행됩니다.")
            log("="*70)
        elif isinstance(e, KeyboardInterrupt):
            log("\n⚠️  사용자 중단")
            log(f"💾 진행 상황 저장됨: {PROGRESS_FILE}")
        else:
            log(f"\n❌ 오류 발생: {e}")
            import traceback
            traceback.print_exc()
    finally:
        try:
            driver.quit()
            log("✅ 드라이버 종료")
        except:
            pass

if __name__ == "__main__":
    main()
