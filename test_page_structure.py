# -*- coding: utf-8 -*-
"""
페이지 구조 탐색: 실제로 어떤 탭/링크가 있는지 확인
"""
import os
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

from config import MOLIT_URL

IS_CI = os.getenv("CI", "") == "1"


def build_driver():
    opts = Options()
    if IS_CI:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=ko-KR")
    
    chromedriver_bin = os.getenv("CHROMEDRIVER_BIN")
    if chromedriver_bin and Path(chromedriver_bin).exists():
        service = Service(chromedriver_bin)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    
    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin:
        opts.binary_location = chrome_bin
    
    return webdriver.Chrome(service=service, options=opts)


def explore_page_structure():
    """페이지의 모든 링크와 버튼 탐색"""
    driver = build_driver()
    
    try:
        print("="*70)
        print("🔍 페이지 구조 탐색")
        print("="*70)
        
        driver.get(MOLIT_URL)
        time.sleep(3)
        
        print(f"\n📍 현재 URL: {driver.current_url}")
        print(f"📋 페이지 제목: {driver.title}\n")
        
        # 1. 모든 링크 찾기
        print("\n" + "="*70)
        print("📎 모든 <a> 링크:")
        print("="*70)
        links = driver.find_elements(By.TAG_NAME, "a")
        for i, link in enumerate(links[:50], 1):  # 최대 50개
            text = link.text.strip()
            href = link.get_attribute("href") or ""
            classes = link.get_attribute("class") or ""
            displayed = link.is_displayed()
            
            if text or "link" in classes.lower():
                print(f"{i:3d}. text='{text:30s}' | href={href[:60]:60s} | class={classes[:30]:30s} | visible={displayed}")
        
        # 2. 모든 버튼 찾기
        print("\n" + "="*70)
        print("🔘 모든 <button> 버튼:")
        print("="*70)
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for i, btn in enumerate(buttons, 1):
            text = btn.text.strip()
            btn_type = btn.get_attribute("type") or ""
            classes = btn.get_attribute("class") or ""
            displayed = btn.is_displayed()
            print(f"{i:3d}. text='{text:30s}' | type={btn_type:10s} | class={classes[:30]:30s} | visible={displayed}")
        
        # 3. 특정 class 가진 요소들
        print("\n" + "="*70)
        print("🎯 'link' 클래스 포함 요소:")
        print("="*70)
        link_class_elems = driver.find_elements(By.XPATH, "//*[contains(@class, 'link')]")
        for i, elem in enumerate(link_class_elems, 1):
            tag = elem.tag_name
            text = elem.text.strip()
            classes = elem.get_attribute("class") or ""
            displayed = elem.is_displayed()
            print(f"{i:3d}. <{tag}> text='{text:30s}' | class={classes[:40]:40s} | visible={displayed}")
        
        # 4. 탭/메뉴로 보이는 구조
        print("\n" + "="*70)
        print("📑 탭/메뉴 구조 (ul > li > a):")
        print("="*70)
        uls = driver.find_elements(By.TAG_NAME, "ul")
        for ul_idx, ul in enumerate(uls[:10], 1):  # 최대 10개
            ul_classes = ul.get_attribute("class") or ""
            lis = ul.find_elements(By.TAG_NAME, "li")
            if lis:
                print(f"\n[UL #{ul_idx}] class='{ul_classes}' | {len(lis)}개 항목:")
                for li_idx, li in enumerate(lis[:20], 1):  # 최대 20개
                    try:
                        a = li.find_element(By.TAG_NAME, "a")
                        text = a.text.strip()
                        href = a.get_attribute("href") or ""
                        displayed = a.is_displayed()
                        print(f"  {li_idx:2d}. '{text:25s}' | {href[:50]:50s} | visible={displayed}")
                    except:
                        text = li.text.strip()
                        if text:
                            print(f"  {li_idx:2d}. (no link) '{text}'")
        
        # 5. 스크린샷 저장
        Path("screenshots").mkdir(exist_ok=True)
        driver.save_screenshot("screenshots/page_structure.png")
        print("\n📸 스크린샷 저장: screenshots/page_structure.png")
        
        # 6. HTML 저장
        Path("page_sources").mkdir(exist_ok=True)
        with open("page_sources/page_structure.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("📄 HTML 저장: page_sources/page_structure.html")
        
        print("\n✅ 탐색 완료!")
        
    finally:
        driver.quit()


if __name__ == "__main__":
    explore_page_structure()
