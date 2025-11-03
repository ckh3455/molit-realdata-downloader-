# -*- coding: utf-8 -*-
"""
탭 선택 테스트 스크립트 (GitHub Actions 대응 + 상세 디버깅)
- 각 부동산 종목 탭을 순서대로 클릭
- 스크린샷, 페이지 소스, 상세 로그 저장
"""
import os
import time
import sys
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

from config import MOLIT_URL, PROPERTY_TYPES

# 출력 디렉토리
SCREENSHOT_DIR = Path("screenshots")
PAGE_SOURCE_DIR = Path("page_sources")
SCREENSHOT_DIR.mkdir(exist_ok=True)
PAGE_SOURCE_DIR.mkdir(exist_ok=True)

IS_CI = os.getenv("CI", "") == "1"


def log(msg: str, level="INFO"):
    """타임스탬프 포함 로그 출력"""
    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    prefix = {
        "INFO": "ℹ️ ",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "WARNING": "⚠️ ",
        "DEBUG": "🔍"
    }.get(level, "  ")
    print(f"[{timestamp}] {prefix} {msg}", flush=True)


def build_driver():
    """크롬 드라이버 생성"""
    log("크롬 드라이버 생성 중...", "DEBUG")
    
    opts = Options()
    if IS_CI:
        opts.add_argument("--headless=new")
        log("  - Headless 모드 활성화", "DEBUG")
    
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    
    # CI 환경: 환경변수로 지정된 chromedriver 사용
    chromedriver_bin = os.getenv("CHROMEDRIVER_BIN")
    if chromedriver_bin and Path(chromedriver_bin).exists():
        log(f"  - Chromedriver: {chromedriver_bin}", "DEBUG")
        service = Service(chromedriver_bin)
    else:
        log("  - Chromedriver: webdriver-manager로 다운로드", "DEBUG")
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        opts.binary_location = chrome_bin
        log(f"  - Chrome binary: {chrome_bin}", "DEBUG")
    
    driver = webdriver.Chrome(service=service, options=opts)
    log("✅ 드라이버 생성 완료", "SUCCESS")
    return driver


def save_screenshot(driver, name: str):
    """스크린샷 저장"""
    filepath = SCREENSHOT_DIR / f"{name}.png"
    try:
        driver.save_screenshot(str(filepath))
        log(f"  📸 스크린샷 저장: {filepath}", "DEBUG")
    except Exception as e:
        log(f"  스크린샷 저장 실패: {e}", "ERROR")


def save_page_source(driver, name: str):
    """페이지 소스 저장"""
    filepath = PAGE_SOURCE_DIR / f"{name}.html"
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log(f"  📄 페이지 소스 저장: {filepath}", "DEBUG")
    except Exception as e:
        log(f"  페이지 소스 저장 실패: {e}", "ERROR")


def find_and_click_tab(driver, tab_name: str, index: int) -> bool:
    """
    탭 메뉴에서 특정 종목 클릭
    """
    log(f"탭 클릭 시도: {tab_name}", "INFO")
    
    # 현재 상태 저장
    save_screenshot(driver, f"{index:02d}_before_{tab_name}")
    save_page_source(driver, f"{index:02d}_before_{tab_name}")
    
    # 여러 방법으로 탭 찾기
    locators = [
        (By.XPATH, f"//a[contains(text(), '{tab_name}')]"),
        (By.XPATH, f"//a[normalize-space()='{tab_name}']"),
        (By.XPATH, f"//button[contains(text(), '{tab_name}')]"),
        (By.XPATH, f"//li//a[contains(text(), '{tab_name}')]"),
        (By.LINK_TEXT, tab_name),
        (By.PARTIAL_LINK_TEXT, tab_name),
    ]
    
    for method_idx, (by, selector) in enumerate(locators, 1):
        log(f"  방법 {method_idx}: {by} = {selector}", "DEBUG")
        try:
            elements = driver.find_elements(by, selector)
            log(f"    발견된 요소 수: {len(elements)}", "DEBUG")
            
            for elem_idx, elem in enumerate(elements, 1):
                try:
                    is_displayed = elem.is_displayed()
                    is_enabled = elem.is_enabled()
                    tag = elem.tag_name
                    text = elem.text
                    classes = elem.get_attribute("class") or ""
                    
                    log(f"    요소 #{elem_idx}: tag={tag}, text='{text}', "
                        f"displayed={is_displayed}, enabled={is_enabled}, "
                        f"class='{classes}'", "DEBUG")
                    
                    if is_displayed:
                        # 스크롤
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", 
                            elem
                        )
                        time.sleep(0.3)
                        
                        # 클릭
                        elem.click()
                        log(f"  ✅ 클릭 성공! (방법 {method_idx}, 요소 #{elem_idx})", "SUCCESS")
                        time.sleep(1.5)
                        
                        # 클릭 후 상태 저장
                        save_screenshot(driver, f"{index:02d}_after_{tab_name}")
                        save_page_source(driver, f"{index:02d}_after_{tab_name}")
                        
                        return True
                        
                except Exception as e:
                    log(f"    요소 #{elem_idx} 처리 실패: {e}", "WARNING")
                    continue
                    
        except Exception as e:
            log(f"  방법 {method_idx} 실패: {e}", "WARNING")
            continue
    
    log(f"  ❌ 클릭 실패: 모든 방법 시도했으나 실패", "ERROR")
    return False


def get_current_tab_info(driver) -> dict:
    """현재 페이지 상태 정보"""
    info = {
        "url": driver.current_url,
        "title": driver.title,
        "active_tab": "(확인 불가)"
    }
    
    try:
        # 활성화된 탭 찾기
        active_selectors = [
            "//li[contains(@class, 'active')]//a",
            "//a[contains(@class, 'active')]",
            "//li[contains(@class, 'on')]//a",
            "//a[contains(@class, 'on')]",
        ]
        
        for sel in active_selectors:
            try:
                elem = driver.find_element(By.XPATH, sel)
                info["active_tab"] = elem.text
                break
            except:
                continue
                
    except Exception as e:
        log(f"  활성 탭 확인 실패: {e}", "WARNING")
    
    return info


def test_all_tabs():
    """모든 탭 순서대로 클릭 테스트"""
    log("="*70, "INFO")
    log("🔍 국토부 실거래가 사이트 탭 선택 테스트 시작", "INFO")
    log("="*70, "INFO")
    
    driver = build_driver()
    
    try:
        # 페이지 로드
        log(f"📍 접속: {MOLIT_URL}", "INFO")
        driver.get(MOLIT_URL)
        time.sleep(3)
        
        info = get_current_tab_info(driver)
        log(f"📋 URL: {info['url']}", "INFO")
        log(f"📋 제목: {info['title']}", "INFO")
        log(f"📌 현재 활성 탭: {info['active_tab']}", "INFO")
        
        # 초기 상태 저장
        save_screenshot(driver, "00_initial")
        save_page_source(driver, "00_initial")
        
        # 각 탭 클릭 시도
        results = {}
        for idx, prop_type in enumerate(PROPERTY_TYPES, 1):
            log("─"*70, "INFO")
            log(f"[{idx}/{len(PROPERTY_TYPES)}] {prop_type}", "INFO")
            log("─"*70, "INFO")
            
            success = find_and_click_tab(driver, prop_type, idx)
            results[prop_type] = success
            
            if success:
                info = get_current_tab_info(driver)
                log(f"  📌 현재 활성 탭: {info['active_tab']}", "INFO")
                log(f"  📌 현재 URL: {info['url']}", "INFO")
            
            time.sleep(2)
        
        # 결과 요약
        log("="*70, "INFO")
        log("📊 테스트 결과 요약", "INFO")
        log("="*70, "INFO")
        
        for prop_type, success in results.items():
            level = "SUCCESS" if success else "ERROR"
            status = "성공" if success else "실패"
            log(f"{status:4s} | {prop_type}", level)
        
        success_count = sum(results.values())
        total_count = len(PROPERTY_TYPES)
        log("="*70, "INFO")
        log(f"총 {total_count}개 중 {success_count}개 성공", "INFO")
        
        # 최종 상태 저장
        save_screenshot(driver, "99_final")
        save_page_source(driver, "99_final")
        
        # 종료 코드
        if success_count == total_count:
            log("✅ 모든 탭 클릭 성공!", "SUCCESS")
            return 0
        else:
            log(f"⚠️  {total_count - success_count}개 탭 클릭 실패", "WARNING")
            return 1
        
    except Exception as e:
        log(f"❌ 치명적 오류 발생: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        
        # 오류 시 상태 저장
        try:
            save_screenshot(driver, "error")
            save_page_source(driver, "error")
        except:
            pass
        
        return 2
    
    finally:
        try:
            driver.quit()
            log("✅ 드라이버 종료", "SUCCESS")
        except:
            pass


if __name__ == "__main__":
    exit_code = test_all_tabs()
    sys.exit(exit_code)
