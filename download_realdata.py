def build_driver():

    """크롬 드라이버 생성"""

    log("  🔧 build_driver() 시작")

    

    opts = Options()

    if IS_CI:

        opts.add_argument("--headless=new")

        log("  ✅ Headless 모드 활성화")

    

    opts.add_argument("--no-sandbox")

    opts.add_argument("--disable-dev-shm-usage")

    opts.add_argument("--disable-gpu")

    opts.add_argument("--window-size=1400,900")

    opts.add_argument("--lang=ko-KR")

    

    # 로컬 실행 시 안정성 개선

    if not IS_CI:

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

    log("  ✅ Chrome 옵션 설정 완료")

    

    # CI 환경

    chromedriver_bin = os.getenv("CHROMEDRIVER_BIN")

    log(f"  🔍 CHROMEDRIVER_BIN 환경변수: {chromedriver_bin}")

    

    if chromedriver_bin and Path(chromedriver_bin).exists():

        log(f"  ✅ 환경변수에서 ChromeDriver 발견: {chromedriver_bin}")

        service = Service(chromedriver_bin)

    else:

        log("  🔍 webdriver_manager를 사용하여 ChromeDriver 다운로드 시작")

        from webdriver_manager.chrome import ChromeDriverManager

        

        try:

            driver_path = ChromeDriverManager().install()

            log(f"  📥 ChromeDriverManager().install() 반환값: {driver_path}")

            log(f"  📥 반환값 타입: {type(driver_path)}")

        except Exception as e:

            log(f"  ❌ ChromeDriverManager().install() 실패: {e}")

            raise

        

        driver_path_obj = Path(driver_path)

        log(f"  📂 Path 객체 생성: {driver_path_obj}")

        log(f"  📂 절대 경로: {driver_path_obj.absolute()}")

        log(f"  📂 존재 여부: {driver_path_obj.exists()}")

        log(f"  📂 디렉토리인가? {driver_path_obj.is_dir()}")

        log(f"  📂 파일인가? {driver_path_obj.is_file()}")

        

        # 디렉토리인 경우 실행 파일 찾기

        if driver_path_obj.is_dir():

            log(f"  🔍 디렉토리로 확인됨 - 실행 파일 찾기 시작")

            log(f"  📁 디렉토리 경로: {driver_path}")

            

            # 디렉토리 내용 확인

            try:

                all_files_raw = list(driver_path_obj.iterdir())

                log(f"  📋 디렉토리 내 파일/폴더 수: {len(all_files_raw)}")

                for idx, item in enumerate(all_files_raw, 1):

                    log(f"    [{idx}] {item.name} (파일: {item.is_file()}, 폴더: {item.is_dir()})")

            except Exception as e:

                log(f"  ⚠️  디렉토리 내용 읽기 실패: {e}")

                all_files_raw = []

            

            # 우선순위: 1) chromedriver (확장자 없음), 2) chromedriver.exe

            candidates = [

                driver_path_obj / "chromedriver",

                driver_path_obj / "chromedriver.exe",

            ]

            log(f"  🔍 후보 파일 검색 시작 (총 {len(candidates)}개)")

            

            found = False

            for idx, candidate in enumerate(candidates, 1):

                log(f"  🔍 후보 {idx}/{len(candidates)}: {candidate}")

                log(f"    - 존재 여부: {candidate.exists()}")

                if candidate.exists():

                    log(f"    - 파일인가? {candidate.is_file()}")

                    log(f"    - 디렉토리인가? {candidate.is_dir()}")

                

                if candidate.exists() and candidate.is_file():

                    log(f"    ✅ 파일 발견! 실행 권한 확인 중...")

                    # 실행 권한 확인 (Unix/Linux)

                    try:

                        is_executable = os.access(candidate, os.X_OK)

                        log(f"    - 실행 권한 (os.X_OK): {is_executable}")

                        log(f"    - 확장자: {candidate.suffix}")

                        

                        if is_executable or candidate.suffix == '.exe':

                            driver_path = str(candidate.absolute())

                            log(f"  ✅ ChromeDriver 실행 파일 발견: {driver_path}")

                            log(f"  📝 파일명: {candidate.name}")

                            found = True

                            break

                        else:

                            log(f"    ⚠️  실행 권한 없음 - 다음 후보로")

                    except Exception as e:

                        log(f"    ⚠️  실행 권한 확인 실패: {e}")

                        pass

                else:

                    log(f"    ⏭️  파일 없음 - 다음 후보로")

            

            if not found:

                log(f"  ⚠️  기본 후보에서 찾지 못함 - 전체 검색 시작")

                # 디렉토리 내 모든 파일 검색

                all_files = list(driver_path_obj.iterdir())

                log(f"  📋 전체 파일/폴더 수: {len(all_files)}")

                

                executable_files = []

                

                for idx, f in enumerate(all_files, 1):

                    log(f"  [{idx}/{len(all_files)}] 검사: {f.name}")

                    

                    if not f.is_file():

                        log(f"    ⏭️  파일이 아님 (폴더이거나 기타) - 스킵")

                        continue

                    

                    log(f"    ✅ 파일 확인됨")

                    

                    # NOTICES 파일 완전히 제외 (대소문자 구분 없이)

                    if 'NOTICES' in f.name.upper():

                        log(f"    🚫 NOTICES 파일 감지 - 제외")

                        continue

                    

                    # 텍스트 파일, 스크립트 파일 제외

                    if f.suffix in ['.txt', '.sh', '.md', '.pdf', '.json']:

                        log(f"    🚫 텍스트/스크립트 파일 (.{f.suffix}) - 제외")

                        continue

                    

                    # 파일명이 정확히 "chromedriver"인 경우 우선

                    if f.name == "chromedriver" or f.name == "chromedriver.exe":

                        log(f"    ⭐ 우선순위 파일 발견! (정확히 'chromedriver')")

                        executable_files.insert(0, f)

                        continue

                    

                    # chromedriver로 시작하되 NOTICES가 없는 경우

                    if f.name.lower().startswith("chromedriver"):

                        log(f"    ✅ chromedriver로 시작하는 파일 발견")

                        executable_files.append(f)

                        continue

                    

                    log(f"    ⏭️  조건 불일치 - 스킵")

                

                log(f"  📊 검색 결과: {len(executable_files)}개 파일 발견")

                for idx, f in enumerate(executable_files, 1):

                    log(f"    [{idx}] {f.name} (경로: {f.absolute()})")

                

                if executable_files:

                    # 첫 번째 파일 선택 (우선순위: chromedriver > chromedriver로 시작하는 파일)

                    selected = executable_files[0]

                    driver_path = str(selected.absolute())

                    log(f"  ✅ ChromeDriver 파일 발견: {driver_path}")

                    log(f"  📝 선택된 파일명: {selected.name}")

                    found = True

                else:

                    log(f"  ⚠️  실행 가능한 파일 없음 - 상위 디렉토리 검색")

                    # 상위 디렉토리에서 찾기

                    parent_chromedriver = driver_path_obj.parent / "chromedriver"

                    log(f"  🔍 상위 디렉토리 후보: {parent_chromedriver}")

                    log(f"    - 존재 여부: {parent_chromedriver.exists()}")

                    if parent_chromedriver.exists():

                        log(f"    - 파일인가? {parent_chromedriver.is_file()}")

                    

                    if parent_chromedriver.exists() and parent_chromedriver.is_file():

                        driver_path = str(parent_chromedriver.absolute())

                        log(f"  ✅ 상위 디렉토리에서 ChromeDriver 발견: {driver_path}")

                        found = True

                    else:

                        log(f"  ❌ ChromeDriver 실행 파일을 찾을 수 없습니다")

                        log(f"  📁 원본 디렉토리: {driver_path}")

                        log(f"  📁 디렉토리 내용: {[f.name for f in all_files]}")

                        raise RuntimeError(f"ChromeDriver executable not found in {driver_path}")

        else:

            log(f"  🔍 파일 경로로 확인됨")

            # 이미 파일 경로인 경우

            if not driver_path_obj.exists():

                log(f"  ❌ 파일이 존재하지 않음: {driver_path}")

                raise RuntimeError(f"ChromeDriver not found at {driver_path}")

            

            # 파일명 검증

            file_name = driver_path_obj.name

            log(f"  📝 파일명: {file_name}")

            

            if 'NOTICES' in file_name.upper():

                log(f"  ⚠️  NOTICES 파일 감지! 실제 chromedriver 파일을 찾아야 함")

                log(f"  🔍 상위 디렉토리에서 chromedriver 파일 검색")

                

                parent_dir = driver_path_obj.parent

                log(f"  📁 상위 디렉토리: {parent_dir}")

                

                if parent_dir.exists() and parent_dir.is_dir():

                    log(f"  📋 상위 디렉토리 내용 확인 중...")

                    try:

                        parent_files = list(parent_dir.iterdir())

                        log(f"  📋 파일/폴더 수: {len(parent_files)}")

                        for item in parent_files:

                            log(f"    - {item.name} (파일: {item.is_file()})")

                            

                            # chromedriver 파일 찾기 (NOTICES 제외)

                            if item.is_file() and 'NOTICES' not in item.name.upper():

                                if item.name == "chromedriver" or item.name.lower().startswith("chromedriver"):

                                    driver_path = str(item.absolute())

                                    log(f"  ✅ 대체 파일 발견: {driver_path}")

                                    log(f"  📝 파일명: {item.name}")

                                    driver_path_obj = Path(driver_path)

                                    break

                    except Exception as e:

                        log(f"  ⚠️  상위 디렉토리 검색 실패: {e}")

                

                # 여전히 NOTICES 파일이면 에러

                if 'NOTICES' in driver_path_obj.name.upper():

                    log(f"  ❌ 여전히 NOTICES 파일임 - 에러 발생")

                    raise RuntimeError(f"ChromeDriver path points to NOTICES file: {driver_path}")

            

            driver_path = str(driver_path_obj.absolute())

            log(f"  ✅ 파일 경로 사용: {driver_path}")

        

        service = Service(driver_path)

        log(f"  📦 Service 객체 생성 완료")

        log(f"  📦 최종 ChromeDriver 경로: {driver_path}")

        log(f"  📦 파일명: {Path(driver_path).name}")

    

    chrome_bin = os.getenv("CHROME_BIN")

    if chrome_bin:

        opts.binary_location = chrome_bin

        log(f"  ✅ CHROME_BIN 설정: {chrome_bin}")

    else:

        log(f"  ℹ️  CHROME_BIN 환경변수 없음 (기본값 사용)")

    

    log(f"  🚀 webdriver.Chrome() 생성 시도...")

    try:

        driver = webdriver.Chrome(service=service, options=opts)

        log(f"  ✅ Chrome 드라이버 생성 성공!")

    except Exception as e:

        log(f"  ❌ Chrome 드라이버 생성 실패: {e}")

        log(f"  📦 사용된 경로: {driver_path if 'driver_path' in locals() else 'N/A'}")

        raise

    

    return driver
