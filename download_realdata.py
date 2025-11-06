# -*- coding: utf-8 -*-
"""
국토부 실거래가 다운로드 문제 해결 패치
주요 개선 사항:
1. Alert 처리 강화 및 로깅
2. 스크린샷 디버깅
3. 브라우저 콘솔 로그 확인
4. 다운로드 대기 시간 증가
5. Chrome 다운로드 설정 개선
"""

# ==================== 개선 1: Chrome 드라이버 빌드 함수 ====================
def build_driver_improved():
    """크롬 드라이버 생성 - 개선 버전"""
    opts = Options()
    
    # Headless 설정 (CI 환경)
    if IS_CI:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")  # 크기 증가
    else:
        opts.add_argument("--start-maximized")
    
    # 기본 옵션
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=ko-KR")
    
    # 다운로드 안정성 개선
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    opts.add_experimental_option('useAutomationExtension', False)
    
    # User-Agent 설정 (봇 감지 우회)
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # 다운로드 폴더 절대 경로로 설정
    download_dir = str(TEMP_DOWNLOAD_DIR.absolute())
    
    # 다운로드 설정 강화
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": False,  # False로 변경 (다운로드 차단 방지)
        "safebrowsing.disable_download_protection": True,  # 추가
        "profile.default_content_settings.popups": 0,  # 팝업 차단 해제
        "profile.default_content_setting_values.automatic_downloads": 1,  # 자동 다운로드 허용
    }
    opts.add_experimental_option("prefs", prefs)
    
    # 로깅 활성화 (디버깅용)
    opts.add_argument("--enable-logging")
    opts.add_argument("--v=1")
    
    # ChromeDriver 생성
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
    
    # 다운로드 디렉토리 권한 확인
    log(f"  📁 다운로드 폴더 권한 확인: {download_dir}")
    try:
        # 테스트 파일 생성
        test_file = TEMP_DOWNLOAD_DIR / "test_write.txt"
        test_file.write_text("test")
        test_file.unlink()
        log(f"  ✅ 다운로드 폴더 쓰기 가능")
    except Exception as e:
        log(f"  ⚠️  다운로드 폴더 쓰기 실패: {e}")
    
    return driver


# ==================== 개선 2: Alert 처리 강화 ====================
def try_accept_alert_improved(driver, timeout=3.0) -> bool:
    """Alert 자동 수락 - 개선 버전"""
    end_time = time.time() + timeout
    alert_found = False
    
    while time.time() < end_time:
        try:
            alert = Alert(driver)
            text = alert.text
            alert_found = True
            
            log(f"  🔔 Alert 발견: '{text}'")
            
            # 100건 제한 감지
            if "100건" in text or "100" in text:
                alert.accept()
                log(f"  ⛔ 일일 다운로드 100건 제한!")
                raise Exception("DOWNLOAD_LIMIT_100")
            
            # 데이터 없음 감지
            if "데이터가 존재하지 않습니다" in text or "존재하지 않습니다" in text:
                alert.accept()
                log(f"  ℹ️  데이터 없음")
                raise Exception("NO_DATA_AVAILABLE")
            
            # 기타 Alert
            log(f"  ✅ Alert 수락")
            alert.accept()
            time.sleep(0.5)
            return True
            
        except Exception as e:
            error_str = str(e)
            if "DOWNLOAD_LIMIT_100" in error_str:
                raise
            if "NO_DATA_AVAILABLE" in error_str:
                raise
            if "no such alert" not in error_str.lower() and "no alert" not in error_str.lower():
                # Alert가 아닌 다른 오류
                if alert_found:
                    log(f"  ⚠️  Alert 처리 중 오류: {e}")
            time.sleep(0.2)
    
    if not alert_found:
        log(f"  ℹ️  Alert 없음 (정상)")
    
    return alert_found


# ==================== 개선 3: EXCEL 다운로드 버튼 클릭 ====================
def click_excel_download_improved(driver) -> bool:
    """EXCEL 다운 버튼 클릭 - 개선 버전"""
    try:
        # Google Translate 팝업 제거
        remove_google_translate_popup(driver)
        time.sleep(1.0)
        
        # 디버깅: 스크린샷 (클릭 전)
        try:
            screenshot_path = TEMP_DOWNLOAD_DIR / f"before_click_{datetime.now().strftime('%H%M%S')}.png"
            driver.save_screenshot(str(screenshot_path))
            log(f"  📸 클릭 전 스크린샷: {screenshot_path.name}")
        except Exception as e:
            log(f"  ⚠️  스크린샷 실패: {e}")
        
        # 브라우저 콘솔 로그 확인
        try:
            logs = driver.get_log('browser')
            if logs:
                log(f"  📋 브라우저 콘솔 로그:")
                for entry in logs[-5:]:  # 최근 5개만
                    log(f"     {entry['level']}: {entry['message'][:100]}")
        except:
            pass
        
        # 방법 1: JavaScript 함수 직접 호출
        log(f"  🔍 fnExcelDown() 함수 실행 시도...")
        result = driver.execute_script("""
            console.log('[DEBUG] fnExcelDown 함수 확인 중...');
            
            // 함수 존재 확인
            if (typeof fnExcelDown !== 'function') {
                console.error('[DEBUG] fnExcelDown 함수 없음!');
                return {success: false, error: 'Function not found'};
            }
            
            // 함수 실행
            try {
                console.log('[DEBUG] fnExcelDown() 실행...');
                fnExcelDown();
                console.log('[DEBUG] fnExcelDown() 실행 완료');
                return {success: true};
            } catch(e) {
                console.error('[DEBUG] fnExcelDown() 실행 오류:', e);
                return {success: false, error: e.toString()};
            }
        """)
        
        log(f"  📊 JavaScript 실행 결과: {result}")
        
        if not result or not result.get('success'):
            error = result.get('error', 'Unknown') if result else 'No result'
            log(f"  ❌ fnExcelDown() 실행 실패: {error}")
            
            # 방법 2: 버튼 직접 클릭 시도
            log(f"  🔍 버튼 직접 클릭 시도...")
            selectors = [
                "//button[contains(@onclick, 'fnExcelDown')]",
                "//button[normalize-space(text())='EXCEL 다운']",
                "//button[contains(text(), 'EXCEL')]",
                "button[onclick*='fnExcelDown']",
                "button.btn-excel",
            ]
            
            btn_found = False
            for selector in selectors:
                try:
                    if selector.startswith("//"):
                        btn = driver.find_element(By.XPATH, selector)
                    else:
                        btn = driver.find_element(By.CSS_SELECTOR, selector)
                    
                    if btn and btn.is_displayed():
                        log(f"  ✅ 버튼 발견: {selector}")
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        time.sleep(0.5)
                        driver.execute_script("arguments[0].click();", btn)
                        btn_found = True
                        break
                except:
                    continue
            
            if not btn_found:
                log(f"  ❌ 다운로드 버튼을 찾을 수 없음")
                return False
        else:
            log(f"  ✅ fnExcelDown() 실행 성공")
        
        # 서버 응답 대기
        log(f"  ⏳ 서버 응답 대기 (5초)...")
        time.sleep(5.0)
        
        # Alert 확인 (긴 시간 대기)
        log(f"  🔍 Alert 확인 중...")
        alert_shown = False
        try:
            alert_shown = try_accept_alert_improved(driver, 20.0)  # 20초로 증가
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e):
                raise
            if "NO_DATA_AVAILABLE" in str(e):
                raise
            log(f"  ⚠️  Alert 확인 중 오류: {e}")
        
        # 디버깅: 스크린샷 (클릭 후)
        try:
            screenshot_path = TEMP_DOWNLOAD_DIR / f"after_click_{datetime.now().strftime('%H%M%S')}.png"
            driver.save_screenshot(str(screenshot_path))
            log(f"  📸 클릭 후 스크린샷: {screenshot_path.name}")
        except Exception as e:
            log(f"  ⚠️  스크린샷 실패: {e}")
        
        # Alert가 없으면 다운로드 시작되었을 수 있음
        if not alert_shown:
            log(f"  ℹ️  Alert 없음 - 다운로드 진행 중일 수 있음")
            time.sleep(5.0)  # 추가 대기
        
        log(f"  ✅ 다운로드 요청 완료")
        return True
        
    except Exception as e:
        if "DOWNLOAD_LIMIT_100" in str(e):
            raise
        if "NO_DATA_AVAILABLE" in str(e):
            raise
        log(f"  ❌ 다운로드 버튼 클릭 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


# ==================== 개선 4: 다운로드 대기 ====================
def wait_for_download_improved(timeout: int = 60, baseline_files: set = None) -> Optional[Path]:
    """다운로드 완료 대기 - 개선 버전 (60초)"""
    start_time = time.time()
    
    if baseline_files is None:
        baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
    
    log(f"  ⏳ 다운로드 대기 중... (최대 {timeout}초)")
    log(f"  📁 감시 폴더: {TEMP_DOWNLOAD_DIR.absolute()}")
    log(f"  📊 기존 파일: {len(baseline_files)}개")
    
    # 폴더 권한 재확인
    if not TEMP_DOWNLOAD_DIR.exists():
        log(f"  ⚠️  다운로드 폴더 없음! 생성 시도...")
        TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    # 초기 대기 (서버 응답 시간)
    log(f"  ⏳ 초기 대기 (5초)...")
    time.sleep(5.0)
    
    found_crdownload = False
    last_log_time = start_time
    check_interval = 0.5  # 0.5초마다 체크
    
    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)
        
        # 주기적으로 상태 로깅
        if time.time() - last_log_time >= 5.0:
            current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
            log(f"  ⏱️  {elapsed}초 경과... (현재 파일: {len(current_files)}개)")
            last_log_time = time.time()
            
            # 파일 목록 출력 (디버깅)
            if current_files:
                for f in current_files:
                    if f not in baseline_files:
                        log(f"     🆕 {f.name} ({f.stat().st_size:,} bytes)")
        
        # 현재 폴더의 모든 파일
        current_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
        
        # .crdownload 파일 확인
        crdownloads = [f for f in current_files if f.suffix == '.crdownload']
        if crdownloads:
            if not found_crdownload:
                log(f"  🔄 다운로드 시작 감지! (.crdownload 파일)")
                found_crdownload = True
            
            # 크기 변화 확인
            for cr in crdownloads:
                size = cr.stat().st_size
                if elapsed % 5 == 0:
                    log(f"  📥 다운로드 중... ({size:,} bytes)")
            
            time.sleep(check_interval)
            continue
        
        # 엑셀 파일 찾기 (새 파일만)
        excel_files = [
            f for f in current_files
            if f.is_file()
            and f.suffix.lower() in ['.xls', '.xlsx']
            and f not in baseline_files
        ]
        
        if excel_files:
            # 가장 최근 파일
            latest = max(excel_files, key=lambda p: p.stat().st_mtime)
            size = latest.stat().st_size
            
            log(f"  🎯 엑셀 파일 발견: {latest.name} ({size:,} bytes)")
            
            # 크기가 1KB 이상
            if size > 1000:
                # 크기 안정화 확인 (3초 대기)
                log(f"  ⏳ 파일 안정화 확인 중...")
                time.sleep(3.0)
                
                new_size = latest.stat().st_size
                
                # 크기가 동일하면 완료
                if new_size == size:
                    log(f"  ✅ 다운로드 완료: {latest.name} ({size:,} bytes, {elapsed}초 소요)")
                    return latest
                else:
                    log(f"  📝 파일 쓰기 진행 중... ({new_size:,} bytes)")
            else:
                log(f"  ⚠️  파일이 너무 작음 (< 1KB), 계속 대기...")
        
        time.sleep(check_interval)
    
    # 타임아웃
    log(f"  ⏱️  타임아웃 ({timeout}초)")
    
    # 디버깅: 최종 상태
    all_files = list(TEMP_DOWNLOAD_DIR.glob("*"))
    new_files = [f for f in all_files if f not in baseline_files]
    
    log(f"  📊 최종 상태:")
    log(f"     전체 파일: {len(all_files)}개")
    log(f"     새 파일: {len(new_files)}개")
    
    if new_files:
        log(f"  📁 새 파일 목록:")
        for f in new_files:
            log(f"     - {f.name} ({f.stat().st_size:,} bytes)")
    else:
        log(f"  ⚠️  다운로드된 파일 없음")
        
        # 폴더 권한 확인
        try:
            import stat
            folder_stat = TEMP_DOWNLOAD_DIR.stat()
            log(f"  📁 폴더 권한: {oct(folder_stat.st_mode)[-3:]}")
        except Exception as e:
            log(f"  ⚠️  폴더 권한 확인 실패: {e}")
    
    return None


# ==================== 개선 5: 재시도 로직 ====================
def download_single_month_with_retry_improved(driver, property_type: str, start_date: date, end_date: date, max_retries: int = 3) -> bool:
    """단일 월 다운로드 - 개선된 재시도"""
    year = start_date.year
    month = start_date.month
    
    log(f"\n{'='*60}")
    log(f"📅 {property_type} {year}년 {month}월")
    log(f"{'='*60}")
    
    # 이미 다운로드됨?
    if is_already_downloaded(property_type, year, month):
        log(f"  ⏭️  이미 존재함, 스킵")
        return True
    
    # temp 폴더 정리
    try:
        for old_file in TEMP_DOWNLOAD_DIR.glob("*"):
            if old_file.suffix.lower() in ['.xls', '.xlsx', '.crdownload', '.tmp']:
                old_file.unlink()
                log(f"  🧹 이전 파일 삭제: {old_file.name}")
    except Exception as e:
        log(f"  ⚠️  temp 폴더 정리 실패: {e}")
    
    # 재시도 로직
    for attempt in range(1, max_retries + 1):
        log(f"\n  🔄 시도 {attempt}/{max_retries}")
        
        try:
            # 날짜 설정
            if not set_dates(driver, start_date, end_date):
                if attempt < max_retries:
                    log(f"  ⏳ 10초 대기 후 재시도...")
                    time.sleep(10)
                    continue
                return False
            
            # 날짜 설정 후 Alert 확인
            try:
                try_accept_alert_improved(driver, 3.0)
            except Exception as e:
                if "NO_DATA_AVAILABLE" in str(e):
                    log(f"  ⏭️  데이터 없음, 스킵")
                    return True
                elif "DOWNLOAD_LIMIT_100" in str(e):
                    raise
            
            # 페이지 반영 대기
            time.sleep(2.0)
            
            # 다운로드 클릭 직전 파일 목록
            baseline_files = set(TEMP_DOWNLOAD_DIR.glob("*"))
            log(f"  📊 기존 파일: {len(baseline_files)}개")
            
            # 다운로드 클릭
            if not click_excel_download_improved(driver):
                if attempt < max_retries:
                    log(f"  ⏳ 10초 대기 후 재시도...")
                    time.sleep(10)
                    continue
                return False
            
            # 다운로드 대기 (60초)
            downloaded = wait_for_download_improved(timeout=60, baseline_files=baseline_files)
            
            if downloaded:
                # 성공!
                log(f"  🎉 다운로드 성공!")
                try:
                    move_and_rename_file(downloaded, property_type, year, month)
                    return True
                except Exception as e:
                    log(f"  ❌ 파일 이동 실패: {e}")
                    if attempt < max_retries:
                        log(f"  ⏳ 10초 대기 후 재시도...")
                        time.sleep(10)
                        continue
                    return False
            else:
                # 실패
                log(f"  ❌ 다운로드 실패")
                if attempt < max_retries:
                    log(f"  ⏳ 10초 대기 후 재시도...")
                    time.sleep(10)
                else:
                    log(f"  ❌ {max_retries}회 시도 모두 실패")
                    return False
                    
        except Exception as e:
            if "DOWNLOAD_LIMIT_100" in str(e):
                raise
            if "NO_DATA_AVAILABLE" in str(e):
                log(f"  ⏭️  데이터 없음, 스킵")
                return True
            
            log(f"  ❌ 오류 발생: {e}")
            import traceback
            traceback.print_exc()
            
            if attempt < max_retries:
                log(f"  ⏳ 10초 대기 후 재시도...")
                time.sleep(10)
            else:
                return False
    
    return False


# ==================== 사용 방법 ====================
"""
원본 download_realdata.py 파일에서 다음 함수들을 교체하세요:

1. build_driver() → build_driver_improved()
2. try_accept_alert() → try_accept_alert_improved()
3. click_excel_download() → click_excel_download_improved()
4. wait_for_download() → wait_for_download_improved()
5. download_single_month_with_retry() → download_single_month_with_retry_improved()

또는 main() 함수 시작 부분에서:
    driver = build_driver_improved()
로 변경하고, 다른 함수들도 _improved 버전을 호출하도록 수정하세요.
"""
