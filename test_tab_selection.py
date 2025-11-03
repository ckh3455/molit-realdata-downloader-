# -*- coding: utf-8 -*-
"""
탭 선택 테스트 스크립트
- 각 부동산 종목 탭을 순서대로 클릭
- 현재 활성화된 탭 확인
"""
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from config import MOLIT_URL, PROPERTY_TYPES


def build_driver():
    """크롬 드라이버 생성 (로컬용 - headless 없음)"""
    opts = Options()
    # opts.add_argument("--headless=new")  # 테스트시 주석처리
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    return driver


def find_and_click_tab(driver, tab_name: str) -> bool:
    """
    탭 메뉴에서 특정 종목 클릭
    
    Returns:
        bool: 성공 여부
    """
    print(f"\n[시도] 탭 클릭: {tab_name}")
    
    # 여러 방법으로 탭 찾기
    locators = [
        (By.XPATH, f"//a[contains(text(), '{tab_name}')]"),
        (By.XPATH, f"//button[contains(text(), '{tab_name}')]"),
        (By.LINK_TEXT, tab_name),
        (By.PARTIAL_LINK_TEXT, tab_name),
    ]
    
    for by, selector in locators:
        try:
            elements = driver.find_elements(by, selector)
            for elem in elements:
                if elem.is_displayed():
                    # 스크롤해서 보이게
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", 
                        elem
                    )
                    time.sleep(0.3)
                    
                    # 클릭 전 정보
                    print(f"  - 발견: tag={elem.tag_name}, text={elem.text}")
                    
                    elem.click()
                    time.sleep(1.5)
                    
                    print(f"  ✓ 클릭 성공!")
                    return True
                    
        except Exception as e:
            continue
    
    print(f"  ✗ 클릭 실패: 요소를 찾을 수 없음")
    return False


def get_current_tab_name(driver) -> str:
    """현재 활성화된 탭 이름 확인"""
    try:
        # 활성화된 탭은 보통 class에 'active' 또는 'on' 등이 포함됨
        active_tabs = driver.find_elements(
            By.XPATH, 
            "//li[contains(@class, 'active')]//a | //a[contains(@class, 'active')]"
        )
        if active_tabs:
            return active_tabs[0].text
    except:
        pass
    return "(확인 불가)"


def test_all_tabs():
    """모든 탭 순서대로 클릭 테스트"""
    driver = build_driver()
    
    try:
        print("="*60)
        print("🔍 국토부 실거래가 사이트 탭 선택 테스트")
        print("="*60)
        
        # 페이지 로드
        print(f"\n📍 접속: {MOLIT_URL}")
        driver.get(MOLIT_URL)
        time.sleep(2)
        
        print(f"\n📋 현재 URL: {driver.current_url}")
        print(f"📋 페이지 제목: {driver.title}")
        
        # 각 탭 클릭 시도
        results = {}
        for idx, prop_type in enumerate(PROPERTY_TYPES, 1):
            print(f"\n{'─'*60}")
            print(f"[{idx}/{len(PROPERTY_TYPES)}] {prop_type}")
            print(f"{'─'*60}")
            
            success = find_and_click_tab(driver, prop_type)
            results[prop_type] = success
            
            if success:
                current = get_current_tab_name(driver)
                print(f"  📌 현재 활성 탭: {current}")
                
                # 페이지 스크린샷 (선택사항)
                # screenshot_path = f"screenshot_{prop_type}.png"
                # driver.save_screenshot(screenshot_path)
                # print(f"  📸 스크린샷 저장: {screenshot_path}")
            
            time.sleep(2)  # 다음 테스트 전 대기
        
        # 결과 요약
        print("\n" + "="*60)
        print("📊 테스트 결과 요약")
        print("="*60)
        for prop_type, success in results.items():
            status = "✓ 성공" if success else "✗ 실패"
            print(f"{status:8s} | {prop_type}")
        
        success_count = sum(results.values())
        print(f"\n총 {len(PROPERTY_TYPES)}개 중 {success_count}개 성공")
        
        # 마지막에 브라우저 닫지 않고 대기 (수동 확인용)
        print("\n⏸️  브라우저를 수동으로 확인하세요. (종료하려면 Enter)")
        input()
        
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        driver.quit()
        print("\n✅ 테스트 완료")


if __name__ == "__main__":
    test_all_tabs()
