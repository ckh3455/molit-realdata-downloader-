# -*- coding: utf-8 -*-
"""
국토부 실거래가 데이터 월별 대량 다운로드
- 재시도 로직 (15초 대기, 최대 3회)
- 진행 상황 저장 및 재개
- 100회 제한 대응 (다음날 자동 재개)
- 업데이트 모드 (최근 1년만 갱신)
- 탭 선택 로직 개선

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
from selenium.common.exceptions import UnexpectedAlertPresentException, TimeoutException, StaleElementReferenceException

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
        "profile.default_content_setting_values.notifications": 2,  # 알림 차단
        "profile.content_settings.exceptions.automatic_downloads.*.setting": 1,  # 자동 다운로드 허용 (알림 없이)
    }
    opts.add_experimental_option("prefs", prefs)
    
    # 자동 다운로드 알림 비활성화
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-infobars")
    
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

def select_property_tab(driver, tab_name: str, max_retries: int = 3) -> bool:
    """부동산 종목 탭 선택 - 개선 버전
    
    Args:
        driver: Selenium WebDriver
        tab_name: 탭 이름 (예: "아파트", "연립다세대")
        max_retries: 최대 재시도 횟수
    
    Returns:
        bool: 성공 여부
    """
    actual_tab_name = TAB_NAME_MAPPING.get(tab_name, tab_name)
    tab_id = TAB_ID_MAPPING.get(tab_name)
    
    log(f"  🎯 탭 선택 시도: {tab_name} (페이지: {actual_tab_name}, ID: {tab_id})")
    
    for attempt in range(1, max_retries + 1):
        try:
            log(f"  🔄 시도 {attempt}/{max_retries}")
            
            # 1. 페이지 확인 및 로딩
            if "xls.do" not in driver.current_url:
                log(f"  📄 페이지 이동: {MOLIT_URL}")
                driver.get(MOLIT_URL)
                
            # 2. 페이지 완전 로딩 대기
            try:
                WebDriverWait(driver, 15).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
                log(f"  ✅ 페이지 로딩 완료")
            except TimeoutException:
                log(f"  ⚠️  페이지 로딩 타임아웃")
            
            time.sleep(2)
            
            # 3. Alert 처리
            try_accept_alert(driver, 2.0)
            
            # 4. Google Translate 팝업 제거
            remove_google_translate_popup(driver)
            
            # 5. 탭 컨테이너 로딩 대기
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "ul.quarter-tab-cover"))
                )
                log(f"  ✅ 탭 컨테이너 로딩 완료")
            except TimeoutException:
                log(f"  ⚠️  탭 컨테이너 타임아웃")
                if attempt < max_retries:
                    time.sleep(3)
                    continue
                return False
            
            # 6. 탭 요소 찾기 및 클릭
            tab_clicked = False
            
            # 방법 1: ID로 직접 찾기 (가장 확실)
            if tab_id:
                try:
                    log(f"  🔍 방법 1: ID로 탭 찾기 ({tab_id})")
                    elem = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.ID, tab_id))
                    )
                    
                    # JavaScript로 클릭 (더 안정적)
                    driver.execute_script("""
                        arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});
                        arguments[0].click();
                    """, elem)
                    
                    log(f"  ✅ 탭 클릭 완료 (ID)")
                    tab_clicked = True
                    
                except (TimeoutException, StaleElementReferenceException) as e:
                    log(f"  ⚠️  ID로 찾기 실패: {type(e).__name__}")
            
            # 방법 2: JavaScript로 직접 찾아서 클릭
            if not tab_clicked:
                try:
                    log(f"  🔍 방법 2: JavaScript로 탭 찾기")
                    clicked = driver.execute_script(f"""
                        // ID로 찾기
                        var elem = document.getElementById('{tab_id}');
                        if (elem && elem.offsetParent !== null) {{
                            elem.scrollIntoView({{block: 'center', behavior: 'instant'}});
                            elem.click();
                            return true;
                        }}
                        
                        // 텍스트로 찾기
                        var links = document.querySelectorAll('ul.quarter-tab-cover a');
                        var targetText = '{actual_tab_name}';
                        for (var i = 0; i < links.length; i++) {{
                            var link = links[i];
                            var text = link.textContent.trim();
                            if (text === targetText && link.offsetParent !== null) {{
                                link.scrollIntoView({{block: 'center', behavior: 'instant'}});
                                link.click();
                                return true;
                            }}
                        }}
                        return false;
                    """)
                    
                    if clicked:
                        log(f"  ✅ 탭 클릭 완료 (JavaScript)")
                        tab_clicked = True
                    else:
                        log(f"  ⚠️  JavaScript로 탭을 찾을 수 없음")
                        
                except Exception as e:
                    log(f"  ⚠️  JavaScript 실행 실패: {e}")
            
            if not tab_clicked:
                if attempt < max_retries:
                    log(f"  ⏳ 3초 대기 후 재시도...")
                    time.sleep(3)
                    continue
                log(f"  ❌ 모든 방법 실패")
                return False
            
            # 7. 클릭 후 처리
            time.sleep(2)  # 탭 전환 대기
            try_accept_alert(driver, 2.0)
            remove_google_translate_popup(driver)
            
            # 8. 활성화 확인
            try:
                is_active = driver.execute_script(f"""
                    var elem = document.getElementById('{tab_id}');
                    if (elem) {{
                        var parent = elem.parentElement;
                        return parent && parent.className.includes('on');
                    }}
                    return false;
                """)
                
                if is_active:
                    log(f"  ✅ 탭 활성화 확인됨")
                else:
                    log(f"  ⚠️  탭이 활성화되지 않음, 한 번 더 클릭 시도")
                    # 한 번 더 클릭
                    driver.execute_script(f"""
                        var elem = document.getElementById('{tab_id}');
                        if (elem) {{
                            elem.click();
                        }}
                    """)
                    time.sleep(2)
                    try_accept_alert(driver, 2.0)
                    
            except Exception as e:
                log(f"  ⚠️  활성화 확인 실패: {e}")
            
            # 9. 날짜 입력 필드 대기 (페이지 준비 확인)
            try:
                date_field = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "#srchBgnDe"))
                )
                # 필드가 활성화되고 값을 설정할 수 있는지 확인
                driver.execute_script("arguments[0].value = '';", date_field)
                log(f"  ✅ 페이지 준비 완료 (날짜 필드 확인)")
                
                # 추가 안정화 대기
                time.sleep(1)
                
                return True
                
            except TimeoutException:
                log(f"  ⚠️  날짜 입력 필드 타임아웃")
                if attempt < max_retries:
                    time.sleep(3)
                    continue
                return False
                
        except Exception as e:
            log(f"  ❌ 예외 발생: {type(e).__name__} - {e}")
            if attempt < max_retries:
                log(f"  ⏳ 3초 대기 후 재시도...")
                time.sleep(3)
                continue
            return False
    
    log(f"  ❌ {max_retries}회 시도 모두 실패")
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
        
        # 방법 3: WebDriverWait를 사용한 명시적 대기
        try:
            log(f"  🔍 방법 3: WebDriverWait로 버튼 찾기")
            button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'EXCEL 다운')]"))
            )
            
            # JavaScript로 클릭
            driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'instant'}); arguments[0].click();", button)
            log(f"  ✅ 버튼 클릭 완료 (WebDriverWait)")
            
            # Alert 확인
            try_accept_alert(driver, 2.0)
            return True
            
        except TimeoutException:
            log(f"  ⚠️  버튼을 찾을 수 없음 (타임아웃)")
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e) or "NO_DATA_AVAILABLE" in str(e):
                raise
            log(f"  ⚠️  버튼 클릭 실패: {e}")
        
        log(f"  ❌ EXCEL 다운 버튼을 찾을 수 없습니다")
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
                    # baseline_files 이후에 생성된 새 파일이면 우리가 요청한 파일로 간주
                    # 생성 시간 체크 불필요 - baseline_files 기준으로 새 파일만 확인하면 됨
                    log(f"  ✅ 다운로드 완료: {latest.name} ({size:,} bytes)")
                    return latest
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

# ... (나머지 함수들은 동일하므로 생략)
# preprocess_file, move_and_rename_file, generate_monthly_dates, load_progress, save_progress,
# is_already_downloaded, check_if_all_historical_complete, download_single_month_with_retry, main
