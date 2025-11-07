# -*- coding: utf-8 -*-
"""
국토부 실거래가 데이터 월별 대량 다운로드
- 재시도 로직 (15초 대기, 최대 3회)
- 진행 상황 저장 및 재개
- 100회 제한 대응 (다음날 자동 재개)
- 업데이트 모드 (최근 1년만 갱신)

파일명: download_realdata.py
"""
import os
import re
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import UnexpectedAlertPresentException, TimeoutException

# Google Drive 업로드 모듈
try:
    from drive_uploader import get_uploader
    DRIVE_UPLOAD_ENABLED = True
except ImportError:
    DRIVE_UPLOAD_ENABLED = False

# ==================== 설정 ====================
IS_CI = os.getenv("CI", "") == "1"

# 저장 폴더 (환경에 따라 자동 전환)
if IS_CI:
    # GitHub Actions: 테스트용 output 폴더
    DOWNLOAD_DIR = Path("output")
else:
    # 로컬 PC: OneDrive 경로
    DOWNLOAD_DIR = Path(r"D:\OneDrive\office work\부동산 실거래 데이터")

# 임시 다운로드 폴더
TEMP_DOWNLOAD_DIR = Path("_temp_downloads")

# 국토부 URL (엑셀 다운로드 페이지)
MOLIT_URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="

# 부동산 종목 (7개)
PROPERTY_TYPES = [
    "아파트",
    "연립다세대",
    "단독다가구",
    "오피스텔",
    "토지",
    "상업업무용",
    "공장창고등"
]

# 섹션별 시작 년도 (데이터가 존재하는 시점)
SECTION_START_YEAR = {
    "아파트": 2006,
    "연립다세대": 2006,
    "단독다가구": 2006,
    "오피스텔": 2006,
    "토지": 2006,
    "상업업무용": 2006,
    "공장창고등": 2006,
}

# 섹션별 시작 월 (데이터가 존재하는 시점, 기본값은 1월)
SECTION_START_MONTH = {
    "아파트": 1,
    "연립다세대": 1,
    "단독다가구": 1,
    "오피스텔": 1,
    "토지": 1,
    "상업업무용": 1,
    "공장창고등": 1,
}

# 실제 페이지의 탭 이름 매핑 (페이지에는 슬래시가 있음)
TAB_NAME_MAPPING = {
    "아파트": "아파트",
    "연립다세대": "연립/다세대",
    "단독다가구": "단독/다가구",
    "오피스텔": "오피스텔",
    "토지": "토지",
    "상업업무용": "상업/업무용",
    "공장창고등": "공장/창고 등",
}

# 탭 ID 매핑
TAB_ID_MAPPING = {
    "아파트": "xlsTab1",
    "연립다세대": "xlsTab2",
    "단독다가구": "xlsTab3",
    "오피스텔": "xlsTab4",
    "상업업무용": "xlsTab6",
    "토지": "xlsTab7",
    "공장창고등": "xlsTab8",
}

# 진행 상황 파일
PROGRESS_FILE = Path("download_progress.json")

# 임시 다운로드 폴더 생성
TEMP_DOWNLOAD_DIR.mkdir(exist_ok=True)

def log(msg: str, end="\n"):
    """로그 출력"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}", end=end, flush=True)

def sanitize_folder_name(name: str) -> str:
    """폴더명에서 특수문자 제거"""
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def build_driver():
    """크롬 드라이버 생성"""
    opts = Options()
    is_ci_env = os.getenv("CI") == "1" or os.getenv("GITHUB_ACTIONS") == "true"
    
    if is_ci_env:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1400,900")
    else:
        opts.add_argument("--start-maximized")
    
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--lang=ko-KR")
    
    if not is_ci_env:
        opts.add_argument("--remote-debugging-port=9222")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option('useAutomationExtension', False)
        log("🔧 Chrome DevTools Protocol 활성화 (포트 9222)")
    
    prefs = {
        "download.default_directory": str(TEMP_DOWNLOAD_DIR.absolute()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "profile.default_content_setting_values.notifications": 2,
        "profile.content_settings.exceptions.automatic_downloads.*.setting": 1,
    }
    opts.add_experimental_option("prefs", prefs)
    
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-infobars")
    
    chromedriver_bin = os.getenv("CHROMEDRIVER_BIN")
    if chromedriver_bin and Path(chromedriver_bin).exists():
        service = Service(chromedriver_bin)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        opts.binary_location = chrome_bin
    
    driver = webdriver.Chrome(service=service, options=opts)
    return driver

def remove_google_translate_popup(driver):
    """Google Translate 팝업 강제 제거/숨김"""
    try:
        driver.execute_script("""
            const selectors = [
                'div[class*="translate"]',
                'div[id*="translate"]',
                'iframe[title*="Translate"]',
                'iframe[src*="translate"]',
                '.goog-te-banner-frame',
                '.goog-te-menu-frame',
                '#google_translate_element',
                '[class*="goog-te"]',
                '[id*="goog-te"]'
            ];
            
            selectors.forEach(selector => {
                try {
                    const elements = document.querySelectorAll(selector);
                    elements.forEach(el => {
                        if (el.tagName === 'IFRAME') {
                            el.style.display = 'none';
                            el.style.visibility = 'hidden';
                            el.style.width = '0';
                            el.style.height = '0';
                        } else {
                            el.remove();
                        }
                    });
                } catch(e) {}
            });
        """)
    except:
        pass

def try_accept_alert(driver, timeout=3.0) -> bool:
    """Alert 자동 수락 - 100건 제한 및 데이터 없음 감지"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            alert = Alert(driver)
            text = alert.text
            log(f"  🔔 Alert: {text}")
            
            if "100건" in text or "100" in text:
                alert.accept()
                log(f"  ⛔ 일일 다운로드 100건 제한 도달!")
                raise Exception("DOWNLOAD_LIMIT_100")
            
            if "데이터가 존재하지 않습니다" in text or "존재하지 않습니다" in text:
                alert.accept()
                log(f"  ℹ️  해당 기간에 데이터가 없습니다.")
                raise Exception("NO_DATA_AVAILABLE")
            
            alert.accept()
            time.sleep(0.5)
            return True
        except Exception as e:
            if str(e) == "DOWNLOAD_LIMIT_100":
                raise
            if str(e) == "NO_DATA_AVAILABLE":
                raise
            time.sleep(0.2)
    return False

def select_property_tab(driver, tab_name: str) -> bool:
    """부동산 종목 탭 선택 - 개선 버전"""
    actual_tab_name = TAB_NAME_MAPPING.get(tab_name, tab_name)
    tab_id = TAB_ID_MAPPING.get(tab_name)
    
    log(f"  탭 선택: {tab_name} (ID: {tab_id})")
    
    # 페이지 로딩 완료 대기
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except:
        pass
    
    # 탭 컨테이너 로딩 대기
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "ul.quarter-tab-cover"))
        )
    except:
        log(f"  ⚠️ 탭 컨테이너 타임아웃")
        return False
    
    time.sleep(1)
    try_accept_alert(driver, 2.0)
    remove_google_translate_popup(driver)
    
    # ID로 탭 클릭
    if tab_id:
        try:
            elem = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, tab_id))
            )
            driver.execute_script("arguments[0].click();", elem)
            time.sleep(2)
            try_accept_alert(driver, 2.0)
            remove_google_translate_popup(driver)
            
            # 날짜 필드 준비 확인
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#srchBgnDe"))
            )
            time.sleep(1)
            
            log(f"  ✅ 탭 선택 완료")
            return True
        except Exception as e:
            log(f"  ❌ 탭 클릭 실패: {e}")
            return False
    
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

def click_excel_download(driver, baseline_files: set = None) -> bool:
    """EXCEL 다운 버튼 클릭"""
    try:
        remove_google_translate_popup(driver)
        
        if baseline_files is None:
            baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        
        try:
            fn_ready = False
            for wait_attempt in range(6):
                fn_ready = driver.execute_script("return typeof fnExcelDown === 'function';")
                if fn_ready:
                    break
                if wait_attempt < 5:
                    time.sleep(0.5)
            
            if fn_ready:
                result = driver.execute_script("""
                    try {
                        if (typeof fnExcelDown === 'function') {
                            fnExcelDown();
                            return {success: true, method: 'fnExcelDown'};
                        }
                        return {success: false, error: 'fnExcelDown not found'};
                    } catch(e) {
                        return {success: false, error: e.toString()};
                    }
                """)
                
                if result and result.get('success'):
                    log(f"  ✅ EXCEL 다운 버튼 클릭")
                    try:
                        alert = Alert(driver)
                        alert_text = alert.text
                        log(f"  🔔 Alert: {alert_text}")
                        
                        if "100건" in alert_text or "100" in alert_text:
                            alert.accept()
                            raise Exception("DOWNLOAD_LIMIT_100")
                        
                        if "데이터가 존재하지 않습니다" in alert_text or "존재하지 않습니다" in alert_text:
                            alert.accept()
                            raise Exception("NO_DATA_AVAILABLE")
                        
                        alert.accept()
                    except Exception as e:
                        if str(e) == "DOWNLOAD_LIMIT_100" or str(e) == "NO_DATA_AVAILABLE":
                            raise
                        pass
                    
                    return True
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                raise
            log(f"  ⚠️  함수 호출 실패: {e}")
        
        try:
            clicked = driver.execute_script("""
                var buttons = document.querySelectorAll('button.ifdata-search-result');
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    if (btn.textContent.trim() === 'EXCEL 다운' && btn.offsetParent !== null) {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        btn.click();
                        return true;
                    }
                }
                return false;
            """)
            
            if clicked:
                log(f"  ✅ 버튼 클릭 완료")
                try_accept_alert(driver, 2.0)
                return True
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                raise
            log(f"  ⚠️  클릭 실패: {e}")
        
        log(f"  ❌ EXCEL 다운 버튼을 찾을 수 없습니다")
        raise Exception("EXCEL 다운 버튼을 찾을 수 없습니다")
        
    except Exception as e:
        if "DOWNLOAD_LIMIT_100" in str(e):
            raise
        if "NO_DATA_AVAILABLE" in str(e):
            raise
        log(f"  ❌ 다운 버튼 클릭 실패: {e}")
        return False

def wait_for_download(timeout: int = 15, baseline_files: set = None, expected_year: int = None, expected_month: int = None, driver=None) -> Optional[Path]:
    """다운로드 완료 대기"""
    start_time = time.time()
    
    if baseline_files is None:
        baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
    
    log(f"  ⏳ 다운로드 대기 중... (최대 {timeout}초)")
    
    last_size = {}
    stable_count = {}
    
    while time.time() - start_time < timeout:
        current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
        
        crdownloads = [f for f in current_files if f.suffix == '.crdownload']
        if crdownloads:
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
                file_key = str(latest)
                
                if file_key in last_size:
                    if last_size[file_key] == size:
                        stable_count[file_key] = stable_count.get(file_key, 0) + 1
                    else:
                        stable_count[file_key] = 0
                        last_size[file_key] = size
                else:
                    last_size[file_key] = size
                    stable_count[file_key] = 0
                
                if stable_count.get(file_key, 0) >= 3:
                    log(f"  ✅ 다운로드 완료: {latest.name} ({size:,} bytes)")
                    return latest
        
        time.sleep(0.2)
    
    log(f"  ⏱️  타임아웃 ({timeout}초)")
    return None

def preprocess_file(file_path: Path) -> Path:
    """파일 전처리"""
    return file_path

def move_and_rename_file(downloaded_file: Path, property_type: str, year: int, month: int) -> Path:
    """다운로드 파일을 목적지로 이동 및 이름 변경"""
    folder_name = sanitize_folder_name(property_type)
    dest_dir = DOWNLOAD_DIR / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    dest_path = dest_dir / filename
    
    if dest_path.exists():
        dest_path.unlink()
        log(f"  🗑️  기존 파일 삭제: {filename}")
    
    downloaded_file.rename(dest_path)
    log(f"  📁 저장: {dest_path}")
    
    try:
        preprocessed_path = preprocess_file(dest_path)
    except Exception as e:
        log(f"  ⚠️  전처리 실패: {e}")
    
    if DRIVE_UPLOAD_ENABLED:
        try:
            log(f"  ☁️  Google Drive 업로드 중...")
            uploader = get_uploader()
            if uploader.init_service():
                uploader.upload_file(dest_path, filename, property_type)
                log(f"  ✅ Google Drive 업로드 완료")
        except Exception as e:
            log(f"  ⚠️  Google Drive 업로드 실패: {e}")
    
    return dest_path

def generate_monthly_dates(start_year: int = 2006, start_month: int = 1) -> List[Tuple[date, date]]:
    """월별 날짜 생성"""
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
            progress = json.load(f)
            if progress:
                return progress
    
    if DRIVE_UPLOAD_ENABLED:
        try:
            log("📂 Google Drive에서 진행 상황 확인 중...")
            uploader = get_uploader()
            if uploader.init_service():
                progress = {}
                today = date.today()
                
                for property_type in PROPERTY_TYPES:
                    prop_key = sanitize_folder_name(property_type)
                    all_months = uploader.get_all_file_months(property_type)
                    
                    if not all_months:
                        continue
                    
                    section_start_year = SECTION_START_YEAR.get(property_type, 2006)
                    section_start_month = SECTION_START_MONTH.get(property_type, 1)
                    expected_months = set()
                    current = date(section_start_year, section_start_month, 1)
                    while current <= today:
                        expected_months.add((current.year, current.month))
                        if current.month == 12:
                            current = date(current.year + 1, 1, 1)
                        else:
                            current = date(current.year, current.month + 1, 1)
                    
                    missing_months = expected_months - all_months
                    
                    if missing_months:
                        oldest_missing = min(missing_months)
                        last_year, last_month = oldest_missing
                        if last_month == 1:
                            completed_year = last_year - 1
                            completed_month = 12
                        else:
                            completed_year = last_year
                            completed_month = last_month - 1
                        month_key = f"{completed_year:04d}{completed_month:02d}"
                        progress[prop_key] = {
                            "last_month": month_key,
                            "last_update": datetime.now().isoformat(),
                            "missing_count": len(missing_months)
                        }
                    else:
                        last_year, last_month = max(all_months)
                        month_key = f"{last_year:04d}{last_month:02d}"
                        progress[prop_key] = {
                            "last_month": month_key,
                            "last_update": datetime.now().isoformat()
                        }
                
                if progress:
                    save_progress(progress)
                    return progress
        except Exception as e:
            log(f"⚠️  Google Drive 확인 실패: {e}")
    
    return {}

def save_progress(progress: dict):
    """진행 상황 저장"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)

def is_already_downloaded(property_type: str, year: int, month: int, update_mode: bool = False) -> bool:
    """이미 다운로드된 파일인지 확인"""
    if update_mode:
        today = date.today()
        months_to_subtract = 2
        if today.month <= months_to_subtract:
            update_start_year = today.year - 1
            update_start_month = today.month + 12 - months_to_subtract
        else:
            update_start_year = today.year
            update_start_month = today.month - months_to_subtract
        
        file_date = date(year, month, 1)
        update_start_date = date(update_start_year, update_start_month, 1)
        if file_date >= update_start_date:
            return False
    
    folder_name = sanitize_folder_name(property_type)
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    dest_path = DOWNLOAD_DIR / folder_name / filename
    
    if dest_path.exists():
        return True
    
    if DRIVE_UPLOAD_ENABLED:
        try:
            uploader = get_uploader()
            if uploader.init_service():
                if uploader.check_file_exists(filename, property_type):
                    return True
        except:
            pass
    
    return False

def download_single_month_with_retry(driver, property_type: str, start_date: date, end_date: date, max_retries: int = 3, update_mode: bool = False) -> bool:
    """단일 월 다운로드 - 재시도 포함"""
    year = start_date.year
    month = start_date.month
    
    log(f"\n{'='*60}")
    log(f"📅 {property_type} {year}년 {month}월")
    log(f"{'='*60}")
    
    if is_already_downloaded(property_type, year, month, update_mode=update_mode):
        log(f"  ⏭️  이미 존재함, 스킵")
        return True
    
    try:
        for old_file in TEMP_DOWNLOAD_DIR.glob("*.xlsx"):
            old_file.unlink()
        for old_file in TEMP_DOWNLOAD_DIR.glob("*.xls"):
            old_file.unlink()
    except Exception as e:
        log(f"  🧹 temp 폴더 정리 실패: {e}")
    
    for attempt in range(1, max_retries + 1):
        log(f"  🔄 시도 {attempt}/{max_retries}")
        
        if not set_dates(driver, start_date, end_date):
            if attempt < max_retries:
                time.sleep(5)
                continue
            return False
        
        try:
            try_accept_alert(driver, 2.0)
        except Exception as e:
            if "NO_DATA_AVAILABLE" in str(e):
                return True
            elif "DOWNLOAD_LIMIT_100" in str(e):
                raise
        
        time.sleep(2.0)
        
        baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        
        try:
            if not click_excel_download(driver, baseline_files=baseline_files):
                if attempt < max_retries:
                    driver.get(MOLIT_URL)
                    time.sleep(3)
                    try_accept_alert(driver, 2.0)
                    if not select_property_tab(driver, property_type):
                        log(f"  ⚠️  탭 재선택 실패")
                    time.sleep(5)
                    continue
                return False
            
            time.sleep(10.0)
            
        except Exception as e:
            if "NO_DATA_AVAILABLE" in str(e):
                return True
            elif "DOWNLOAD_LIMIT_100" in str(e):
                raise
            if attempt < max_retries:
                driver.get(MOLIT_URL)
                time.sleep(8)
                try_accept_alert(driver, 2.0)
                remove_google_translate_popup(driver)
                
                if not select_property_tab(driver, property_type):
                    log(f"  ⚠️  탭 재선택 실패")
                time.sleep(5)
                continue
            return False
        
        downloaded = wait_for_download(timeout=15, baseline_files=baseline_files, expected_year=year, expected_month=month, driver=driver)
        
        if downloaded:
            try:
                move_and_rename_file(downloaded, property_type, year, month)
                
                try:
                    for temp_file in TEMP_DOWNLOAD_DIR.glob("*"):
                        try:
                            if temp_file.is_file():
                                temp_file.unlink()
                        except:
                            pass
                except:
                    pass
                
                time.sleep(1.0)
                return True
            except Exception as e:
                log(f"  ❌ 파일 이동 실패: {e}")
                if attempt < max_retries:
                    driver.get(MOLIT_URL)
                    time.sleep(8)
                    try_accept_alert(driver, 2.0)
                    remove_google_translate_popup(driver)
                    
                    if not select_property_tab(driver, property_type):
                        log(f"  ⚠️  탭 재선택 실패")
                    time.sleep(5)
                    continue
                return False
        else:
            if attempt < max_retries:
                driver.get(MOLIT_URL)
                time.sleep(8)
                try_accept_alert(driver, 2.0)
                remove_google_translate_popup(driver)
                
                if not select_property_tab(driver, property_type):
                    log(f"  ⚠️  탭 재선택 실패")
                time.sleep(5)
            else:
                log(f"  ❌ {max_retries}회 시도 모두 실패")
                return False
    
    return False

def main():
    """메인 함수"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--max-months", type=int, default=2)
    parser.add_argument("--update-mode", action="store_true")
    args = parser.parse_args()
    
    log("="*70)
    log("🚀 국토부 실거래가 데이터 다운로드")
    log("="*70)
    
    progress = load_progress()
    
    today = date.today()
    target_month_key = f"{today.year:04d}{today.month:02d}"
    properties_to_download = []
    
    for property_type in PROPERTY_TYPES:
        prop_key = sanitize_folder_name(property_type)
        last_completed = progress.get(prop_key, {}).get("last_month", "")
        
        if not last_completed or last_completed < target_month_key:
            properties_to_download.append(property_type)
    
    if args.update_mode:
        if not properties_to_download:
            update_mode = True
            properties_to_download = PROPERTY_TYPES
        else:
            update_mode = False
    elif not properties_to_download:
        update_mode = True
        properties_to_download = PROPERTY_TYPES
    else:
        update_mode = False
    
    if update_mode:
        months_to_subtract = 2
        if today.month <= months_to_subtract:
            start_year = today.year - 1
            start_month = today.month + 12 - months_to_subtract
        else:
            start_year = today.year
            start_month = today.month - months_to_subtract
        monthly_dates = generate_monthly_dates(start_year, start_month)
    else:
        monthly_dates = generate_monthly_dates(2006, 1)
    
    if args.test_mode:
        monthly_dates = monthly_dates[-args.max_months:]
    
    driver = build_driver()
    total_success = 0
    total_fail = 0
    
    try:
        log("🌐 사이트 접속 중...")
        driver.get(MOLIT_URL)
        time.sleep(5)
        try_accept_alert(driver, 2.0)
        remove_google_translate_popup(driver)
        
        for prop_idx, property_type in enumerate(properties_to_download, 1):
            log("="*70)
            log(f"📊 [{prop_idx}/{len(properties_to_download)}] {property_type}")
            log("="*70)
            
            if not select_property_tab(driver, property_type):
                log(f"⚠️  탭 선택 실패, 다음 종목으로...")
                continue
            
            prop_key = sanitize_folder_name(property_type)
            last_completed = progress.get(prop_key, {}).get("last_month", "")
            
            if update_mode:
                today = date.today()
                months_to_subtract = 2
                if today.month <= months_to_subtract:
                    start_year = today.year - 1
                    start_month = today.month + 12 - months_to_subtract
                else:
                    start_year = today.year
                    start_month = today.month - months_to_subtract
                section_monthly_dates = generate_monthly_dates(start_year, start_month)
            else:
                if last_completed:
                    last_year = int(last_completed[:4])
                    last_month = int(last_completed[4:6])
                    if last_month == 12:
                        start_year = last_year + 1
                        start_month = 1
                    else:
                        start_year = last_year
                        start_month = last_month + 1
                else:
                    section_start_year = SECTION_START_YEAR.get(property_type, 2006)
                    section_start_month = SECTION_START_MONTH.get(property_type, 1)
                    start_year = section_start_year
                    start_month = section_start_month
                section_monthly_dates = generate_monthly_dates(start_year, start_month)
            
            success_count = 0
            fail_count = 0
            
            for month_idx, (start_date, end_date) in enumerate(section_monthly_dates, 1):
                year = start_date.year
                month = start_date.month
                month_key = f"{year:04d}{month:02d}"
                
                if month_idx > 1:
                    driver.get(MOLIT_URL)
                    time.sleep(8)
                    try_accept_alert(driver, 2.0)
                    remove_google_translate_popup(driver)
                    
                    if not select_property_tab(driver, property_type):
                        log(f"  ⚠️  탭 재선택 실패")
                
                if is_already_downloaded(property_type, year, month, update_mode=update_mode):
                    continue
                
                success = download_single_month_with_retry(driver, property_type, start_date, end_date, max_retries=3, update_mode=update_mode)
                
                if success:
                    success_count += 1
                    
                    if prop_key not in progress:
                        progress[prop_key] = {}
                    progress[prop_key]["last_month"] = month_key
                    progress[prop_key]["last_update"] = datetime.now().isoformat()
                    save_progress(progress)
                else:
                    fail_count += 1
                
                time.sleep(5)
            
            total_success += success_count
            total_fail += fail_count
            
            if args.test_mode:
                break
        
        log("="*70)
        log("🎉 다운로드 완료!")
        log(f"📊 전체 통계: 성공 {total_success}, 실패 {total_fail}")
        log("="*70)
        
    except Exception as e:
        if str(e) == "DOWNLOAD_LIMIT_100":
            log("\n" + "="*70)
            log("⛔ 일일 다운로드 100건 제한 도달")
            log("="*70)
        else:
            log(f"\n❌ 오류 발생: {e}")
    finally:
        try:
            driver.quit()
        except:
            pass

if __name__ == "__main__":
    main()
