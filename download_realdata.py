# -*- coding: utf-8 -*-
"""
국토부 실거래가 데이터 월별 대량 다운로드
- 재시도 로직 (15초 대기, 최대 3회)
- 진행 상황 저장 및 재개
- 100회 제한 대응 (다음날 자동 재개)
- 업데이트 모드 (최근 1년만 갱신)
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
from selenium.common.exceptions import UnexpectedAlertPresentException

# ==================== 설정 ====================
# 저장 폴더 (OneDrive 경로)
DOWNLOAD_DIR = Path(r"D:\OneDrive\office work\부동산 실거래 데이터")

# 임시 다운로드 폴더
TEMP_DOWNLOAD_DIR = Path("_temp_downloads")

# 국토부 URL
MOLIT_URL = "https://rt.molit.go.kr/new/gis/srh.do?menuGubun=A&xls=xls.do"

# 부동산 종목 (8개)
PROPERTY_TYPES = [
    "아파트",
    "연립다세대",
    "단독다가구",
    "오피스텔",
    "토지",
    "상업업무용",
    "분양권",
    "입주권"
]

# 진행 상황 파일
PROGRESS_FILE = Path("download_progress.json")

# 임시 다운로드 폴더 생성
TEMP_DOWNLOAD_DIR.mkdir(exist_ok=True)

IS_CI = os.getenv("CI", "") == "1"


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
    if IS_CI:
        opts.add_argument("--headless=new")
    
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    
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
        service = Service(chromedriver_bin)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        opts.binary_location = chrome_bin
    
    driver = webdriver.Chrome(service=service, options=opts)
    return driver


def try_accept_alert(driver, timeout=3.0) -> bool:
    """Alert 자동 수락"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            alert = Alert(driver)
            text = alert.text
            log(f"  🔔 Alert: {text}")
            alert.accept()
            time.sleep(0.5)
            return True
        except:
            time.sleep(0.2)
    return False


def select_property_tab(driver, tab_name: str) -> bool:
    """부동산 종목 탭 선택"""
    log(f"  탭 선택: {tab_name}")
    
    # xls.do 페이지인지 확인
    if "xls.do" not in driver.current_url:
        driver.get(MOLIT_URL)
        time.sleep(2)
        try_accept_alert(driver, 2.0)
    
    # quarter-tab-cover 내부 탭 클릭
    try:
        elem = driver.find_element(
            By.XPATH, 
            f"//ul[@class='quarter-tab-cover']//a[contains(text(), '{tab_name}')]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
        time.sleep(0.3)
        elem.click()
        time.sleep(1.5)
        try_accept_alert(driver, 2.0)
        log(f"  ✅ 탭 선택 완료: {tab_name}")
        return True
    except Exception as e:
        log(f"  ❌ 탭 선택 실패: {e}")
        return False


def find_date_inputs(driver) -> Tuple[object, object]:
    """시작일/종료일 입력 박스 찾기"""
    # 명시적 ID 우선
    try:
        start = driver.find_element(By.CSS_SELECTOR, "#srchBgnDe")
        end = driver.find_element(By.CSS_SELECTOR, "#srchEndDe")
        return start, end
    except:
        pass
    
    # name 속성
    try:
        start = driver.find_element(By.CSS_SELECTOR, "input[name='srchBgnDe']")
        end = driver.find_element(By.CSS_SELECTOR, "input[name='srchEndDe']")
        return start, end
    except:
        pass
    
    # type=date
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
        
        # JavaScript로 강제 입력
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
        
        # 검증
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
        try_accept_alert(driver, 3.0)
        log(f"  ✅ EXCEL 다운 버튼 클릭")
        return True
    except Exception as e:
        log(f"  ❌ 다운 버튼 클릭 실패: {e}")
        return False


def wait_for_download(timeout: int = 15) -> Optional[Path]:
    """다운로드 완료 대기 - 15초 제한"""
    start_time = time.time()
    baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
    
    log(f"  ⏳ 다운로드 대기 중... (최대 {timeout}초)")
    
    found_crdownload = False
    
    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)
        
        # 현재 폴더의 모든 파일
        current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
        
        # .crdownload 파일 확인
        crdownloads = [f for f in current_files if f.suffix == '.crdownload']
        if crdownloads:
            found_crdownload = True
            if elapsed % 3 == 0:
                sizes = [f.stat().st_size for f in crdownloads]
                log(f"  ⏳ 진행중... ({elapsed}초, {sizes[0]:,} bytes)")
            time.sleep(0.5)
            continue
        
        # 새 파일 찾기
        if found_crdownload or elapsed > 2:
            excel_files = [
                f for f in current_files 
                if f.is_file() and f.suffix.lower() in ['.xls', '.xlsx']
                and f not in baseline_files
            ]
            
            if excel_files:
                latest = max(excel_files, key=lambda p: p.stat().st_mtime)
                size = latest.stat().st_size
                
                if size > 0:
                    time.sleep(0.5)  # 안정화 대기
                    new_size = latest.stat().st_size
                    
                    if new_size == size and size > 1000:
                        log(f"  ✅ 다운로드 완료: {latest.name} ({size:,} bytes)")
                        return latest
        
        time.sleep(0.3)
    
    # 타임아웃
    log(f"  ⏱️  타임아웃 ({timeout}초)")
    return None


def move_and_rename_file(downloaded_file: Path, property_type: str, year: int, month: int) -> Path:
    """다운로드 파일을 목적지로 이동 및 이름 변경"""
    # 폴더 생성
    folder_name = sanitize_folder_name(property_type)
    dest_dir = DOWNLOAD_DIR / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # 파일명: 아파트 200601.xlsx
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    dest_path = dest_dir / filename
    
    # 이동
    downloaded_file.rename(dest_path)
    log(f"  📁 저장: {dest_path}")
    
    return dest_path


def generate_monthly_dates(start_year: int = 2006, start_month: int = 1) -> List[Tuple[date, date]]:
    """2006년 1월부터 현재까지 월별 (시작일, 종료일) 생성"""
    today = date.today()
    current = date(start_year, start_month, 1)
    dates = []
    
    while current <= today:
        # 해당 월의 마지막 날
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        
        last_day = next_month - timedelta(days=1)
        
        # 현재 달이면 오늘까지만
        if current.year == today.year and current.month == today.month:
            last_day = today
        
        dates.append((current, last_day))
        
        # 다음 달로
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


def is_already_downloaded(property_type: str, year: int, month: int) -> bool:
    """이미 다운로드된 파일인지 확인"""
    folder_name = sanitize_folder_name(property_type)
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    dest_path = DOWNLOAD_DIR / folder_name / filename
    return dest_path.exists()


def check_if_all_historical_complete(progress: dict) -> bool:
    """모든 과거 데이터가 완료되었는지 확인 (2006-01 ~ 작년 12월)"""
    last_year = date.today().year - 1
    last_historical_month = f"{last_year}12"
    
    for prop in PROPERTY_TYPES:
        prop_key = sanitize_folder_name(prop)
        last_month = progress.get(prop_key, {}).get("last_month", "")
        
        # 작년 12월까지 완료되지 않았으면 False
        if not last_month or last_month < last_historical_month:
            return False
    
    return True


def download_single_month_with_retry(driver, property_type: str, start_date: date, end_date: date, max_retries: int = 3) -> bool:
    """단일 월 다운로드 - 재시도 포함"""
    year = start_date.year
    month = start_date.month
    
    log(f"\n{'='*60}")
    log(f"📅 {property_type} {year}년 {month}월")
    log(f"{'='*60}")
    
    # 이미 다운로드됨?
    if is_already_downloaded(property_type, year, month):
        log(f"  ⏭️  이미 존재함, 스킵")
        return True
    
    # 재시도 로직
    for attempt in range(1, max_retries + 1):
        log(f"  🔄 시도 {attempt}/{max_retries}")
        
        # 날짜 설정
        if not set_dates(driver, start_date, end_date):
            if attempt < max_retries:
                log(f"  ⏳ 15초 대기 후 재시도...")
                time.sleep(15)
                continue
            return False
        
        # 다운로드 클릭
        if not click_excel_download(driver):
            if attempt < max_retries:
                log(f"  ⏳ 15초 대기 후 재시도...")
                time.sleep(15)
                continue
            return False
        
        # 다운로드 대기 (15초)
        downloaded = wait_for_download(timeout=15)
        
        if downloaded:
            # 성공! 이동 및 이름 변경
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
            # 실패
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
    parser.add_argument("--update-mode", action="store_true", help="업데이트 모드 (최근 1년만)")
    args = parser.parse_args()
    
    log("="*70)
    log("🚀 국토부 실거래가 데이터 다운로드")
    log("="*70)
    log(f"📂 저장 경로: {DOWNLOAD_DIR}")
    log(f"📊 종목 수: {len(PROPERTY_TYPES)}")
    log("")
    
    # 진행 상황 로드
    progress = load_progress()
    
    # 모드 결정
    if args.update_mode:
        # 강제 업데이트 모드
        update_mode = True
        log("🔄 업데이트 모드: 최근 1년치만 갱신")
    else:
        # 자동 판단
        update_mode = check_if_all_historical_complete(progress)
        if update_mode:
            log("✅ 과거 데이터 완료 확인")
            log("🔄 업데이트 모드로 전환: 최근 1년치만 갱신")
        else:
            log("📥 전체 다운로드 모드: 2006-01부터 현재까지")
    
    log("")
    
    # 날짜 범위 생성
    if update_mode:
        # 최근 1년 (13개월 - 여유있게)
        today = date.today()
        start_year = today.year - 1
        start_month = today.month
        monthly_dates = generate_monthly_dates(start_year, start_month)
        log(f"📅 다운로드 기간: {start_year}-{start_month:02d} ~ {today.strftime('%Y-%m')} ({len(monthly_dates)}개월)")
    else:
        # 전체 기간
        monthly_dates = generate_monthly_dates(2006, 1)
        log(f"📅 다운로드 기간: 2006-01 ~ {date.today().strftime('%Y-%m')} ({len(monthly_dates)}개월)")
    
    # 테스트 모드
    if args.test_mode:
        monthly_dates = monthly_dates[-2:]
        log(f"🧪 테스트 모드: 최근 {len(monthly_dates)}개월만")
    
    log("")
    
    driver = build_driver()
    
    try:
        # 페이지 로드
        log("🌐 사이트 접속 중...")
        driver.get(MOLIT_URL)
        time.sleep(3)
        try_accept_alert(driver, 2.0)
        log("✅ 접속 완료\n")
        
        # 전체 통계
        total_success = 0
        total_fail = 0
        
        # 각 부동산 종목별로
        for prop_idx, property_type in enumerate(PROPERTY_TYPES, 1):
            log("="*70)
            log(f"📊 [{prop_idx}/{len(PROPERTY_TYPES)}] {property_type}")
            log("="*70)
            
            # 탭 선택
            if not select_property_tab(driver, property_type):
                log(f"⚠️  탭 선택 실패, 다음 종목으로...")
                continue
            
            # 진행 상황 확인
            prop_key = sanitize_folder_name(property_type)
            last_completed = progress.get(prop_key, {}).get("last_month", "")
            
            if last_completed:
                log(f"📌 마지막 완료: {last_completed}")
            
            # 각 월별로
            success_count = 0
            fail_count = 0
            consecutive_fails = 0
            
            for month_idx, (start_date, end_date) in enumerate(monthly_dates, 1):
                year = start_date.year
                month = start_date.month
                month_key = f"{year:04d}{month:02d}"
                
                # 이미 완료한 달 스킵
                if last_completed and month_key <= last_completed:
                    continue
                
                log(f"\n[{month_idx}/{len(monthly_dates)}]", end=" ")
                
                # 다운로드 시도 (최대 3회 재시도)
                success = download_single_month_with_retry(driver, property_type, start_date, end_date, max_retries=3)
                
                if success:
                    success_count += 1
                    consecutive_fails = 0
                    
                    # 진행 상황 저장
                    if prop_key not in progress:
                        progress[prop_key] = {}
                    progress[prop_key]["last_month"] = month_key
                    progress[prop_key]["last_update"] = datetime.now().isoformat()
                    save_progress(progress)
                else:
                    fail_count += 1
                    consecutive_fails += 1
                    log(f"⚠️  실패 카운트: {fail_count} (연속: {consecutive_fails})")
                    
                    # 연속 3회 실패 시 중단 (100회 제한 가능성)
                    if consecutive_fails >= 3:
                        log(f"\n⛔ 연속 {consecutive_fails}회 실패 - 다운로드 제한 가능성")
                        log(f"💾 진행 상황 저장됨: {PROGRESS_FILE}")
                        log(f"📌 다음 실행시 {month_key}부터 재개됩니다")
                        log(f"⏰ 100회 제한일 경우 내일 다시 실행하세요")
                        driver.quit()
                        return
                
                # 다음 요청 전 대기
                time.sleep(2)
            
            log(f"\n✅ {property_type} 완료: 성공 {success_count}, 실패 {fail_count}")
            total_success += success_count
            total_fail += fail_count
            
            # 테스트 모드: 첫 종목만
            if args.test_mode:
                log("\n🧪 테스트 모드 - 첫 종목만 완료")
                break
            
            log("")
        
        log("="*70)
        log("🎉 다운로드 완료!")
        log(f"📊 전체 통계: 성공 {total_success}, 실패 {total_fail}")
        log("="*70)
        
    except KeyboardInterrupt:
        log("\n⚠️  사용자 중단")
        log(f"💾 진행 상황 저장됨: {PROGRESS_FILE}")
    except Exception as e:
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
