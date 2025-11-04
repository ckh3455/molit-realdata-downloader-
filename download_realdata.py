            driver_path = str(driver_path_obj.absolute())

            log(f"  🔧 [DEBUG] 파일 경로 사용: {driver_path}")

        

        # 실행 권한 부여 (Linux/Unix - CI 환경)

        if sys.platform != 'win32':

            try:

                current_perms = os.stat(driver_path).st_mode

                # 실행 권한 추가 (소유자, 그룹, 기타 모두)

                os.chmod(driver_path, current_perms | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

                is_executable_after = os.access(driver_path, os.X_OK)

                log(f"  🔧 [DEBUG] 실행 권한 부여 완료: {oct(os.stat(driver_path).st_mode)}")

                log(f"  🔧 [DEBUG] 실행 가능 여부 확인: {is_executable_after}")

            except Exception as e:

                log(f"  ⚠️  [DEBUG] 실행 권한 부여 실패: {e}")

                # 권한 부여 실패해도 계속 진행 (이미 권한이 있을 수도 있음)

        

        service = Service(driver_path)

        log(f"  🔧 [DEBUG] Service 객체 생성 완료")

        log(f"  📦 ChromeDriver 경로: {driver_path}")

        log(f"  📦 파일명: {Path(driver_path).name}")
