# -*- coding: utf-8 -*-
"""
최근 3개월치 데이터 다운로드 및 전처리
- 7개 탭의 최근 3개월치만 다운로드
- 다운로드 후 자동 전처리
- Google Drive 업로드
"""
import os
import sys
import io
import json
import time
import argparse
import warnings
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from selenium.common.exceptions import UnexpectedAlertPresentException
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.utils import get_column_letter

# openpyxl 경고 억제
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

# Google Drive 업로드 모듈
try:
    from drive_uploader import get_uploader
    DRIVE_UPLOAD_ENABLED = True
except ImportError:
    DRIVE_UPLOAD_ENABLED = False

# Windows 콘솔 인코딩 설정
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ==================== 설정 ====================
# CI 환경 감지 (GitHub Actions)
IS_CI = os.getenv("CI", "") == "true" or os.getenv("GITHUB_ACTIONS", "") == "true"

# 저장 폴더 (환경에 따라 자동 전환)
if IS_CI:
    # GitHub Actions: 임시 output 폴더
    DOWNLOAD_DIR = Path("output")
else:
    # 로컬 PC: OneDrive 경로
    DOWNLOAD_DIR = Path(r"D:\OneDrive\office work\부동산 실거래 데이터")

TEMP_DOWNLOAD_DIR = Path("_temp_downloads")
MOLIT_URL = "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="

PROPERTY_TYPES = [
    "아파트",
    "연립다세대",
    "단독다가구",
    "오피스텔",
    "토지",
    "상업업무용",
    "공장창고등"
]

TAB_NAME_MAPPING = {
    "아파트": "아파트",
    "연립다세대": "연립/다세대",
    "단독다가구": "단독/다가구",
    "오피스텔": "오피스텔",
    "토지": "토지",
    "상업업무용": "상업/업무용",
    "공장창고등": "공장/창고 등",
}

TEMP_DOWNLOAD_DIR.mkdir(exist_ok=True)
if IS_CI:
    DOWNLOAD_DIR.mkdir(exist_ok=True)

def log(message: str):
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def get_recent_months(count: int = 3) -> list:
    """최근 N개월 반환"""
    today = date.today()
    months = []
    for i in range(count):
        target_date = today - timedelta(days=30 * i)
        months.append((target_date.year, target_date.month))
    return months

def preprocess_excel_file(file_path: Path) -> bool:
    """엑셀 파일 전처리"""
    try:
        log(f"전처리 시작: {file_path.name}")
        
        # 전체 파일 읽기
        df = pd.read_excel(file_path, header=None)
        
        # 이미 전처리된 파일인지 확인
        first_row = df.iloc[0].astype(str).tolist() if len(df) > 0 else []
        if '광역' in first_row or '계약년' in first_row:
            log(f"이미 전처리된 파일: {file_path.name}")
            return True
        
        # 1-12행 삭제
        if len(df) > 12:
            df = df.iloc[12:].reset_index(drop=True)
        else:
            return False
        
        # A열 삭제
        if len(df.columns) > 0:
            df = df.drop(df.columns[0], axis=1)
            df.columns = range(len(df.columns))
        
        # 헤더 설정
        if len(df) > 0:
            header_row = df.iloc[0].tolist()
            df.columns = header_row
            df = df.iloc[1:].reset_index(drop=True)
        
        # 시군구 열 처리
        if '시군구' in df.columns:
            시군구_인덱스 = list(df.columns).index('시군구')
            address_parts = df['시군구'].str.split(' ', expand=True)
            
            광역_데이터 = address_parts[0].fillna('').astype(str).replace('nan', '')
            구_데이터 = address_parts[1].fillna('').astype(str).replace('nan', '') if address_parts.shape[1] >= 2 else pd.Series([''] * len(df), index=df.index, dtype=str)
            법정동_데이터 = address_parts[2].fillna('').astype(str).replace('nan', '') if address_parts.shape[1] >= 3 else pd.Series([''] * len(df), index=df.index, dtype=str)
            리_데이터 = address_parts[3].fillna('').astype(str).replace('nan', '').replace('None', '') if address_parts.shape[1] >= 4 else pd.Series([''] * len(df), index=df.index, dtype=str)
            
            df.insert(시군구_인덱스 + 1, '광역', 광역_데이터)
            df.insert(시군구_인덱스 + 2, '구', 구_데이터)
            df.insert(시군구_인덱스 + 3, '법정동', 법정동_데이터)
            df.insert(시군구_인덱스 + 4, '리', 리_데이터)
            df = df.drop('시군구', axis=1)
        
        # 계약년월 열 처리
        if '계약년월' in df.columns:
            계약년월_인덱스 = list(df.columns).index('계약년월')
            df['계약년월'] = df['계약년월'].astype(str)
            계약년_데이터 = df['계약년월'].str[:4].astype(str)
            계약월_데이터 = df['계약년월'].str[4:6].astype(str)
            
            df.insert(계약년월_인덱스 + 1, '계약년', 계약년_데이터)
            df.insert(계약년월_인덱스 + 2, '계약월', 계약월_데이터)
            df = df.drop('계약년월', axis=1)
        
        # 열 순서 재배열
        desired_order = [
            '광역', '구', '법정동', '리', '번지', '본번', '부번', '단지명', 
            '전용면적', '전용면적(㎡)', '거래금액', '거래금액(만원)',
            '계약년', '계약월', '계약일', '동', '층', '매수자', '매도자', '건축년도', '도로명',
            '해제사유발생일', '거래유형', '중개사소재지', '등기일자', '주택유형'
        ]
        
        existing_columns = [col for col in desired_order if col in df.columns]
        remaining_columns = [col for col in df.columns if col not in desired_order]
        df = df[existing_columns + remaining_columns]
        
        # 파일 저장
        df = df.fillna('')
        if '리' in df.columns:
            df['리'] = df['리'].astype(str).replace('nan', '').replace('None', '')
        if '계약년' in df.columns:
            df['계약년'] = df['계약년'].astype(str)
        if '계약월' in df.columns:
            df['계약월'] = df['계약월'].astype(str)
        
        # Excel 저장
        wb = Workbook()
        ws = wb.active
        
        for r in dataframe_to_rows(df, index=False, header=True):
            ws.append(r)
        
        # 계약년, 계약월 텍스트 형식 설정
        header_row = list(df.columns)
        if '계약년' in header_row:
            col_idx = header_row.index('계약년') + 1
            col_letter = get_column_letter(col_idx)
            for row in range(2, len(df) + 2):
                ws[f'{col_letter}{row}'].number_format = '@'
        if '계약월' in header_row:
            col_idx = header_row.index('계약월') + 1
            col_letter = get_column_letter(col_idx)
            for row in range(2, len(df) + 2):
                ws[f'{col_letter}{row}'].number_format = '@'
        
        # 열 너비 자동 조정
        for idx, col_name in enumerate(header_row, start=1):
            col_letter = get_column_letter(idx)
            header_text = str(col_name)
            header_length = sum(2 if ord(c) > 127 else 1 for c in header_text)
            
            col_data = df.iloc[:min(1000, len(df)), idx-1]
            if len(col_data) > 0:
                max_data_length = 0
                for val in col_data.astype(str).head(1000):
                    if pd.notna(val) and val != 'nan':
                        val_length = sum(2 if ord(c) > 127 else 1 for c in str(val))
                        max_data_length = max(max_data_length, val_length)
            else:
                max_data_length = 0
            
            max_length = max(header_length, max_data_length) + 3
            adjusted_width = max(12, min(max_length, 60))
            ws.column_dimensions[col_letter].width = adjusted_width
        
        wb.save(file_path)
        wb.close()
        
        log(f"전처리 완료: {file_path.name}")
        return True
        
    except Exception as e:
        log(f"전처리 오류: {e}")
        import traceback
        traceback.print_exc()
        return False

def setup_driver() -> webdriver.Chrome:
    """Chrome 드라이버 설정"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_experimental_option("prefs", {
        "download.default_directory": str(TEMP_DOWNLOAD_DIR.absolute()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    })
    
    service = Service()
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def remove_google_translate_popup(driver):
    """Google Translate 팝업 제거"""
    try:
        driver.execute_script("""
            document.querySelectorAll('div').forEach(div => {
                const text = div.textContent || '';
                const className = div.className || '';
                const id = div.id || '';
                if ((text.includes('Google Translate') || 
                     (text.includes('영어') && text.includes('한국어')) ||
                     className.includes('translate') ||
                     id.includes('translate')) && 
                    div.offsetParent !== null) {
                    div.style.display = 'none';
                    div.style.visibility = 'hidden';
                }
            });
        """)
    except:
        pass

def try_accept_alert(driver, timeout=3.0) -> bool:
    """Alert 자동 수락"""
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
            if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                raise
            time.sleep(0.2)
    return False

def select_property_tab(driver, tab_name: str) -> bool:
    """부동산 종목 탭 선택"""
    actual_tab_name = TAB_NAME_MAPPING.get(tab_name, tab_name)
    log(f"  탭 선택: {tab_name} (페이지: {actual_tab_name})")
    
    if "xls.do" not in driver.current_url:
        log(f"  🔄 페이지 로딩...")
        driver.get(MOLIT_URL)
        time.sleep(5)
        try_accept_alert(driver, 2.0)
    
    time.sleep(3)
    try_accept_alert(driver, 2.0)
    remove_google_translate_popup(driver)
    
    TAB_ID_MAPPING = {
        "아파트": "xlsTab1",
        "연립다세대": "xlsTab2",
        "단독다가구": "xlsTab3",
        "오피스텔": "xlsTab4",
        "상업업무용": "xlsTab6",
        "토지": "xlsTab7",
        "공장창고등": "xlsTab8",
    }
    
    tab_id = TAB_ID_MAPPING.get(tab_name)
    if tab_id:
        try:
            elem = driver.find_element(By.ID, tab_id)
            if not elem.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", elem)
                time.sleep(1)
            
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", elem)
            time.sleep(2)
            try_accept_alert(driver, 2.0)
            remove_google_translate_popup(driver)
            log(f"  ✅ 탭 선택 완료: {tab_name}")
            return True
        except Exception as e:
            log(f"  ⚠️  ID로 찾기 실패: {e}")
    
    return False

def find_date_inputs(driver):
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
        remove_google_translate_popup(driver)
        time.sleep(0.3)
        
        fn_ready = driver.execute_script("return typeof fnExcelDown === 'function';")
        if not fn_ready:
            time.sleep(2.0)
            fn_ready = driver.execute_script("return typeof fnExcelDown === 'function';")
            if not fn_ready:
                log(f"  ⚠️  fnExcelDown 함수를 찾을 수 없습니다")
        
        result = driver.execute_script("""
            if (typeof fnExcelDown === 'function') {
                fnExcelDown();
                return true;
            }
            return false;
        """)
        if result:
            log(f"  ✅ EXCEL 다운 버튼 클릭")
            try:
                alert = Alert(driver)
                alert_text = alert.text
                log(f"  🔔 Alert: {alert_text}")
                
                if "100건" in alert_text or "100" in alert_text:
                    alert.accept()
                    log(f"  ⛔ 일일 다운로드 100건 제한 도달!")
                    raise Exception("DOWNLOAD_LIMIT_100")
                
                if "데이터가 존재하지 않습니다" in alert_text or "존재하지 않습니다" in alert_text:
                    alert.accept()
                    log(f"  ℹ️  해당 기간에 데이터가 없습니다.")
                    raise Exception("NO_DATA_AVAILABLE")
                
                alert.accept()
            except Exception as e:
                if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                    raise
                pass
            
            time.sleep(1.0)
            return True
    except Exception as e:
        if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
            raise
        log(f"  ⚠️  JavaScript 함수 호출 실패: {e}")
    
    return False

def wait_for_download(timeout: int = 15, expected_year: int = None, expected_month: int = None) -> Optional[Path]:
    """다운로드 완료 대기"""
    end_time = time.time() + timeout
    baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
    
    while time.time() < end_time:
        current_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        new_files = current_files - baseline_files
        
        for new_file in new_files:
            if new_file.suffix.lower() in ['.xls', '.xlsx']:
                if not new_file.name.endswith('.crdownload'):
                    log(f"  ✅ 다운로드 완료: {new_file.name}")
                    return new_file
        
        time.sleep(0.5)
    
    log(f"  ⚠️  다운로드 타임아웃")
    return None

def download_month(driver, property_type: str, year: int, month: int) -> Optional[Path]:
    """한 달치 데이터 다운로드"""
    try:
        # 탭 선택
        if not select_property_tab(driver, property_type):
            log(f"  ❌ 탭 선택 실패")
            return None
        
        time.sleep(2)
        
        # 날짜 설정
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        
        if not set_dates(driver, start_date, end_date):
            log(f"  ❌ 날짜 설정 실패")
            return None
        
        time.sleep(2)
        
        # temp 폴더 정리
        try:
            for old_file in TEMP_DOWNLOAD_DIR.glob("*.xlsx"):
                old_file.unlink()
            for old_file in TEMP_DOWNLOAD_DIR.glob("*.xls"):
                old_file.unlink()
        except:
            pass
        
        baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        
        # 엑셀 다운로드
        try:
            if not click_excel_download(driver):
                log(f"  ❌ 다운로드 버튼 클릭 실패")
                return None
        except Exception as e:
            if "NO_DATA_AVAILABLE" in str(e):
                log(f"  ⏭️  데이터 없음, 스킵")
                return None
            if "DOWNLOAD_LIMIT_100" in str(e):
                raise
            return None
        
        # 다운로드 대기
        downloaded = wait_for_download(timeout=15, expected_year=year, expected_month=month)
        return downloaded
        
    except Exception as e:
        if "DOWNLOAD_LIMIT_100" in str(e):
            raise
        log(f"다운로드 오류 ({property_type} {year:04d}{month:02d}): {e}")
        return None

def move_and_upload_file(downloaded_file: Path, property_type: str, year: int, month: int) -> Optional[Path]:
    """파일 이동 및 Google Drive 업로드"""
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    
    # CI 환경에서는 로컬 저장 없이 바로 Google Drive에 업로드
    if IS_CI:
        # 임시 파일로 전처리
        temp_processed = TEMP_DOWNLOAD_DIR / filename
        downloaded_file.rename(temp_processed)
        
        # 전처리
        if not preprocess_excel_file(temp_processed):
            log(f"전처리 실패: {temp_processed.name}")
            return None
        
        # Google Drive 업로드
        if DRIVE_UPLOAD_ENABLED:
            try:
                log(f"  ☁️  Google Drive 업로드 중...")
                uploader = get_uploader()
                if uploader.init_service():
                    uploader.upload_file(temp_processed, filename, property_type)
                    log(f"  ✅ Google Drive 업로드 완료")
                else:
                    log(f"  ⚠️  Google Drive 업로드 실패: 서비스 초기화 실패")
            except Exception as e:
                log(f"  ⚠️  Google Drive 업로드 실패: {e}")
        
        # 임시 파일 삭제
        try:
            temp_processed.unlink()
        except:
            pass
        
        return None
    else:
        # 로컬 환경: 로컬 저장 후 Google Drive 업로드
        dest_dir = DOWNLOAD_DIR / property_type
        dest_dir.mkdir(exist_ok=True)
        dest_path = dest_dir / filename
        
        # 파일 이동 (덮어쓰기)
        if dest_path.exists():
            dest_path.unlink()
        downloaded_file.rename(dest_path)
        log(f"  📁 저장: {dest_path}")
        
        # 전처리
        if not preprocess_excel_file(dest_path):
            log(f"전처리 실패: {dest_path.name}")
            return None
        
        # Google Drive 업로드
        if DRIVE_UPLOAD_ENABLED:
            try:
                log(f"  ☁️  Google Drive 업로드 중...")
                uploader = get_uploader()
                if uploader.init_service():
                    uploader.upload_file(dest_path, filename, property_type)
                    log(f"  ✅ Google Drive 업로드 완료")
                else:
                    log(f"  ⚠️  Google Drive 업로드 실패: 서비스 초기화 실패")
            except Exception as e:
                log(f"  ⚠️  Google Drive 업로드 실패: {e}")
        
        return dest_path

def main():
    """메인 함수"""
    # 명령줄 인자 파싱
    parser = argparse.ArgumentParser(description='최근 3개월치 부동산 실거래 데이터 다운로드 및 전처리')
    parser.add_argument('--update-mode', action='store_true', 
                       help='업데이트 모드 (최근 3개월치만 다운로드)')
    args = parser.parse_args()
    
    log("="*70)
    log("최근 3개월치 데이터 다운로드 및 전처리 시작")
    if args.update_mode:
        log("모드: --update-mode (최근 3개월치만 다운로드)")
    if IS_CI:
        log("환경: GitHub Actions (CI)")
    else:
        log("환경: 로컬 PC")
    log("="*70)
    
    # 최근 3개월 계산
    recent_months = get_recent_months(3)
    log(f"다운로드 대상: 최근 3개월 ({recent_months})")
    
    driver = setup_driver()
    
    try:
        for property_type in PROPERTY_TYPES:
            log(f"\n{'='*70}")
            log(f"처리 중: {property_type}")
            log(f"{'='*70}")
            
            for year, month in recent_months:
                log(f"\n[{property_type}] {year:04d}-{month:02d} 다운로드 중...")
                
                # 다운로드
                downloaded_file = download_month(driver, property_type, year, month)
                if not downloaded_file:
                    log(f"다운로드 실패: {property_type} {year:04d}{month:02d}")
                    continue
                
                # 파일 이동 및 업로드
                dest_path = move_and_upload_file(downloaded_file, property_type, year, month)
                
                if dest_path or IS_CI:
                    log(f"완료: {property_type} {year:04d}{month:02d}")
                
    finally:
        driver.quit()
    
    log("\n" + "="*70)
    log("모든 작업 완료!")
    log("="*70)

if __name__ == "__main__":
    main()
