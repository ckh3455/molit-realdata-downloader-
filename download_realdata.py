import argparse

# ... 기존 코드 ...

def main():
    """메인 함수"""
    # ✅ 명령행 인자 파싱
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-mode", action="store_true", help="테스트 모드 (제한된 다운로드)")
    parser.add_argument("--max-months", type=int, default=2, help="테스트 모드에서 최대 다운로드 개월 수")
    parser.add_argument("--property", type=str, default=None, help="특정 부동산 종목만 다운로드")
    args = parser.parse_args()
    
    log("="*70)
    if args.test_mode:
        log("🧪 테스트 모드 (제한된 다운로드)")
        log(f"📊 최대 개월 수: {args.max_months}")
    else:
        log("🚀 국토부 실거래가 데이터 다운로드 시작")
    log("="*70)
    log(f"📂 저장 경로: {DOWNLOAD_DIR}")
    
    # 종목 필터링
    if args.property:
        properties_to_download = [p for p in PROPERTY_TYPES if args.property in p]
        log(f"📊 다운로드 종목: {properties_to_download}")
    else:
        properties_to_download = PROPERTY_TYPES
        log(f"📊 종목 수: {len(properties_to_download)}")
    
    log("")
    
    # 진행 상황 로드
    progress = load_progress()
    
    # 월별 날짜 생성
    monthly_dates = generate_monthly_dates(2006, 1)
    total_months = len(monthly_dates)
    
    # ✅ 테스트 모드: 최근 N개월만
    if args.test_mode:
        monthly_dates = monthly_dates[-args.max_months:]
        log(f"📅 테스트 다운로드 기간: {len(monthly_dates)}개월")
    else:
        log(f"📅 총 다운로드 기간: {total_months}개월 (2006-01 ~ {date.today().strftime('%Y-%m')})")
    
    log("")
    
    driver = build_driver()
    
    try:
        # 페이지 로드
        log("🌐 사이트 접속 중...")
        driver.get(MOLIT_URL)
        time.sleep(3)
        try_accept_alert(driver, 2.0)
        log("✅ 접속 완료\n")
        
        # 각 부동산 종목별로
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
            
            if last_completed:
                log(f"📌 마지막 완료: {last_completed}")
            
            # 각 월별로
            success_count = 0
            fail_count = 0
            
            for month_idx, (start_date, end_date) in enumerate(monthly_dates, 1):
                year = start_date.year
                month = start_date.month
                month_key = f"{year:04d}{month:02d}"
                
                # 이미 완료한 달 스킵 (테스트 모드가 아닐 때만)
                if not args.test_mode and last_completed and month_key <= last_completed:
                    continue
                
                log(f"\n[{month_idx}/{len(monthly_dates)}]", end=" ")
                
                # 다운로드 시도
                success = download_single_month(driver, property_type, start_date, end_date)
                
                if success:
                    success_count += 1
                    # 진행 상황 저장
                    if prop_key not in progress:
                        progress[prop_key] = {}
                    progress[prop_key]["last_month"] = month_key
                    progress[prop_key]["last_update"] = datetime.now().isoformat()
                    save_progress(progress)
                else:
                    fail_count += 1
                    log(f"⚠️  실패 카운트: {fail_count}")
                    
                    # 테스트 모드가 아닐 때만 자동 중단
                    if not args.test_mode and fail_count >= 3:
                        log(f"\n⛔ 연속 {fail_count}회 실패 - 다운로드 제한 가능성")
                        log(f"💾 진행 상황 저장됨: {PROGRESS_FILE}")
                        log(f"📌 다음 실행시 {month_key}부터 재개됩니다")
                        return
                
                # 다음 요청 전 대기 (서버 부하 방지)
                time.sleep(2)
            
            log(f"\n✅ {property_type} 완료: 성공 {success_count}, 실패 {fail_count}\n")
            
            # ✅ 테스트 모드: 첫 번째 종목만 테스트
            if args.test_mode:
                log("🧪 테스트 모드 - 첫 번째 종목만 완료")
                break
        
        log("="*70)
        if args.test_mode:
            log("🧪 테스트 완료!")
        else:
            log("🎉 모든 다운로드 완료!")
        log("="*70)
        
    except KeyboardInterrupt:
        log("\n⚠️  사용자 중단")
        log(f"💾 진행 상황 저장됨: {PROGRESS_FILE}")
    except Exception as e:
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
