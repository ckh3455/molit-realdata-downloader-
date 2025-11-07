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
from selenium.common.exceptions import UnexpectedAlertPresentException

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
    """크롬 드라이버 생성 (Chrome DevTools Protocol 활성화)"""
    opts = Options()
    # CI 환경 확인 (더 확실하게)
    is_ci_env = os.getenv("CI") == "1" or os.getenv("GITHUB_ACTIONS") == "true"
    
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
    
    # Chrome DevTools Protocol 활성화 (디버깅용)
    # 로컬 환경에서만 디버깅 포트 활성화
    if not is_ci_env:
        opts.add_argument("--remote-debugging-port=9222")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option('useAutomationExtension', False)
        log("🔧 Chrome DevTools Protocol 활성화 (포트 9222)")
        log("   브라우저 상태 확인: http://localhost:9222")
    
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
                log(f"  💾 진행 상황이 저장되었습니다.")
                log(f"  ⏰ 내일 다시 실행하면 이어서 진행됩니다.")
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
        remove_google_translate_popup(driver)
    
    # 페이지가 완전히 로드될 때까지 대기
    time.sleep(3)
    try_accept_alert(driver, 2.0)
    
    # Google Translate 팝업 제거
    remove_google_translate_popup(driver)
    
    # 페이지 URL 재확인 (다른 페이지로 이동했을 수 있음)
    if "xls.do" not in driver.current_url:
        log(f"  ⚠️  페이지가 xls.do가 아닙니다. 다시 로딩... ({driver.current_url})")
        driver.get(MOLIT_URL)
        time.sleep(5)
        try_accept_alert(driver, 2.0)
        remove_google_translate_popup(driver)
    
    # 탭 요소가 나타날 때까지 명시적으로 대기
    log(f"  ⏳ 탭 요소 로딩 대기 중...")
    tab_loaded = False
    for wait_attempt in range(15):  # 최대 15초 대기
        try:
            # 탭 컨테이너나 탭 요소가 있는지 확인
            tab_container = driver.find_elements(By.CSS_SELECTOR, "ul.quarter-tab-cover")
            if tab_container:
                # 탭 링크가 있는지 확인
                tab_links = driver.find_elements(By.CSS_SELECTOR, "ul.quarter-tab-cover a")
                if tab_links:
                    tab_loaded = True
                    log(f"  ✅ 탭 요소 로딩 완료 ({wait_attempt + 1}번째 시도)")
                    break
        except:
            pass
        time.sleep(1.0)
    
    if not tab_loaded:
        log(f"  ⚠️  탭 요소를 찾을 수 없지만 계속 진행...")
        # 추가 대기
        time.sleep(3.0)
    
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
    
    # 첫 번째 시도 전에 탭 구조 확인 (디버깅)
    if not hasattr(select_property_tab, '_inspected'):
        log(f"  🔍 페이지 로드 후 탭 구조 확인 중...")
        inspect_tabs_and_selectors(driver)
        select_property_tab._inspected = True
    
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
                
                # ✅ 추가: 페이지가 완전히 준비될 때까지 대기
                log(f"  ⏳ 탭 선택 후 페이지 준비 대기 중...")
                
                # 날짜 입력 필드가 준비될 때까지 반복 확인 (최대 2초)
                date_field_ready = False
                for wait_attempt in range(2):  # 최대 2번 시도 (총 2초)
                    try:
                        date_field = driver.find_element(By.CSS_SELECTOR, "#srchBgnDe")
                        if date_field.is_displayed() and date_field.is_enabled():
                            # 추가 검증: 값이 설정 가능한지 확인
                            try:
                                driver.execute_script("arguments[0].value = '2024-01-01';", date_field)
                                driver.execute_script("arguments[0].value = '';", date_field)
                                date_field_ready = True
                                log(f"  ✅ 페이지 준비 완료 ({wait_attempt + 1}번째 시도)")
                                break
                            except:
                                pass
                    except:
                        pass
                    time.sleep(1.0)
                
                if not date_field_ready:
                    log(f"  ⚠️  날짜 입력 필드 확인 실패, 계속 진행...")
                else:
                    # 추가 안정화 대기 (폼이 완전히 초기화될 시간)
                    time.sleep(1.0)
                
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
                    
                    # ✅ 추가: 페이지가 완전히 준비될 때까지 대기
                    log(f"  ⏳ 탭 선택 후 페이지 준비 대기 중...")
                    
                    # 날짜 입력 필드가 준비될 때까지 반복 확인 (최대 2초)
                    date_field_ready = False
                    for wait_attempt in range(2):  # 최대 2번 시도 (총 2초)
                        try:
                            date_field = driver.find_element(By.CSS_SELECTOR, "#srchBgnDe")
                            if date_field.is_displayed() and date_field.is_enabled():
                                # 추가 검증: 값이 설정 가능한지 확인
                                try:
                                    driver.execute_script("arguments[0].value = '2024-01-01';", date_field)
                                    driver.execute_script("arguments[0].value = '';", date_field)
                                    date_field_ready = True
                                    log(f"  ✅ 페이지 준비 완료 ({wait_attempt + 1}번째 시도)")
                                    break
                                except:
                                    pass
                        except:
                            pass
                        time.sleep(1.0)
                    
                    if not date_field_ready:
                        log(f"  ⚠️  날짜 입력 필드 확인 실패, 계속 진행...")
                    else:
                        # 추가 안정화 대기 (폼이 완전히 초기화될 시간)
                        time.sleep(1.0)
                    
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
    
    # 방법 2: 모든 링크를 찾아서 텍스트로 비교
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
    
    # 방법 3: CSS 선택자로 시도
    try:
        log(f"  🔍 CSS 선택자 시도...")
        css_selectors = [
            f"a:contains('{tab_name}')",  # 일부 브라우저에서만 작동
            f"a[href*='{tab_name.lower()}']",
        ]
        
        # CSS 선택자 대신 JavaScript로 찾기
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
    
    # 모든 방법 실패 시 탭 구조 재확인
    log(f"  ❌ 탭 선택 실패: 모든 방법 시도 완료")
    log(f"  🔍 실패 원인 분석을 위해 탭 구조 재확인 중...")
    inspect_tabs_and_selectors(driver)
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
    """EXCEL 다운 버튼 클릭 - fnExcelDown() 함수 호출 (창 변화 대응)"""
    try:
        # Google Translate 팝업 강제 제거/숨김
        remove_google_translate_popup(driver)
        
        # baseline_files가 없으면 현재 파일 목록 사용
        if baseline_files is None:
            baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        
        # 방법 1: JavaScript 함수 직접 호출 (가장 안전 - 창 변화에 영향 없음)
        try:
            # fnExcelDown 함수가 준비되었는지 확인 (최대 3초 대기)
            fn_ready = False
            for wait_attempt in range(6):  # 0.5초씩 6번 = 최대 3초
                fn_ready = driver.execute_script("return typeof fnExcelDown === 'function';")
                if fn_ready:
                    break
                if wait_attempt < 5:
                    time.sleep(0.5)
            
            if fn_ready:
                # 함수 호출과 Alert 처리, 다운로드 확인을 하나의 스크립트로 실행
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
                    log(f"  ✅ EXCEL 다운 버튼 클릭 (JavaScript 함수 직접 호출)")
                    # Alert 확인 (즉시)
                    alert_shown = False
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
                        alert_shown = True
                    except Exception as e:
                        if str(e) == "DOWNLOAD_LIMIT_100" or str(e) == "NO_DATA_AVAILABLE":
                            raise
                        # Alert가 없으면 다운로드가 시작되었을 수 있음
                        pass
                    
                    return True
            else:
                log(f"  ⚠️  fnExcelDown 함수를 찾을 수 없습니다")
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                raise
            log(f"  ⚠️  JavaScript 함수 호출 실패, 버튼 클릭으로 시도: {e}")
        
        # 방법 2: JavaScript로 버튼을 찾아서 즉시 클릭 (원자적 작업)
        try:
            clicked = driver.execute_script("""
                // 버튼을 찾고 즉시 클릭 (창이 변하기 전에)
                var buttons = document.querySelectorAll('button.ifdata-search-result');
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    if (btn.textContent.trim() === 'EXCEL 다운' && btn.offsetParent !== null) {
                        // 스크롤과 클릭을 한 번에
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        btn.click();
                        return true;
                    }
                }
                // CSS 선택자로 못 찾으면 XPath 시도
                var xpathButtons = document.evaluate(
                    "//button[contains(text(), 'EXCEL 다운')]",
                    document,
                    null,
                    XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
                    null
                );
                for (var i = 0; i < xpathButtons.snapshotLength; i++) {
                    var btn = xpathButtons.snapshotItem(i);
                    if (btn.offsetParent !== null) {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        btn.click();
                        return true;
                    }
                }
                return false;
            """)
            
            if clicked:
                log(f"  ✅ JavaScript로 버튼 찾아서 클릭 완료")
                # Alert 확인 (즉시)
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
                
                return True
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                raise
            log(f"  ⚠️  JavaScript로 찾기/클릭 실패: {e}")
        
        # 방법 3: 버튼을 찾은 직후 바로 클릭 (StaleElementReferenceException 방지)
        from selenium.common.exceptions import StaleElementReferenceException
        
        # 여러 선택자로 시도
        selectors = [
            (By.CSS_SELECTOR, "button.ifdata-search-result"),
            (By.XPATH, "//button[@class='ifdata-search-result' and normalize-space(text())='EXCEL 다운']"),
            (By.XPATH, "//button[contains(@onclick, 'fnExcelDown')]"),
            (By.XPATH, "//button[normalize-space(text())='EXCEL 다운']"),
            (By.XPATH, "//button[contains(text(), 'EXCEL 다운')]"),
        ]
        
        for by, selector in selectors:
            try:
                # 버튼을 찾고 즉시 클릭 (저장하지 않음)
                btn = driver.find_element(by, selector)
                if btn.is_displayed() and btn.is_enabled():
                    # 찾은 직후 바로 클릭 (대기 없이)
                    driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'instant'}); arguments[0].click();", btn)
                    log(f"  ✅ 버튼 클릭 완료 ({by}, {selector})")
                    
                    # Alert 확인 (즉시)
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
                    
                    return True
            except StaleElementReferenceException:
                # 요소가 무효화되었으면 다음 방법 시도
                continue
            except Exception as e:
                # 다른 오류면 다음 선택자 시도
                continue
        
        # 방법 4: 모든 버튼을 순회하며 찾고 즉시 클릭
        try:
            all_buttons = driver.find_elements(By.TAG_NAME, "button")
            for button in all_buttons:
                try:
                    if button.text.strip() == "EXCEL 다운" and button.is_displayed() and button.is_enabled():
                        # 찾은 직후 바로 클릭
                        driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'instant'}); arguments[0].click();", button)
                        log(f"  ✅ 모든 버튼 순회로 클릭 완료")
                        
                        # Alert 확인 (즉시)
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
                        
                        return True
                except StaleElementReferenceException:
                    continue
                except:
                    continue
        except Exception as e:
            log(f"  ⚠️  버튼 순회 실패: {e}")
        
        # 모든 방법 실패 시 버튼 구조 재확인
        log(f"  ❌ EXCEL 다운 버튼을 찾을 수 없습니다")
        log(f"  🔍 실패 원인 분석을 위해 버튼 구조 재확인 중...")
        inspect_tabs_and_selectors(driver)
        raise Exception("EXCEL 다운 버튼을 찾을 수 없습니다")
        
    except Exception as e:
        if "DOWNLOAD_LIMIT_100" in str(e):
            raise  # 100건 제한은 상위로 전달
        if "NO_DATA_AVAILABLE" in str(e):
            raise  # 데이터 없음은 상위로 전달
        log(f"  ❌ 다운 버튼 클릭 실패: {e}")
        import traceback
        traceback.print_exc()
        return False

def inspect_tabs_and_selectors(driver):
    """Chrome DevTools를 이용하여 탭과 선택자 상세 확인"""
    try:
        log("  🔍 Chrome DevTools로 탭과 선택자 상세 분석 중...")
        
        # 탭 구조 상세 분석
        tab_info = driver.execute_script("""
            var tabs = [];
            var tabContainers = document.querySelectorAll('ul.quarter-tab-cover, .quarter-tab-cover, [class*="tab"]');
            
            // 모든 링크 찾기
            var allLinks = document.querySelectorAll('a');
            for (var i = 0; i < allLinks.length; i++) {
                var link = allLinks[i];
                var text = link.textContent.trim();
                var id = link.id || '';
                var href = link.href || '';
                var className = link.className || '';
                var parent = link.parentElement;
                var parentClass = parent ? parent.className || '' : '';
                var parentId = parent ? parent.id || '' : '';
                
                // 탭 관련 링크만 필터링
                if (text && (text.includes('아파트') || text.includes('연립') || text.includes('다세대') || 
                    text.includes('단독') || text.includes('다가구') || text.includes('오피스텔') || 
                    text.includes('토지') || text.includes('상업') || text.includes('업무') || 
                    text.includes('공장') || text.includes('창고') || 
                    id.includes('Tab') || className.includes('tab') || parentClass.includes('tab'))) {
                    tabs.push({
                        text: text,
                        id: id,
                        href: href,
                        className: className,
                        parentClass: parentClass,
                        parentId: parentId,
                        visible: link.offsetParent !== null,
                        enabled: !link.disabled,
                        onclick: link.onclick ? link.onclick.toString().substring(0, 100) : null,
                        xpath: getXPath(link)
                    });
                }
            }
            
            // XPath 계산 함수
            function getXPath(element) {
                if (element.id !== '') {
                    return '//*[@id="' + element.id + '"]';
                }
                if (element === document.body) {
                    return '/html/body';
                }
                var ix = 0;
                var siblings = element.parentNode.childNodes;
                for (var i = 0; i < siblings.length; i++) {
                    var sibling = siblings[i];
                    if (sibling === element) {
                        return getXPath(element.parentNode) + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                    }
                    if (sibling.nodeType === 1 && sibling.tagName === element.tagName) {
                        ix++;
                    }
                }
            }
            
            return {
                tabs: tabs,
                tabContainers: Array.from(tabContainers).map(container => ({
                    tagName: container.tagName,
                    id: container.id || '',
                    className: container.className || '',
                    childrenCount: container.children.length
                }))
            };
        """)
        
        log(f"  📋 탭 구조 분석 결과:")
        log(f"     탭 컨테이너: {len(tab_info.get('tabContainers', []))}개")
        for container in tab_info.get('tabContainers', []):
            log(f"       - {container.get('tagName')} (id: {container.get('id')}, class: {container.get('className')}, 자식: {container.get('childrenCount')}개)")
        
        tabs = tab_info.get('tabs', [])
        log(f"     발견된 탭 링크: {len(tabs)}개")
        for idx, tab in enumerate(tabs, 1):
            log(f"     [{idx}] '{tab.get('text')}'")
            log(f"         ID: {tab.get('id') or '(없음)'}")
            log(f"         Class: {tab.get('className') or '(없음)'}")
            log(f"         부모 Class: {tab.get('parentClass') or '(없음)'}")
            log(f"         부모 ID: {tab.get('parentId') or '(없음)'}")
            log(f"         표시: {tab.get('visible')}, 활성: {tab.get('enabled')}")
            log(f"         XPath: {tab.get('xpath', 'N/A')}")
        
        # 버튼 구조 상세 분석
        button_info = driver.execute_script("""
            var buttons = [];
            var allButtons = document.querySelectorAll('button');
            
            for (var i = 0; i < allButtons.length; i++) {
                var btn = allButtons[i];
                var text = btn.textContent.trim();
                
                // EXCEL 다운 관련 버튼만
                if (text.includes('EXCEL') || text.includes('다운') || 
                    btn.className.includes('search-result') || 
                    (btn.onclick && btn.onclick.toString().includes('Excel'))) {
                    buttons.push({
                        text: text,
                        id: btn.id || '',
                        className: btn.className || '',
                        onclick: btn.onclick ? btn.onclick.toString().substring(0, 150) : null,
                        visible: btn.offsetParent !== null,
                        enabled: !btn.disabled,
                        xpath: getXPath(btn)
                    });
                }
            }
            
            function getXPath(element) {
                if (element.id !== '') {
                    return '//*[@id="' + element.id + '"]';
                }
                if (element === document.body) {
                    return '/html/body';
                }
                var ix = 0;
                var siblings = element.parentNode.childNodes;
                for (var i = 0; i < siblings.length; i++) {
                    var sibling = siblings[i];
                    if (sibling === element) {
                        return getXPath(element.parentNode) + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                    }
                    if (sibling.nodeType === 1 && sibling.tagName === element.tagName) {
                        ix++;
                    }
                }
            }
            
            return buttons;
        """)
        
        log(f"  🔘 EXCEL 다운 버튼 분석 결과:")
        log(f"     발견된 버튼: {len(button_info)}개")
        for idx, btn in enumerate(button_info, 1):
            log(f"     [{idx}] '{btn.get('text')}'")
            log(f"         ID: {btn.get('id') or '(없음)'}")
            log(f"         Class: {btn.get('className') or '(없음)'}")
            log(f"         표시: {btn.get('visible')}, 활성: {btn.get('enabled')}")
            log(f"         onclick: {btn.get('onclick', 'N/A')[:100] if btn.get('onclick') else '(없음)'}")
            log(f"         XPath: {btn.get('xpath', 'N/A')}")
        
        # 현재 사용 중인 선택자 검증
        log(f"  ✅ 현재 코드의 선택자 검증:")
        
        # 탭 ID 매핑 검증
        TAB_ID_MAPPING = {
            "아파트": "xlsTab1",
            "연립다세대": "xlsTab2",
            "단독다가구": "xlsTab3",
            "오피스텔": "xlsTab4",
            "상업업무용": "xlsTab6",
            "토지": "xlsTab7",
            "공장창고등": "xlsTab8",
        }
        
        for tab_name, expected_id in TAB_ID_MAPPING.items():
            found = False
            for tab in tabs:
                if tab.get('id') == expected_id:
                    found = True
                    log(f"     ✅ {tab_name}: ID '{expected_id}' 발견됨")
                    break
            if not found:
                log(f"     ❌ {tab_name}: ID '{expected_id}' 없음")
        
        # 버튼 선택자 검증
        button_selectors = [
            "button.ifdata-search-result",
            "//button[@class='ifdata-search-result' and normalize-space(text())='EXCEL 다운']",
            "//button[contains(@onclick, 'fnExcelDown')]",
        ]
        
        for selector in button_selectors:
            try:
                if selector.startswith('//'):
                    # XPath
                    elements = driver.find_elements(By.XPATH, selector)
                else:
                    # CSS
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                
                if elements:
                    log(f"     ✅ 선택자 '{selector}': {len(elements)}개 발견")
                else:
                    log(f"     ❌ 선택자 '{selector}': 없음")
            except Exception as e:
                log(f"     ⚠️  선택자 '{selector}': 오류 - {e}")
        
    except Exception as e:
        log(f"  ⚠️  탭/선택자 분석 실패: {e}")
        import traceback
        traceback.print_exc()

def debug_browser_state(driver):
    """Chrome DevTools Protocol을 사용하여 브라우저 상태 디버깅"""
    try:
        # CDP 명령 사용 가능 여부 확인
        if hasattr(driver, 'execute_cdp_cmd'):
            log("  🔍 Chrome DevTools Protocol로 브라우저 상태 확인 중...")
            
            # 탭과 선택자 상세 분석
            inspect_tabs_and_selectors(driver)
            
            # 1. 콘솔 로그 확인
            try:
                logs = driver.get_log('browser')
                if logs:
                    log(f"  📋 브라우저 콘솔 로그 ({len(logs)}개):")
                    for log_entry in logs[-10:]:  # 최근 10개만
                        log(f"     [{log_entry['level']}] {log_entry['message']}")
                else:
                    log("  📋 브라우저 콘솔 로그: 없음")
            except Exception as e:
                log(f"  ⚠️  콘솔 로그 확인 실패: {e}")
            
            # 2. 네트워크 요청 확인
            try:
                # Performance.getMetrics로 네트워크 상태 확인
                metrics = driver.execute_cdp_cmd('Performance.getMetrics', {})
                if metrics:
                    log(f"  🌐 성능 메트릭: {metrics}")
            except Exception as e:
                pass
            
            # 3. 현재 페이지 상태 확인
            try:
                page_state = driver.execute_script("""
                    return {
                        readyState: document.readyState,
                        url: window.location.href,
                        title: document.title,
                        hasFnExcelDown: typeof fnExcelDown === 'function',
                        buttons: Array.from(document.querySelectorAll('button')).map(b => ({
                            text: b.textContent.trim(),
                            visible: b.offsetParent !== null,
                            enabled: !b.disabled,
                            onclick: b.onclick ? b.onclick.toString() : null
                        }))
                    };
                """)
                log(f"  📄 페이지 상태:")
                log(f"     URL: {page_state.get('url', 'N/A')}")
                log(f"     ReadyState: {page_state.get('readyState', 'N/A')}")
                log(f"     fnExcelDown 함수 존재: {page_state.get('hasFnExcelDown', False)}")
                buttons = page_state.get('buttons', [])
                excel_buttons = [b for b in buttons if 'EXCEL' in b.get('text', '').upper() or '다운' in b.get('text', '')]
                if excel_buttons:
                    log(f"     EXCEL 다운 버튼 ({len(excel_buttons)}개):")
                    for btn in excel_buttons:
                        log(f"       - 텍스트: '{btn.get('text')}', 표시: {btn.get('visible')}, 활성: {btn.get('enabled')}")
                else:
                    log(f"     EXCEL 다운 버튼: 없음")
            except Exception as e:
                log(f"  ⚠️  페이지 상태 확인 실패: {e}")
            
            # 4. 다운로드 설정 확인
            try:
                download_path = driver.execute_cdp_cmd('Browser.getDownloadPaths', {})
                log(f"  📥 다운로드 경로: {download_path}")
            except Exception as e:
                pass
            
        else:
            log("  ⚠️  Chrome DevTools Protocol을 사용할 수 없습니다 (Selenium 버전 확인 필요)")
    except Exception as e:
        log(f"  ⚠️  브라우저 상태 디버깅 실패: {e}")

def wait_for_download(timeout: int = 15, baseline_files: set = None, expected_year: int = None, expected_month: int = None, driver=None) -> Optional[Path]:
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
            # 로그 출력 빈도 줄이기: 5초마다만 출력
            if elapsed_int > 0 and elapsed_int % 5 == 0:
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
        
        # 다운로드가 시작되지 않았을 때 경고 메시지 (한 번만) - 10초 후에만 표시
        # elapsed는 실수이므로 10.0 이상일 때만 경고
        if not found_any_file and elapsed >= 10.0 and not no_file_warning_shown:
            elapsed_rounded = round(elapsed, 1)
            log(f"  ⚠️  다운로드가 시작되지 않은 것 같습니다. ({elapsed_rounded}초 경과)")
            log(f"     - 다운로드 폴더 확인: {TEMP_DOWNLOAD_DIR.absolute()}")
            log(f"     - 브라우저의 다운로드 설정을 확인하세요")
            no_file_warning_shown = True
    
    # 타임아웃
    log(f"  ⏱️  타임아웃 ({timeout}초)")
    
    # Chrome DevTools Protocol로 브라우저 상태 디버깅
    if driver:
        debug_browser_state(driver)
    
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
    # 먼저 로컬 파일 확인
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            progress = json.load(f)
            # 비어있지 않으면 사용
            if progress:
                return progress
    
    # 로컬 파일이 없거나 비어있으면 Google Drive에서 확인
    if DRIVE_UPLOAD_ENABLED:
        try:
            log("📂 Google Drive에서 진행 상황 확인 중...")
            uploader = get_uploader()
            if uploader.init_service():
                progress = {}
                today = date.today()
                
                for property_type in PROPERTY_TYPES:
                    prop_key = sanitize_folder_name(property_type)
                    
                    # 모든 파일의 년월 확인
                    all_months = uploader.get_all_file_months(property_type)
                    
                    if not all_months:
                        log(f"  ℹ️  {property_type}: 파일 없음 (처음 시작)")
                        continue
                    
                    # 섹션별 시작 년도/월부터 현재까지 빠진 파일 찾기
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
                        # 빠진 파일이 있으면 가장 오래된 빠진 파일부터 시작
                        oldest_missing = min(missing_months)
                        last_year, last_month = oldest_missing
                        # 가장 오래된 빠진 파일의 이전 달까지 완료된 것으로 표시
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
                        log(f"  ⚠️  {property_type}: {month_key}까지 완료, {len(missing_months)}개 파일 누락 ({oldest_missing[0]:04d}-{oldest_missing[1]:02d}부터 필요)")
                    else:
                        # 모든 파일이 있으면 가장 최근 파일
                        last_year, last_month = max(all_months)
                        month_key = f"{last_year:04d}{last_month:02d}"
                        progress[prop_key] = {
                            "last_month": month_key,
                            "last_update": datetime.now().isoformat()
                        }
                        log(f"  ✅ {property_type}: {month_key}까지 완료 (모든 파일 존재)")
                
                if progress:
                    # 로컬에도 저장
                    save_progress(progress)
                    log("💾 진행 상황을 로컬 파일에 저장했습니다.")
                    return progress
        except Exception as e:
            log(f"⚠️  Google Drive 확인 실패: {e}")
            import traceback
            traceback.print_exc()
    
    return {}

def save_progress(progress: dict):
    """진행 상황 저장"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)

def is_already_downloaded(property_type: str, year: int, month: int, update_mode: bool = False) -> bool:
    """이미 다운로드된 파일인지 확인
    
    Args:
        property_type: 부동산 종목
        year: 연도
        month: 월
        update_mode: 업데이트 모드일 때는 최근 3개월은 항상 False 반환 (재다운로드)
    """
    # 업데이트 모드일 때는 최근 3개월 범위에 있으면 항상 재다운로드
    if update_mode:
        today = date.today()
        months_to_subtract = 2  # 현재 월 포함하여 3개월
        if today.month <= months_to_subtract:
            update_start_year = today.year - 1
            update_start_month = today.month + 12 - months_to_subtract
        else:
            update_start_year = today.year
            update_start_month = today.month - months_to_subtract
        
        # 현재 파일이 최근 3개월 범위에 있는지 확인
        file_date = date(year, month, 1)
        update_start_date = date(update_start_year, update_start_month, 1)
        if file_date >= update_start_date:
            # 최근 3개월 범위에 있으면 항상 재다운로드
            return False
    
    folder_name = sanitize_folder_name(property_type)
    filename = f"{property_type} {year:04d}{month:02d}.xlsx"
    dest_path = DOWNLOAD_DIR / folder_name / filename
    
    # 로컬 확인
    if dest_path.exists():
        return True
    
    # Google Drive 확인
    if DRIVE_UPLOAD_ENABLED:
        try:
            uploader = get_uploader()
            if uploader.init_service():
                if uploader.check_file_exists(filename, property_type):
                    return True
        except:
            pass
    
    return False

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

def download_single_month_with_retry(driver, property_type: str, start_date: date, end_date: date, max_retries: int = 3, update_mode: bool = False) -> bool:
    """단일 월 다운로드 - 재시도 포함"""
    year = start_date.year
    month = start_date.month
    
    log(f"\n{'='*60}")
    log(f"📅 {property_type} {year}년 {month}월")
    log(f"{'='*60}")
    
    # 이미 다운로드됨? (업데이트 모드일 때는 최근 3개월은 스킵하지 않음)
    if is_already_downloaded(property_type, year, month, update_mode=update_mode):
        log(f"  ⏭️  이미 존재함, 스킵")
        return True
    
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
            return False
        
        # 날짜 설정 후 Alert 확인 (데이터 없음 체크)
        try:
            try_accept_alert(driver, 2.0)
        except Exception as e:
            if "NO_DATA_AVAILABLE" in str(e):
                log(f"  ⏭️  데이터 없음, 스킵")
                return True  # 데이터 없음은 정상적인 경우로 처리
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
                    # 재시도 전 페이지 새로고침
                    log(f"  🔄 페이지 새로고침 중...")
                    driver.get(MOLIT_URL)
                    time.sleep(3)
                    try_accept_alert(driver, 2.0)
                    # 탭 재선택
                    if not select_property_tab(driver, property_type):
                        log(f"  ⚠️  탭 재선택 실패")
                    log(f"  ⏳ 5초 대기 후 재시도...")
                    time.sleep(5)
                    continue
                return False
            
            # ✅ 추가: 다운로드 버튼 클릭 후 다운로드 시작 대기 (10초)
            log(f"  ⏳ 다운로드 시작 대기 중... (10초)")
            time.sleep(10.0)
            
            # 10초 대기 후 baseline_files 업데이트 (10초 동안 생성된 파일 제외)
            baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
        except Exception as e:
            if "NO_DATA_AVAILABLE" in str(e):
                log(f"  ⏭️  데이터 없음, 스킵")
                return True  # 데이터 없음은 정상적인 경우로 처리
            elif "DOWNLOAD_LIMIT_100" in str(e):
                raise  # 100건 제한은 상위로 전달
            if attempt < max_retries:
                # 재시도 전 페이지 새로고침
                log(f"  🔄 페이지 새로고침 중...")
                driver.get(MOLIT_URL)
                time.sleep(8)  # 페이지 완전 로딩 대기 (증가)
                try_accept_alert(driver, 2.0)
                remove_google_translate_popup(driver)
                
                # 페이지 URL 확인
                if "xls.do" not in driver.current_url:
                    log(f"  ⚠️  페이지가 xls.do가 아닙니다. 다시 로딩... ({driver.current_url})")
                    driver.get(MOLIT_URL)
                    time.sleep(8)
                    try_accept_alert(driver, 2.0)
                    remove_google_translate_popup(driver)
                
                # 탭 재선택
                if not select_property_tab(driver, property_type):
                    log(f"  ⚠️  탭 재선택 실패")
                log(f"  ⏳ 5초 대기 후 재시도...")
                time.sleep(5)
                continue
            return False
        
        # 다운로드 대기 (15초 - 서버 응답 지연 및 파일 생성 시간 고려)
        # 다운로드 버튼 클릭 직후이므로 즉시 감지 시작
        downloaded = wait_for_download(timeout=15, baseline_files=baseline_files, expected_year=year, expected_month=month, driver=driver)
        
        if downloaded:
            # 성공! 이동 및 이름 변경
            try:
                move_and_rename_file(downloaded, property_type, year, month)
                
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
                
                # 다운로드 성공 후 간단한 대기 (다음 다운로드를 위한 준비)
                # 페이지 재로드는 main 함수에서 처리
                time.sleep(1.0)
                
                return True
            except Exception as e:
                log(f"  ❌ 파일 이동 실패: {e}")
                if attempt < max_retries:
                    # 재시도 전 페이지 새로고침
                    log(f"  🔄 페이지 새로고침 중...")
                    driver.get(MOLIT_URL)
                    time.sleep(8)  # 페이지 완전 로딩 대기 (증가)
                    try_accept_alert(driver, 2.0)
                    remove_google_translate_popup(driver)
                    
                    # 페이지 URL 확인
                    if "xls.do" not in driver.current_url:
                        log(f"  ⚠️  페이지가 xls.do가 아닙니다. 다시 로딩... ({driver.current_url})")
                        driver.get(MOLIT_URL)
                        time.sleep(8)
                        try_accept_alert(driver, 2.0)
                        remove_google_translate_popup(driver)
                    
                    # 탭 재선택
                    if not select_property_tab(driver, property_type):
                        log(f"  ⚠️  탭 재선택 실패")
                    log(f"  ⏳ 5초 대기 후 재시도...")
                    time.sleep(5)
                    continue
                return False
        else:
            # 실패
            if attempt < max_retries:
                # 재시도 전 페이지 새로고침
                log(f"  🔄 페이지 새로고침 중...")
                driver.get(MOLIT_URL)
                time.sleep(8)  # 페이지 완전 로딩 대기 (증가)
                try_accept_alert(driver, 2.0)
                remove_google_translate_popup(driver)
                
                # 페이지 URL 확인
                if "xls.do" not in driver.current_url:
                    log(f"  ⚠️  페이지가 xls.do가 아닙니다. 다시 로딩... ({driver.current_url})")
                    driver.get(MOLIT_URL)
                    time.sleep(8)
                    try_accept_alert(driver, 2.0)
                    remove_google_translate_popup(driver)
                
                # 탭 재선택
                if not select_property_tab(driver, property_type):
                    log(f"  ⚠️  탭 재선택 실패")
                log(f"  ⏳ 5초 대기 후 재시도...")
                time.sleep(5)
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
    progress = load_progress()
    
    # 다운로드가 필요한 섹션 확인 (2006-01부터 현재까지 완료 여부)
    today = date.today()
    target_month_key = f"{today.year:04d}{today.month:02d}"
    properties_to_download = []
    
    log("📋 각 섹션별 완료 상태 확인 중...")
    for property_type in PROPERTY_TYPES:
        prop_key = sanitize_folder_name(property_type)
        last_completed = progress.get(prop_key, {}).get("last_month", "")
        
        if not last_completed:
            # 파일이 하나도 없으면 2006-01부터 다운로드 필요
            properties_to_download.append(property_type)
            log(f"  ⬇️  {property_type}: 파일 없음 → 2006-01부터 다운로드 필요")
        elif last_completed < target_month_key:
            # 2006-01부터 현재까지 완료되지 않았으면 다운로드 필요
            properties_to_download.append(property_type)
            log(f"  ⬇️  {property_type}: {last_completed}까지 완료 → {target_month_key}까지 필요 (2006-01부터)")
        else:
            # 2006-01부터 현재까지 모두 완료되었으면 스킵
            log(f"  ✅ {property_type}: {last_completed}까지 완료 → 스킵")
    
    log("")
    
    # 모드 결정
    if args.update_mode:
        # 강제 업데이트 모드이지만, 파일이 없는 섹션이 있으면 전체 다운로드
        if not properties_to_download:
            # 모든 섹션이 완료되었으면 업데이트 모드
            update_mode = True
            log("🔄 강제 업데이트 모드: 최근 3개월치만 갱신")
            properties_to_download = PROPERTY_TYPES  # 모든 섹션 처리
        else:
            # 파일이 없는 섹션이 있으면 전체 다운로드 모드
            update_mode = False
            log(f"📥 전체 다운로드 모드: {len(properties_to_download)}개 섹션 (2006-01부터)")
    elif not properties_to_download:
        # 모든 섹션이 완료되었으면 업데이트 모드로 전환
        update_mode = True
        log("✅ 모든 섹션이 2006-01부터 현재까지 완료되었습니다!")
        log("🔄 업데이트 모드로 전환: 최근 3개월치만 갱신")
        properties_to_download = PROPERTY_TYPES  # 모든 섹션을 업데이트 모드로 처리
    else:
        # 완료되지 않은 섹션이 있으면 전체 다운로드 모드
        update_mode = False
        log(f"📥 전체 다운로드 모드: {len(properties_to_download)}개 섹션 (2006-01부터)")
    
    log("")
    
    # 날짜 범위 생성
    if update_mode:
        # 최근 3개월
        months_to_subtract = 2  # 현재 월 포함하여 3개월
        if today.month <= months_to_subtract:
            start_year = today.year - 1
            start_month = today.month + 12 - months_to_subtract
        else:
            start_year = today.year
            start_month = today.month - months_to_subtract
        monthly_dates = generate_monthly_dates(start_year, start_month)
        log(f"📅 다운로드 기간: {start_year}-{start_month:02d} ~ {today.strftime('%Y-%m')} ({len(monthly_dates)}개월)")
    else:
        # 전체 기간
        monthly_dates = generate_monthly_dates(2006, 1)
        log(f"📅 다운로드 기간: 2006-01 ~ {today.strftime('%Y-%m')} ({len(monthly_dates)}개월)")
    
    # 테스트 모드
    if args.test_mode:
        monthly_dates = monthly_dates[-args.max_months:]
        log(f"🧪 테스트 모드: 최근 {len(monthly_dates)}개월만")
    
    log("")
    
    driver = build_driver()
    
    try:
        # 페이지 로드
        log("🌐 사이트 접속 중...")
        driver.get(MOLIT_URL)
        time.sleep(5)  # 로딩 대기 증가
        try_accept_alert(driver, 2.0)
        
        # Google Translate 팝업 제거
        remove_google_translate_popup(driver)
        
        log(f"✅ 접속 완료: {driver.current_url}\n")
        
        # 페이지 상태 확인
        log(f"📄 페이지 제목: {driver.title}")
        log("")
        
        # 전체 통계
        total_success = 0
        total_fail = 0
        
        # 다운로드가 필요한 섹션만 처리
        for prop_idx, property_type in enumerate(properties_to_download, 1):
            log("="*70)
            log(f"📊 [{prop_idx}/{len(properties_to_download)}] {property_type}")
            log("="*70)
            
            # 탭 선택
            if not select_property_tab(driver, property_type):
                log(f"⚠️  탭 선택 실패, 다음 종목으로...")
                continue
            
            # 진행 상황 확인
            prop_key = sanitize_folder_name(property_type)
            last_completed = progress.get(prop_key, {}).get("last_month", "")
            
            # 이 섹션에 대한 월별 날짜 범위 생성
            if update_mode:
                # 업데이트 모드: 최근 3개월만 갱신 (last_completed와 무관하게)
                today = date.today()
                # 최근 3개월 계산
                months_to_subtract = 2  # 현재 월 포함하여 3개월
                if today.month <= months_to_subtract:
                    start_year = today.year - 1
                    start_month = today.month + 12 - months_to_subtract
                else:
                    start_year = today.year
                    start_month = today.month - months_to_subtract
                section_monthly_dates = generate_monthly_dates(start_year, start_month)
            else:
                # 전체 다운로드 모드: 2006-01부터
                if last_completed:
                    # last_completed 다음 달부터 시작
                    last_year = int(last_completed[:4])
                    last_month = int(last_completed[4:6])
                    if last_month == 12:
                        start_year = last_year + 1
                        start_month = 1
                    else:
                        start_year = last_year
                        start_month = last_month + 1
                else:
                    # 파일이 없으면 섹션별 시작 년도/월부터
                    section_start_year = SECTION_START_YEAR.get(property_type, 2006)
                    section_start_month = SECTION_START_MONTH.get(property_type, 1)
                    start_year = section_start_year
                    start_month = section_start_month
                section_monthly_dates = generate_monthly_dates(start_year, start_month)
            
            if last_completed:
                log(f"📌 마지막 완료: {last_completed}")
                log(f"🔄 이어서 진행합니다... ({start_year:04d}-{start_month:02d}부터)")
            else:
                log(f"🆕 처음 시작합니다 ({start_year:04d}-{start_month:02d}부터)")
            
            log(f"📅 다운로드 예정: {len(section_monthly_dates)}개월")
            
            # 각 월별로
            success_count = 0
            fail_count = 0
            consecutive_fails = 0
            skipped_count = 0
            
            for month_idx, (start_date, end_date) in enumerate(section_monthly_dates, 1):
                year = start_date.year
                month = start_date.month
                month_key = f"{year:04d}{month:02d}"
                
                log(f"\n[{month_idx}/{len(section_monthly_dates)}]", end=" ")
                
                # 두 번째 다운로드부터는 페이지를 재로드하고 탭을 다시 선택 (안정성 향상)
                if month_idx > 1:
                    retry_count = 0
                    tab_selected = False
                    while retry_count < 3 and not tab_selected:
                        try:
                            log(f"  🔄 페이지 재로딩 및 탭 재선택... (시도 {retry_count + 1}/3)")
                            driver.get(MOLIT_URL)
                            time.sleep(8)  # 페이지 완전 로딩 대기 (증가)
                            try_accept_alert(driver, 2.0)
                            remove_google_translate_popup(driver)
                            
                            # 페이지 URL 확인
                            if "xls.do" not in driver.current_url:
                                log(f"  ⚠️  페이지가 xls.do가 아닙니다. 다시 로딩... ({driver.current_url})")
                                driver.get(MOLIT_URL)
                                time.sleep(8)
                                try_accept_alert(driver, 2.0)
                                remove_google_translate_popup(driver)
                            
                            # 탭 요소가 나타날 때까지 명시적으로 대기
                            log(f"  ⏳ 탭 요소 로딩 대기 중...")
                            tab_loaded = False
                            for wait_attempt in range(15):  # 최대 15초 대기
                                try:
                                    tab_container = driver.find_elements(By.CSS_SELECTOR, "ul.quarter-tab-cover")
                                    if tab_container:
                                        tab_links = driver.find_elements(By.CSS_SELECTOR, "ul.quarter-tab-cover a")
                                        if tab_links:
                                            tab_loaded = True
                                            log(f"  ✅ 탭 요소 로딩 완료 ({wait_attempt + 1}번째 시도)")
                                            break
                                except:
                                    pass
                                time.sleep(1.0)
                            
                            if not tab_loaded:
                                log(f"  ⚠️  탭 요소를 찾을 수 없지만 계속 진행...")
                                time.sleep(3.0)
                            
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
                
                # 파일 존재 확인 (업데이트 모드일 때는 최근 3개월은 스킵하지 않음)
                if is_already_downloaded(property_type, year, month, update_mode=update_mode):
                    log(f"  ⏭️  이미 존재함, 스킵")
                    skipped_count += 1
                    continue
                
                # 다운로드 시도 (최대 3회 재시도)
                success = download_single_month_with_retry(driver, property_type, start_date, end_date, max_retries=3, update_mode=update_mode)
                
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
                
                # 다음 요청 전 대기 (서버 부하 방지 및 요청 간격 확보)
                time.sleep(5)
            
            log(f"\n✅ {property_type} 완료")
            log(f"   성공: {success_count}, 실패: {fail_count}, 스킵: {skipped_count}")
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