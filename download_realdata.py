# -*- coding: utf-8 -*-
"""
최근 3개월치 데이터 다운로드 및 전처리
- 7개 탭의 최근 3개월치만 다운로드
- 다운로드 후 자동 전처리
- Google Drive 업로드
"""
import os
import re
import sys
import io
import json
import time
import argparse
import warnings
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, List
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

def log(msg: str, end="\n"):
    """로그 출력"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}", end=end, flush=True)

def sanitize_folder_name(name: str) -> str:
    """폴더명에서 특수문자 제거"""
    return re.sub(r'[<>:"/\\|?*]', '_', name)

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

def build_driver():
    """크롬 드라이버 생성"""
    opts = Options()
    # CI 환경 확인 (더 확실하게)
    is_ci_env = os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true"
    
    # CI 환경이 아니면 무조건 브라우저 창 보이기
    if is_ci_env:
        # CI 환경 (GitHub Actions 등) - headless 필수
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1400,900")
    else:
        # 로컬 환경 - 브라우저 창 무조건 보이기
        # headless 옵션 절대 사용 안 함
        opts.add_argument("--start-maximized")
    
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--lang=ko-KR")
    
    # 로컬 실행 시 안정성 개선
    if not is_ci_env:
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
            // Google Translate 관련 모든 요소 찾아서 제거 또는 숨김
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
                        // iframe인 경우
                        if (el.tagName === 'IFRAME') {
                            el.style.display = 'none';
                            el.style.visibility = 'hidden';
                            el.style.width = '0';
                            el.style.height = '0';
                        } else {
                            // 일반 요소는 제거
                            el.remove();
                        }
                    });
                } catch(e) {}
            });
            
            // body에 직접 추가된 Google Translate 요소도 찾기
            const allDivs = document.querySelectorAll('div');
            allDivs.forEach(div => {
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
    """Alert 자동 수락 - 100건 제한 및 데이터 없음 감지"""
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
                raise Exception("DOWNLOAD_LIMIT_100")
            
            # 데이터 없음 감지
            if "데이터가 존재하지 않습니다" in text or "존재하지 않습니다" in text:
                alert.accept()
                log(f"  ℹ️  해당 기간에 데이터가 없습니다.")
                raise Exception("NO_DATA_AVAILABLE")
            
            alert.accept()
            time.sleep(0.5)
            return True
        except Exception as e:
            if str(e) == "DOWNLOAD_LIMIT_100":
                raise  # 100건 제한은 상위로 전달
            if str(e) == "NO_DATA_AVAILABLE":
                raise  # 데이터 없음은 상위로 전달
            time.sleep(0.2)
    return False

def select_property_tab(driver, tab_name: str) -> bool:
    """부동산 종목 탭 선택 - 강화 버전"""
    # 실제 페이지의 탭 이름으로 변환
    actual_tab_name = TAB_NAME_MAPPING.get(tab_name, tab_name)
    log(f"  탭 선택: {tab_name} (페이지: {actual_tab_name})")
    
    # xls.do 페이지인지 확인
    if "xls.do" not in driver.current_url:
        log(f"  🔄 페이지 로딩...")
        driver.get(MOLIT_URL)
        time.sleep(5)  # 페이지 로딩 대기 증가
        try_accept_alert(driver, 2.0)
    
    # 페이지가 완전히 로드될 때까지 대기
    time.sleep(3)
    try_accept_alert(driver, 2.0)
    
    # Google Translate 팝업 제거
    remove_google_translate_popup(driver)
    
    # 탭 ID 매핑 (실제 페이지 구조 기반)
    TAB_ID_MAPPING = {
        "아파트": "xlsTab1",
        "연립다세대": "xlsTab2",
        "단독다가구": "xlsTab3",
        "오피스텔": "xlsTab4",
        "상업업무용": "xlsTab6",
        "토지": "xlsTab7",
        "공장창고등": "xlsTab8",
    }
    
    # 방법 0: ID로 직접 찾기 (가장 확실한 방법)
    tab_id = TAB_ID_MAPPING.get(tab_name)
    if tab_id:
        try:
            log(f"  🔍 ID로 탭 찾기: {tab_id}")
            elem = driver.find_element(By.ID, tab_id)
            if not elem.is_displayed():
                log(f"  ⚠️  요소가 보이지 않음, 스크롤 시도...")
                driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", elem)
                time.sleep(1)
            
            # 클릭 전 상태 확인
            parent_before = elem.find_element(By.XPATH, "./..")
            parent_class_before = parent_before.get_attribute("class")
            log(f"  📊 클릭 전 부모 클래스: {parent_class_before}")
            
            # 클릭
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", elem)
            time.sleep(2)
            try_accept_alert(driver, 2.0)
            
            # 클릭 후 활성화 확인
            parent_after = elem.find_element(By.XPATH, "./..")
            parent_class_after = parent_after.get_attribute("class")
            log(f"  📊 클릭 후 부모 클래스: {parent_class_after}")
            
            # 활성화 확인 (부모에 'on' 클래스가 있으면 활성화됨)
            if "on" in parent_class_after:
                # 탭 선택 후 Google Translate 팝업 제거
                remove_google_translate_popup(driver)
                log(f"  ✅ 탭 선택 완료 (ID): {tab_name}")
                return True
            else:
                log(f"  ⚠️  탭 클릭했지만 활성화되지 않음, 재시도...")
                # 한 번 더 클릭 시도
                driver.execute_script("arguments[0].click();", elem)
                time.sleep(2)
                try_accept_alert(driver, 2.0)
                parent_after2 = elem.find_element(By.XPATH, "./..")
                parent_class_after2 = parent_after2.get_attribute("class")
                if "on" in parent_class_after2:
                    # 탭 선택 후 Google Translate 팝업 제거
                    remove_google_translate_popup(driver)
                    log(f"  ✅ 탭 선택 완료 (ID, 재시도): {tab_name}")
                    return True
                else:
                    log(f"  ❌ 탭 활성화 실패")
        except Exception as e:
            log(f"  ⚠️  ID로 찾기 실패: {e}")
            import traceback
            traceback.print_exc()
    
    # 방법 1: CSS 선택자로 quarter-tab-cover 내부 링크 찾기
    css_selectors = []
    if tab_id:
        css_selectors.append(f"ul.quarter-tab-cover a#{tab_id}")
    css_selectors.extend([
        f"ul.quarter-tab-cover a[title*='{tab_name}']",
        f"ul.quarter-tab-cover a[title*='{actual_tab_name.replace('/', '')}']",
        f".quarter-tab-cover a.link",
    ])
    
    for idx, selector in enumerate(css_selectors, 1):
        try:
            log(f"  🔍 탭 찾기 시도 {idx}/{len(css_selectors)} (CSS: {selector})")
            elems = driver.find_elements(By.CSS_SELECTOR, selector)
            for elem in elems:
                link_text = elem.text.strip()
                if link_text == actual_tab_name or actual_tab_name in link_text:
                    if elem.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
                        time.sleep(0.5)
                        driver.execute_script("arguments[0].click();", elem)
                        time.sleep(2)
                        try_accept_alert(driver, 2.0)
                        # 탭 선택 후 Google Translate 팝업 제거
                        remove_google_translate_popup(driver)
                        log(f"  ✅ 탭 선택 완료 (CSS): {tab_name}")
                        return True
        except Exception as e:
            if idx == len(css_selectors):
                log(f"  ⏭️  CSS 선택자 모두 실패, XPath 시도...")
            continue
    
    # 방법 2: XPath 선택자 시도
    xpath_selectors = [
        f"//ul[@class='quarter-tab-cover']//a[contains(text(), '{actual_tab_name}')]",
        f"//ul[@class='quarter-tab-cover']//a[normalize-space(text())='{actual_tab_name}']",
        f"//a[@id='{tab_id}']" if tab_id else None,
        f"//a[contains(text(), '{actual_tab_name}')]",
        f"//a[normalize-space(text())='{actual_tab_name}']",
    ]
    xpath_selectors = [s for s in xpath_selectors if s is not None]
    
    for idx, selector in enumerate(xpath_selectors, 1):
        try:
            log(f"  🔍 탭 찾기 시도 {idx}/{len(xpath_selectors)} (XPath)")
            elem = driver.find_element(By.XPATH, selector)
            
            # 요소가 보이는지 확인
            if not elem.is_displayed():
                log(f"  ⚠️  요소가 보이지 않음, 스크롤 시도...")
                driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", elem)
                time.sleep(1)
            
            # 스크롤 및 클릭
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            time.sleep(0.5)
            
            # JavaScript로 클릭 시도
            try:
                driver.execute_script("arguments[0].click();", elem)
            except:
                elem.click()
            
            time.sleep(2)
            try_accept_alert(driver, 2.0)
            # 탭 선택 후 Google Translate 팝업 제거
            remove_google_translate_popup(driver)
            
            log(f"  ✅ 탭 선택 완료: {tab_name}")
            return True
            
        except Exception as e:
            if idx == len(xpath_selectors):
                log(f"  ⏭️  XPath 선택자 모두 실패, 다른 방법 시도...")
            else:
                continue
    
    # 방법 3: 모든 링크를 찾아서 텍스트로 비교
    try:
        log(f"  🔍 모든 링크 검색 중...")
        all_links = driver.find_elements(By.TAG_NAME, "a")
        log(f"  📋 발견된 링크: {len(all_links)}개")
        
        # 디버깅: 모든 링크 텍스트 출력 (처음 20개만)
        link_texts = []
        for link in all_links[:20]:
            try:
                link_text = link.text.strip()
                if link_text:
                    link_texts.append(link_text)
            except:
                pass
        
        if link_texts:
            log(f"  📝 링크 텍스트 샘플: {link_texts}")
        
        # 부분 매칭 시도 (더 유연하게)
        for link in all_links:
            try:
                link_text = link.text.strip()
                # 정확히 일치하거나, 부분 일치, 또는 공백 제거 후 일치
                normalized_link = link_text.replace(" ", "").replace("\n", "").replace("\t", "").replace("/", "")
                normalized_tab = actual_tab_name.replace(" ", "").replace("\n", "").replace("\t", "").replace("/", "")
                
                # 실제 탭 이름을 우선적으로 매칭 (정확도 높음)
                if (link_text == actual_tab_name or 
                    normalized_link == normalized_tab or
                    actual_tab_name in link_text or
                    normalized_tab in normalized_link):
                    log(f"  ✅ 링크 발견: '{link_text}' (매핑: '{tab_name}' → '{actual_tab_name}')")
                    
                    # 요소가 보이는지 확인
                    if not link.is_displayed():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", link)
                        time.sleep(1)
                    
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
                    time.sleep(0.5)
                    
                    # JavaScript로 클릭 시도
                    try:
                        driver.execute_script("arguments[0].click();", link)
                    except:
                        link.click()
                    
                    time.sleep(2)
                    try_accept_alert(driver, 2.0)
                    # 탭 선택 후 Google Translate 팝업 제거
                    remove_google_translate_popup(driver)
                    
                    log(f"  ✅ 탭 선택 완료: {tab_name}")
                    return True
            except Exception as e:
                continue
        
        # 더 많은 링크 확인 (20개 이후)
        if len(all_links) > 20:
            log(f"  🔍 나머지 {len(all_links) - 20}개 링크 확인 중...")
            for link in all_links[20:]:
                try:
                    link_text = link.text.strip()
                    normalized_link = link_text.replace(" ", "").replace("\n", "").replace("\t", "").replace("/", "")
                    normalized_tab = actual_tab_name.replace(" ", "").replace("\n", "").replace("\t", "").replace("/", "")
                    
                    # 실제 탭 이름을 우선적으로 매칭 (정확도 높음)
                    if (link_text == actual_tab_name or 
                        normalized_link == normalized_tab or
                        actual_tab_name in link_text or
                        normalized_tab in normalized_link):
                        log(f"  ✅ 링크 발견: '{link_text}' (매핑: '{tab_name}' → '{actual_tab_name}')")
                        
                        if not link.is_displayed():
                            driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", link)
                            time.sleep(1)
                        
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
                        time.sleep(0.5)
                        
                        try:
                            driver.execute_script("arguments[0].click();", link)
                        except:
                            link.click()
                        
                        time.sleep(2)
                        try_accept_alert(driver, 2.0)
                        # 탭 선택 후 Google Translate 팝업 제거
                        remove_google_translate_popup(driver)
                        
                        log(f"  ✅ 탭 선택 완료: {tab_name}")
                        return True
                except:
                    continue
                    
    except Exception as e:
        log(f"  ⚠️  링크 검색 실패: {e}")
    
    # 방법 4: JavaScript로 찾기
    try:
        log(f"  🔍 JavaScript로 탭 찾기 시도...")
        script = f"""
        var links = document.querySelectorAll('a');
        var targetTab = '{actual_tab_name}';
        var normalizedTarget = targetTab.replace(/[\\s\\/]/g, '');
        for (var i = 0; i < links.length; i++) {{
            var text = links[i].textContent.trim();
            var normalizedText = text.replace(/[\\s\\/]/g, '');
            if (text === targetTab || normalizedText === normalizedTarget || text.includes(targetTab)) {{
                links[i].scrollIntoView({{block: 'center'}});
                links[i].click();
                return true;
            }}
        }}
        return false;
        """
        result = driver.execute_script(script)
        if result:
            time.sleep(2)
            try_accept_alert(driver, 2.0)
            # 탭 선택 후 Google Translate 팝업 제거
            remove_google_translate_popup(driver)
            log(f"  ✅ 탭 선택 완료 (JavaScript): {tab_name}")
            return True
    except Exception as e:
        log(f"  ⚠️  JavaScript 클릭 실패: {e}")
    
    log(f"  ❌ 탭 선택 실패: 모든 방법 시도 완료")
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

def click_excel_download(driver, baseline_files: set = None) -> bool:
    """EXCEL 다운 버튼 클릭 - fnExcelDown() 함수 호출"""
    try:
        # Google Translate 팝업 강제 제거/숨김
        remove_google_translate_popup(driver)
        time.sleep(0.3)
        
        # EXCEL 다운 버튼이 준비되었는지 확인
        try:
            btn = driver.find_element(By.XPATH, "//button[contains(text(), 'EXCEL 다운')]")
            if not btn.is_displayed() or not btn.is_enabled():
                log(f"  ⏳ 버튼 준비 대기 중...")
                time.sleep(1.0)
        except:
            log(f"  ⏳ 버튼 찾기 대기 중...")
            time.sleep(1.0)
        
        # baseline_files가 없으면 현재 파일 목록 사용
        if baseline_files is None:
            baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        
        # 방법 1: JavaScript 함수 직접 호출 (가장 안전 - 다른 요소를 건드리지 않음)
        try:
            # fnExcelDown 함수가 준비되었는지 확인
            fn_ready = driver.execute_script("return typeof fnExcelDown === 'function';")
            if not fn_ready:
                log(f"  ⏳ fnExcelDown 함수 준비 대기 중...")
                time.sleep(2.0)
                # 다시 확인
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
                log(f"  ✅ EXCEL 다운 버튼 클릭 (JavaScript 함수 직접 호출)")
                # Alert 확인 및 다운로드 시작 확인
                alert_shown = False
                alert_text = None
                try:
                    alert = Alert(driver)
                    alert_text = alert.text
                    log(f"  🔔 Alert: {alert_text}")
                    
                    # 100건 제한 감지
                    if "100건" in alert_text or "100" in alert_text:
                        alert.accept()
                        log(f"  ⛔ 일일 다운로드 100건 제한 도달!")
                        raise Exception("DOWNLOAD_LIMIT_100")
                    
                    # 데이터 없음 감지
                    if "데이터가 존재하지 않습니다" in alert_text or "존재하지 않습니다" in alert_text:
                        alert.accept()
                        log(f"  ℹ️  해당 기간에 데이터가 없습니다.")
                        raise Exception("NO_DATA_AVAILABLE")
                    
                    alert.accept()
                    alert_shown = True
                except Exception as e:
                    if str(e) == "DOWNLOAD_LIMIT_100" or str(e) == "NO_DATA_AVAILABLE":
                        raise
                    # Alert가 없으면 다운로드가 시작되었을 수 있음
                    pass
                
                # 다운로드 시작 확인 (1초 대기 후 .crdownload 파일이나 새 파일 확인)
                time.sleep(1.0)
                download_started = False
                try:
                    current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
                    # .crdownload 파일 확인 (baseline 제외)
                    crdownloads = [f for f in current_files if f.suffix == '.crdownload' and f not in baseline_files]
                    if crdownloads:
                        download_started = True
                        log(f"  📥 다운로드 시작 확인: .crdownload 파일 발견")
                    # 새 엑셀 파일 확인 (baseline 제외)
                    excel_files = [f for f in current_files if f.suffix.lower() in ['.xls', '.xlsx'] and f not in baseline_files]
                    if excel_files:
                        download_started = True
                        log(f"  📥 다운로드 시작 확인: 새 엑셀 파일 발견")
                except:
                    pass
                
                if not download_started and not alert_shown:
                    log(f"  ⚠️  다운로드 시작 신호가 보이지 않습니다. 계속 대기합니다...")
                
                return True
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                raise
            log(f"  ⚠️  JavaScript 함수 호출 실패, 버튼 클릭으로 시도: {e}")
        
        # 방법 2: 버튼을 정확하게 찾아서 클릭
        btn = None
        
        # 우선순위 1: CSS 선택자로 클래스와 텍스트로 찾기 (가장 정확)
        try:
            all_buttons = driver.find_elements(By.CSS_SELECTOR, "button.ifdata-search-result")
            for button in all_buttons:
                if button.text.strip() == "EXCEL 다운" and button.is_displayed():
                    btn = button
                    log(f"  🔍 CSS 선택자로 버튼 발견: button.ifdata-search-result")
                    break
        except Exception as e:
            log(f"  ⚠️  CSS 선택자로 찾기 실패: {e}")
        
        # 우선순위 2: XPath 선택자로 찾기
        if not btn:
            selectors = [
                "//button[@class='ifdata-search-result' and normalize-space(text())='EXCEL 다운']",
                "//button[contains(@onclick, 'fnExcelDown')]",
                "//button[contains(@onclick, 'Excel')]",
                "//button[normalize-space(text())='EXCEL 다운']",
                "//button[contains(text(), 'EXCEL 다운')]",
            ]
            
            for selector in selectors:
                try:
                    btn = driver.find_element(By.XPATH, selector)
                    # 버튼 텍스트 재확인
                    btn_text = btn.text.strip()
                    if btn_text == "EXCEL 다운" and btn.is_displayed():
                        log(f"  🔍 XPath로 버튼 발견: {selector}")
                        break
                    else:
                        btn = None
                except:
                    continue
        
        # 우선순위 3: JavaScript로 직접 찾고 클릭
        if not btn:
            try:
                # JavaScript로 버튼을 찾아서 직접 클릭
                clicked = driver.execute_script("""
                    var buttons = document.querySelectorAll('button.ifdata-search-result');
                    for (var i = 0; i < buttons.length; i++) {
                        if (buttons[i].textContent.trim() === 'EXCEL 다운') {
                            buttons[i].scrollIntoView({block: 'center', behavior: 'smooth'});
                            buttons[i].click();
                            return true;
                        }
                    }
                    return false;
                """)
                if clicked:
                    log(f"  ✅ JavaScript로 버튼 찾아서 클릭 완료")
                    # Alert 확인 및 다운로드 시작 확인
                    alert_shown = False
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
                        alert_shown = True
                    except Exception as e:
                        if str(e) == "DOWNLOAD_LIMIT_100" or str(e) == "NO_DATA_AVAILABLE":
                            raise
                        pass
                    
                    # 다운로드 시작 확인
                    time.sleep(1.0)
                    download_started = False
                    try:
                        current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
                        crdownloads = [f for f in current_files if f.suffix == '.crdownload' and f not in baseline_files]
                        if crdownloads:
                            download_started = True
                            log(f"  📥 다운로드 시작 확인: .crdownload 파일 발견")
                        excel_files = [f for f in current_files if f.suffix.lower() in ['.xls', '.xlsx'] and f not in baseline_files]
                        if excel_files:
                            download_started = True
                            log(f"  📥 다운로드 시작 확인: 새 엑셀 파일 발견")
                    except:
                        pass
                    
                    if not download_started and not alert_shown:
                        log(f"  ⚠️  다운로드 시작 신호가 보이지 않습니다. 계속 대기합니다...")
                    
                    return True
            except Exception as e:
                if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                    raise
                log(f"  ⚠️  JavaScript로 찾기/클릭 실패: {e}")
        
        if not btn:
            # 최종 시도: 모든 버튼을 순회하며 찾기
            try:
                all_buttons = driver.find_elements(By.TAG_NAME, "button")
                for button in all_buttons:
                    try:
                        if button.text.strip() == "EXCEL 다운" and button.is_displayed():
                            btn = button
                            log(f"  🔍 모든 버튼 순회로 발견")
                            break
                    except:
                        continue
            except Exception as e:
                log(f"  ⚠️  버튼 순회 실패: {e}")
        
        if not btn:
            raise Exception("EXCEL 다운 버튼을 찾을 수 없습니다")
        
        # 버튼이 보이도록 스크롤
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", btn)
            time.sleep(0.5)
        except:
            pass
        
        # JavaScript로 직접 클릭 (다른 요소를 건드리지 않도록)
        try:
            driver.execute_script("arguments[0].click();", btn)
            log(f"  ✅ 버튼 클릭 완료 (JavaScript)")
        except:
            # JavaScript 클릭 실패 시 일반 클릭 시도
            try:
                btn.click()
                log(f"  ✅ 버튼 클릭 완료 (일반 클릭)")
            except Exception as e:
                log(f"  ⚠️  클릭 실패, onclick 직접 호출 시도: {e}")
                # onclick 속성이 있으면 직접 호출
                onclick_attr = btn.get_attribute("onclick")
                if onclick_attr and "fnExcelDown" in onclick_attr:
                    driver.execute_script("fnExcelDown();")
                    log(f"  ✅ onclick 직접 호출 완료")
                else:
                    raise Exception(f"버튼 클릭 실패: {e}")
        
        # Alert 확인 및 다운로드 시작 확인
        alert_shown = False
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
            alert_shown = True
        except Exception as e:
            if str(e) == "DOWNLOAD_LIMIT_100" or str(e) == "NO_DATA_AVAILABLE":
                raise
            # Alert가 없으면 다운로드가 시작되었을 수 있음
            pass
        
        # 다운로드 시작 확인 (1초 대기 후 .crdownload 파일이나 새 파일 확인)
        time.sleep(1.0)
        download_started = False
        try:
            current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
            crdownloads = [f for f in current_files if f.suffix == '.crdownload' and f not in baseline_files]
            if crdownloads:
                download_started = True
                log(f"  📥 다운로드 시작 확인: .crdownload 파일 발견")
            excel_files = [f for f in current_files if f.suffix.lower() in ['.xls', '.xlsx'] and f not in baseline_files]
            if excel_files:
                download_started = True
                log(f"  📥 다운로드 시작 확인: 새 엑셀 파일 발견")
        except:
            pass
        
        if not download_started and not alert_shown:
            log(f"  ⚠️  다운로드 시작 신호가 보이지 않습니다. 계속 대기합니다...")
        
        log(f"  ✅ EXCEL 다운 버튼 클릭 완료")
        return True
    except Exception as e:
        if "DOWNLOAD_LIMIT_100" in str(e):
            raise  # 100건 제한은 상위로 전달
        if "NO_DATA_AVAILABLE" in str(e):
            raise  # 데이터 없음은 상위로 전달
        log(f"  ❌ 다운 버튼 클릭 실패: {e}")
        import traceback
        traceback.print_exc()
        return False

def wait_for_download(timeout: int = 15, baseline_files: set = None, expected_year: int = None, expected_month: int = None) -> Optional[Path]:
    """다운로드 완료 대기 - 개선된 감지 로직 (즉시 감지 시작)"""
    start_time = time.time()
    
    # baseline이 없으면 현재 파일 목록 사용
    if baseline_files is None:
        baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
    
    log(f"  ⏳ 다운로드 대기 중... (최대 {timeout}초)")
    log(f"  📁 감시 폴더: {TEMP_DOWNLOAD_DIR.absolute()}")
    log(f"  📊 기존 파일: {len(baseline_files)}개")
    if expected_year and expected_month:
        log(f"  🎯 예상 파일: {expected_year:04d}-{expected_month:02d} 데이터")
    
    # 초기 대기 시간 제거 - 즉시 감지 시작
    # 다운로드가 시작되면 .crdownload 파일이나 새 파일이 즉시 나타날 수 있음
    
    found_crdownload = False
    found_any_file = False
    last_check_time = start_time
    last_size = {}
    stable_count = {}  # 파일 크기가 안정된 횟수
    no_file_warning_shown = False
    
    while time.time() - start_time < timeout:
        elapsed = time.time() - start_time
        elapsed_int = int(elapsed)
        current_time = time.time()
        
        # 처음 5초는 0.1초마다, 그 이후는 0.2초마다 체크
        check_interval = 0.1 if elapsed < 5.0 else 0.2
        if current_time - last_check_time < check_interval:
            time.sleep(0.05)
            continue
        last_check_time = current_time
        
        # 현재 폴더의 모든 파일
        current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
        
        # .crdownload 파일 확인 (다운로드 진행 중)
        crdownloads = [f for f in current_files if f.suffix == '.crdownload']
        if crdownloads:
            found_crdownload = True
            found_any_file = True
            # 가장 최근 .crdownload 파일
            latest_crdownload = max(crdownloads, key=lambda p: p.stat().st_mtime)
            size = latest_crdownload.stat().st_size
            if elapsed_int % 2 == 0 and elapsed_int > 0:
                log(f"  ⏳ 다운로드 진행중... ({elapsed_int}초, {size:,} bytes)")
            continue
        
        # 엑셀 파일 찾기 - 새 파일만
        excel_files = [
            f for f in current_files 
            if f.is_file() 
            and f.suffix.lower() in ['.xls', '.xlsx']
            and f not in baseline_files  # 기존 파일 제외
        ]
        
        if excel_files:
            found_any_file = True
            # 가장 최근 파일 (mtime 기준) - 우리가 방금 요청한 파일일 가능성이 높음
            latest = max(excel_files, key=lambda p: p.stat().st_mtime)
            size = latest.stat().st_size
            
            # 파일이 있고 크기가 1KB 이상이면
            if size > 1000:
                file_key = str(latest)
                
                # 크기 안정화 확인 (연속으로 3번 같은 크기면 안정화된 것으로 간주)
                if file_key in last_size:
                    if last_size[file_key] == size:
                        stable_count[file_key] = stable_count.get(file_key, 0) + 1
                    else:
                        # 크기가 변했으면 카운트 리셋
                        stable_count[file_key] = 0
                        last_size[file_key] = size
                else:
                    last_size[file_key] = size
                    stable_count[file_key] = 0
                
                # 크기가 3번 연속 같으면 안정화된 것으로 간주 (약 0.6초)
                if stable_count.get(file_key, 0) >= 3:
                    # 파일이 우리가 요청한 파일인지 검증 (생성 시간으로 확인)
                    file_mtime = latest.stat().st_mtime
                    time_diff = file_mtime - start_time
                    
                    # 파일이 다운로드 시작 후 30초 이내에 생성되었으면 우리가 요청한 파일로 간주
                    if time_diff >= -5 and time_diff <= 30:
                        log(f"  ✅ 다운로드 완료: {latest.name} ({size:,} bytes, 생성: {time_diff:.1f}초 전)")
                        return latest
                    else:
                        # 너무 오래된 파일이면 다른 파일일 수 있음
                        if elapsed_int % 3 == 0:
                            log(f"  ⚠️  파일 발견했지만 생성 시간이 이상함: {latest.name} (생성: {time_diff:.1f}초 전)")
                else:
                    # 아직 크기가 변하는 중
                    if elapsed_int % 2 == 0:
                        log(f"  📝 파일 쓰기 중... ({size:,} bytes, 안정화 대기: {stable_count.get(file_key, 0)}/3)")
        
        # 다운로드가 시작되지 않았을 때 경고 메시지 (한 번만)
        if not found_any_file and elapsed_int >= 3 and not no_file_warning_shown:
            log(f"  ⚠️  다운로드가 시작되지 않은 것 같습니다. ({elapsed_int}초 경과)")
            log(f"     - 다운로드 폴더 확인: {TEMP_DOWNLOAD_DIR.absolute()}")
            log(f"     - 브라우저의 다운로드 설정을 확인하세요")
            no_file_warning_shown = True
    
    # 타임아웃
    log(f"  ⏱️  타임아웃 ({timeout}초)")
    
    # 디버깅: 새 파일이 있는지 확인
    all_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
    new_files = [f for f in all_files if f not in baseline_files]
    
    if new_files:
        log(f"  🆕 새 파일 발견: {len(new_files)}개")
        for f in new_files:
            file_mtime = f.stat().st_mtime
            time_diff = file_mtime - start_time
            log(f"     - {f.name} ({f.stat().st_size:,} bytes, 생성: {time_diff:.1f}초 전)")
        
        # 가장 최근 파일이라도 반환 (검증 실패했지만 파일은 있음)
        latest = max(new_files, key=lambda p: p.stat().st_mtime)
        if latest.suffix.lower() in ['.xls', '.xlsx']:
            log(f"  ⚠️  검증 실패했지만 가장 최근 파일 반환: {latest.name}")
            return latest
    else:
        log(f"  ⚠️  새 파일 없음 (전체 {len(all_files)}개)")
    
    return None

def download_single_month_with_retry(driver, property_type: str, start_date: date, end_date: date, max_retries: int = 3) -> Optional[Path]:
    """단일 월 다운로드 - 재시도 포함"""
    year = start_date.year
    month = start_date.month
    
    log(f"\n{'='*60}")
    log(f"📅 {property_type} {year}년 {month}월")
    log(f"{'='*60}")
    
    # temp 폴더 정리 (이전 실패 파일 제거)
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
        
        # 첫 번째 시도 전 페이지 준비 상태 확인
        if attempt == 1:
            # 날짜 입력 필드가 준비될 때까지 반복 확인
            date_field_ready = False
            for wait_attempt in range(3):  # 최대 3번 시도 (총 3초)
                try:
                    date_field = driver.find_element(By.CSS_SELECTOR, "#srchBgnDe")
                    if date_field.is_displayed() and date_field.is_enabled():
                        date_field_ready = True
                        break
                except:
                    pass
                if wait_attempt < 2:  # 마지막 시도가 아니면 대기
                    time.sleep(1.0)
            
            if not date_field_ready:
                log(f"  ⏳ 페이지 준비 대기 중... (날짜 입력 필드 확인 실패)")
                time.sleep(2.0)
        
        # 날짜 설정
        if not set_dates(driver, start_date, end_date):
            if attempt < max_retries:
                log(f"  ⏳ 5초 대기 후 재시도...")
                time.sleep(5)
                continue
            return None
        
        # 날짜 설정 후 Alert 확인 (데이터 없음 체크)
        try:
            try_accept_alert(driver, 2.0)
        except Exception as e:
            if "NO_DATA_AVAILABLE" in str(e):
                log(f"  ⏭️  데이터 없음, 스킵")
                return None  # 데이터 없음은 None 반환
            elif "DOWNLOAD_LIMIT_100" in str(e):
                raise  # 100건 제한은 상위로 전달
        
        # 날짜 설정 후 페이지 반영 대기 (첫 번째 시도에서는 더 길게)
        if attempt == 1:
            time.sleep(3.0)  # 첫 번째 시도: 3초 대기
        else:
            time.sleep(2.0)  # 재시도: 2초 대기
        
        # 다운로드 클릭 직전 파일 목록 저장
        baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        
        # 다운로드 클릭
        try:
            if not click_excel_download(driver, baseline_files=baseline_files):
                if attempt < max_retries:
                    log(f"  ⏳ 5초 대기 후 재시도...")
                    time.sleep(5)
                    continue
                return None
        except Exception as e:
            if "NO_DATA_AVAILABLE" in str(e):
                log(f"  ⏭️  데이터 없음, 스킵")
                return None  # 데이터 없음은 None 반환
            elif "DOWNLOAD_LIMIT_100" in str(e):
                raise  # 100건 제한은 상위로 전달
            if attempt < max_retries:
                log(f"  ⏳ 5초 대기 후 재시도...")
                time.sleep(5)
                continue
            return None
        
        # 다운로드 대기 (15초 - 서버 응답 지연 및 파일 생성 시간 고려)
        # 다운로드 버튼 클릭 직후이므로 즉시 감지 시작
        downloaded = wait_for_download(timeout=15, baseline_files=baseline_files, expected_year=year, expected_month=month)
        
        if downloaded:
            # 성공! 파일 반환
            return downloaded
        else:
            # 실패
            if attempt < max_retries:
                log(f"  ⏳ 5초 대기 후 재시도...")
                time.sleep(5)
            else:
                log(f"  ❌ {max_retries}회 시도 모두 실패")
                return None
    
    return None

def move_and_rename_file(downloaded_file: Path, property_type: str, year: int, month: int) -> Path:
    """다운로드 파일을 목적지로 이동 및 이름 변경, 전처리 후 저장"""
    # 폴더 생성
    folder_name = sanitize_folder_name(property_type)
    dest_dir = DOWNLOAD_DIR / folder_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # 파일명: 아파트 200601.xlsx
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    dest_path = dest_dir / filename
    
    # CI 환경에서는 임시 파일로 전처리 후 Google Drive에 업로드
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
        # 로컬 환경: 파일 이동 (덮어쓰기)
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
    parser = argparse.ArgumentParser(description='최근 3개월치 부동산 실거래 데이터 다운로드 및 전처리')
    parser.add_argument('--update-mode', action='store_true', 
                       help='업데이트 모드 (최근 3개월치만 다운로드)')
    args = parser.parse_args()
    
    log("="*70)
    log("🚀 최근 3개월치 데이터 다운로드 및 전처리 시작")
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
    log("")
    
    driver = build_driver()
    
    try:
        # 페이지 로드
        log("🌐 사이트 접속 중...")
        driver.get(MOLIT_URL)
        time.sleep(5)  # 로딩 대기
        try_accept_alert(driver, 2.0)
        
        # Google Translate 팝업 제거
        remove_google_translate_popup(driver)
        
        log(f"✅ 접속 완료: {driver.current_url}\n")
        
        # 전체 통계
        total_success = 0
        total_fail = 0
        
        # 7개 탭 처리
        for prop_idx, property_type in enumerate(PROPERTY_TYPES, 1):
            log("="*70)
            log(f"📊 [{prop_idx}/{len(PROPERTY_TYPES)}] {property_type}")
            log("="*70)
            
            # 탭 선택
            if not select_property_tab(driver, property_type):
                log(f"⚠️  탭 선택 실패, 다음 종목으로...")
                continue
            
            # 최근 3개월 처리
            success_count = 0
            fail_count = 0
            
            for month_idx, (year, month) in enumerate(recent_months, 1):
                log(f"\n[{month_idx}/{len(recent_months)}]", end=" ")
                
                # 두 번째 다운로드부터는 페이지를 재로드하고 탭을 다시 선택 (안정성 향상)
                if month_idx > 1:
                    retry_count = 0
                    tab_selected = False
                    while retry_count < 3 and not tab_selected:
                        try:
                            log(f"  🔄 페이지 재로딩 및 탭 재선택... (시도 {retry_count + 1}/3)")
                            driver.get(MOLIT_URL)
                            time.sleep(3)
                            try_accept_alert(driver, 2.0)
                            if select_property_tab(driver, property_type):
                                tab_selected = True
                                # 탭 선택 후 페이지가 완전히 준비될 때까지 대기
                                # 날짜 입력 필드가 준비될 때까지 반복 확인
                                date_field_ready = False
                                for wait_attempt in range(5):  # 최대 5번 시도 (총 5초)
                                    try:
                                        date_field = driver.find_element(By.CSS_SELECTOR, "#srchBgnDe")
                                        if date_field.is_displayed() and date_field.is_enabled():
                                            date_field_ready = True
                                            log(f"  ✅ 페이지 준비 완료 ({wait_attempt + 1}번째 시도)")
                                            break
                                    except:
                                        pass
                                    time.sleep(1.0)
                                
                                if not date_field_ready:
                                    log(f"  ⚠️  날짜 입력 필드 확인 실패, 계속 진행...")
                                else:
                                    # 추가 안정화 대기
                                    time.sleep(1.0)
                            else:
                                retry_count += 1
                                if retry_count < 3:
                                    time.sleep(2)
                        except Exception as e:
                            log(f"  ⚠️  페이지 재설정 실패: {e}")
                            retry_count += 1
                            if retry_count < 3:
                                time.sleep(2)
                    
                    if not tab_selected:
                        log(f"  ❌ 탭 재선택 실패, 다운로드 시도 계속...")
                
                # 날짜 범위 계산
                start_date = date(year, month, 1)
                if month == 12:
                    end_date = date(year + 1, 1, 1) - timedelta(days=1)
                else:
                    end_date = date(year, month + 1, 1) - timedelta(days=1)
                
                # 다운로드 시도 (최대 3회 재시도)
                downloaded_file = download_single_month_with_retry(driver, property_type, start_date, end_date, max_retries=3)
                
                if downloaded_file:
                    # 파일 이동 및 전처리
                    try:
                        dest_path = move_and_rename_file(downloaded_file, property_type, year, month)
                        
                        # 다운로드 성공 후 temp 폴더 정리 (남은 임시 파일 제거)
                        try:
                            for temp_file in TEMP_DOWNLOAD_DIR.glob("*"):
                                try:
                                    if temp_file.is_file():
                                        temp_file.unlink()
                                except:
                                    pass
                        except:
                            pass
                        
                        if dest_path or IS_CI:
                            success_count += 1
                            log(f"✅ 완료: {property_type} {year:04d}{month:02d}")
                    except Exception as e:
                        log(f"  ❌ 파일 이동/전처리 실패: {e}")
                        fail_count += 1
                else:
                    fail_count += 1
                    log(f"⚠️  다운로드 실패: {property_type} {year:04d}{month:02d}")
                
                # 다음 요청 전 대기 (서버 부하 방지 및 요청 간격 확보)
                time.sleep(5)
            
            log(f"\n✅ {property_type} 완료")
            log(f"   성공: {success_count}, 실패: {fail_count}")
            total_success += success_count
            total_fail += fail_count
            
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
            log("⏰ 내일 같은 명령어로 실행하면 이어서 진행됩니다.")
            log("="*70)
        elif isinstance(e, KeyboardInterrupt):
            log("\n⚠️  사용자 중단")
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
