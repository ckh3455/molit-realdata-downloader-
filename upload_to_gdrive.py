#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Drive 관리 스크립트
- 다운로드 전: Drive에서 기존 파일 목록 조회 → 스킵 리스트 생성
- 다운로드 후: 새 파일만 업로드

사용법:
    # 기존 파일 목록 확인
    python upload_to_gdrive.py --check-existing
    
    # 업로드 실행
    python upload_to_gdrive.py --upload

환경변수:
    SERVICE_ACCOUNT_JSON: 서비스 계정 JSON 파일 경로
    GDRIVE_FOLDER_ID: Google Drive 폴더 ID (URL 또는 ID만)
"""
import os
import sys
import json
import re
from pathlib import Path
from typing import Set, Dict
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def log(msg: str):
    """로그 출력"""
    print(msg, flush=True)


def extract_folder_id(folder_id_raw: str) -> str:
    """URL에서 폴더 ID 추출"""
    if not folder_id_raw:
        return None
    
    # URL 형식: https://drive.google.com/drive/folders/1x3lHLwrixnqVFpUoxkEzqgmcn19Jhw19
    if 'folders/' in folder_id_raw:
        folder_id = folder_id_raw.split('folders/')[-1].split('?')[0].split('/')[0].strip()
    else:
        folder_id = folder_id_raw.strip()
    
    return folder_id


def get_drive_service(service_account_path: str):
    """Google Drive 서비스 생성"""
    # 읽기 권한도 필요하므로 drive.readonly 추가
    SCOPES = [
        'https://www.googleapis.com/auth/drive.file',
        'https://www.googleapis.com/auth/drive.readonly'
    ]
    creds = service_account.Credentials.from_service_account_file(
        service_account_path, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)


def parse_filename(filename: str) -> tuple:
    """파일명에서 종목, 년도, 월 추출
    예: "아파트 202411.xlsx" -> ("아파트", 2024, 11)
    """
    match = re.match(r'^(.+?)\s+(\d{4})(\d{2})\.xlsx$', filename)
    if match:
        property_type = match.group(1)
        year = int(match.group(2))
        month = int(match.group(3))
        return (property_type, year, month)
    return None


def get_existing_files(service, folder_id: str) -> Dict[str, Set[str]]:
    """
    Google Drive의 기존 파일 목록 조회 (페이지네이션 지원)
    
    Returns:
        {폴더명: {파일명1, 파일명2, ...}}
    """
    log("🔍 Google Drive 기존 파일 확인 중...")
    log(f"   📂 루트 폴더 ID: {folder_id}")
    
    existing = {}
    
    # 폴더 목록 조회 (페이지네이션 처리)
    query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    folders = []
    page_token = None
    
    log(f"   🔎 쿼리: {query}")
    
    while True:
        try:
            results = service.files().list(
                q=query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=100,
                pageToken=page_token
            ).execute()
        except Exception as e:
            log(f"   ❌ 폴더 목록 조회 실패: {e}")
            raise
        
        folders.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break
    
    log(f"   📂 {len(folders)}개 폴더 발견")
    for f in folders:
        log(f"      - {f['name']} (ID: {f['id']})")
    log("")
    
    if len(folders) == 0:
        log("   ⚠️  폴더가 없습니다. 폴더 ID가 올바른지 확인하세요.")
        return {}
    
    for folder in folders:
        folder_name = folder['name']
        folder_id_sub = folder['id']
        
        log(f"   📁 '{folder_name}' 폴더 스캔 중... (ID: {folder_id_sub})")
        
        # 각 폴더의 파일 목록 (페이지네이션 처리)
        query = f"'{folder_id_sub}' in parents and trashed=false"
        files = []
        page_token = None
        page_num = 0
        
        while True:
            page_num += 1
            try:
                results = service.files().list(
                    q=query,
                    spaces='drive',
                    fields='nextPageToken, files(id, name, size, modifiedTime)',
                    pageSize=1000,
                    pageToken=page_token,
                    orderBy='name'
                ).execute()
            except Exception as e:
                log(f"      ❌ 페이지 {page_num} 조회 실패: {e}")
                break
            
            page_files = results.get('files', [])
            files.extend(page_files)
            log(f"      페이지 {page_num}: {len(page_files)}개 파일")
            
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        
        file_names = {f['name'] for f in files}
        existing[folder_name] = file_names
        
        if file_names:
            log(f"   ✅ {folder_name}: 총 {len(file_names)}개 파일")
            # 파일명에서 날짜 추출하여 정렬
            parsed_files = []
            for fname in sorted(file_names):
                parsed = parse_filename(fname)
                if parsed:
                    parsed_files.append((parsed, fname))
            
            if parsed_files:
                # 년도, 월로 정렬
                parsed_files.sort(key=lambda x: (x[0][1], x[0][2]))
                log(f"      최초 파일: {parsed_files[0][1]} ({parsed_files[0][0][1]}-{parsed_files[0][0][2]:02d})")
                log(f"      최신 파일: {parsed_files[-1][1]} ({parsed_files[-1][0][1]}-{parsed_files[-1][0][2]:02d})")
                
                # 샘플 파일명 몇 개 출력 (2006년, 2024년 등)
                sample_files = []
                for year in [2006, 2010, 2015, 2020, 2024]:
                    for parsed, fname in parsed_files:
                        if parsed[0][1] == year:
                            sample_files.append(fname)
                            break
                if sample_files:
                    log(f"      샘플: {', '.join(sample_files[:5])}")
            else:
                log(f"      ⚠️  날짜 파싱 가능한 파일 없음")
        else:
            log(f"   ⚠️  {folder_name}: (파일 없음)")
        log("")
    
    log("✅ 기존 파일 확인 완료\n")
    return existing


def check_existing_files(service_account_path: str, folder_id: str):
    """기존 파일 목록 조회 후 JSON 저장"""
    service = get_drive_service(service_account_path)
    existing = get_existing_files(service, folder_id)
    
    # JSON 저장
    output_file = Path('existing_files.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({k: sorted(list(v)) for k, v in existing.items()}, f, indent=2, ensure_ascii=False)
    
    log(f"💾 기존 파일 목록 저장: {output_file}")
    
    # 통계
    total_files = sum(len(files) for files in existing.values())
    log(f"📊 전체 {total_files}개 파일")
    log("")
    
    # 각 폴더별 상세 정보
    log("=" * 60)
    log("📋 폴더별 파일 목록 요약")
    log("=" * 60)
    for folder_name, files in existing.items():
        if files:
            log(f"\n📁 {folder_name}: {len(files)}개")
            # 파일명에서 날짜 추출하여 정렬
            parsed_files = []
            for fname in sorted(files):
                parsed = parse_filename(fname)
                if parsed:
                    parsed_files.append((parsed, fname))
            
            if parsed_files:
                parsed_files.sort(key=lambda x: (x[0][1], x[0][2]))
                log(f"   최초: {parsed_files[0][1]} ({parsed_files[0][0][1]}-{parsed_files[0][0][2]:02d})")
                log(f"   최신: {parsed_files[-1][1]} ({parsed_files[-1][0][1]}-{parsed_files[-1][0][2]:02d})")
                log(f"   범위: {parsed_files[0][0][1]}{parsed_files[0][0][2]:02d} ~ {parsed_files[-1][0][1]}{parsed_files[-1][0][2]:02d}")
        else:
            log(f"\n📁 {folder_name}: (파일 없음)")
    log("")
    
    return existing


def upload_to_drive(service_account_path: str, folder_id: str, local_dir: Path):
    """
    output 폴더의 파일들을 Google Drive에 업로드
    existing_files.json을 참고하여 새 파일만 업로드
    """
    service = get_drive_service(service_account_path)
    
    # 기존 파일 목록 로드
    existing_file = Path('existing_files.json')
    if existing_file.exists():
        with open(existing_file, 'r', encoding='utf-8') as f:
            existing_files = json.load(f)
            existing_files = {k: set(v) for k, v in existing_files.items()}
        log("📋 기존 파일 목록 로드됨\n")
    else:
        log("⚠️  기존 파일 목록 없음 - 모든 파일 업로드\n")
        existing_files = {}
    
    if not local_dir.exists():
        log(f"❌ 디렉토리가 없습니다: {local_dir}")
        return
    
    uploaded_count = 0
    updated_count = 0
    skipped_count = 0
    
    # output 폴더의 모든 하위 폴더 순회
    for folder_path in sorted(local_dir.iterdir()):
        if not folder_path.is_dir():
            continue
        
        folder_name = folder_path.name
        log(f"📁 처리 중: {folder_name}")
        
        # Drive에서 해당 폴더 찾기 또는 생성
        query = f"name='{folder_name}' and '{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        items = results.get('files', [])
        
        if items:
            drive_folder_id = items[0]['id']
            log(f"   📂 기존 폴더 사용 (ID: {drive_folder_id})")
        else:
            # 폴더 생성
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [folder_id]
            }
            folder_file = service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()
            drive_folder_id = folder_file.get('id')
            log(f"   📂 새 폴더 생성 (ID: {drive_folder_id})")
        
        # 기존 파일 목록
        existing_in_folder = existing_files.get(folder_name, set())
        log(f"   📋 기존 파일: {len(existing_in_folder)}개")
        
        # 폴더 안의 파일들 업로드
        excel_files = sorted(folder_path.glob('*.xlsx'))
        log(f"   📦 업로드 대상: {len(excel_files)}개")
        
        for file_path in excel_files:
            file_name = file_path.name
            file_size = file_path.stat().st_size
            
            # 이미 존재하는 파일이면 스킵
            if file_name in existing_in_folder:
                log(f"   ⏭️  스킵: {file_name} (이미 존재)")
                skipped_count += 1
                continue
            
            # Drive에서 파일 확인 (혹시 모를 경우 대비)
            query = f"name='{file_name}' and '{drive_folder_id}' in parents and trashed=false"
            results = service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, size)'
            ).execute()
            items = results.get('files', [])
            
            if items:
                # 기존 파일이 있으면 크기 비교
                existing_file_obj = items[0]
                existing_size = int(existing_file_obj.get('size', 0))
                
                # 크기가 다르면 업데이트
                if existing_size != file_size:
                    file_id = existing_file_obj['id']
                    media = MediaFileUpload(str(file_path), resumable=True)
                    service.files().update(
                        fileId=file_id,
                        media_body=media
                    ).execute()
                    log(f"   ✅ 업데이트: {file_name} ({file_size:,} bytes)")
                    updated_count += 1
                else:
                    log(f"   ⏭️  스킵: {file_name} (동일)")
                    skipped_count += 1
            else:
                # 새 파일 업로드
                file_metadata = {
                    'name': file_name,
                    'parents': [drive_folder_id]
                }
                media = MediaFileUpload(str(file_path), resumable=True)
                service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                ).execute()
                log(f"   ✅ 업로드: {file_name} ({file_size:,} bytes)")
                uploaded_count += 1
        
        log("")
    
    log("=" * 60)
    log(f"🎉 완료!")
    log(f"   새 파일: {uploaded_count}개")
    log(f"   업데이트: {updated_count}개")
    log(f"   스킵: {skipped_count}개")
    log("=" * 60)


def main():
    """메인 함수"""
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-existing', action='store_true', help='기존 파일 확인')
    parser.add_argument('--upload', action='store_true', help='파일 업로드')
    args = parser.parse_args()
    
    # 환경변수에서 설정 읽기
    service_account_path = os.getenv('SERVICE_ACCOUNT_JSON', 'service-account.json')
    folder_id_raw = os.getenv('GDRIVE_FOLDER_ID')
    local_dir = Path(os.getenv('OUTPUT_DIR', 'output'))
    
    # 폴더 ID 추출 (URL 또는 ID만)
    folder_id = extract_folder_id(folder_id_raw)
    
    if not folder_id:
        log("❌ GDRIVE_FOLDER_ID 환경변수가 설정되지 않았습니다.")
        log("   예: 1x3lHLwrixnqVFpUoxkEzqgmcn19Jhw19")
        log("   또는: https://drive.google.com/drive/folders/1x3lHLwrixnqVFpUoxkEzqgmcn19Jhw19")
        sys.exit(1)
    
    if not Path(service_account_path).exists():
        log(f"❌ 서비스 계정 파일이 없습니다: {service_account_path}")
        sys.exit(1)
    
    log("=" * 60)
    log("📤 Google Drive 관리")
    log("=" * 60)
    if folder_id_raw != folder_id:
        log(f"   원본: {folder_id_raw}")
    log(f"☁️  폴더 ID: {folder_id}")
    log("")
    
    try:
        if args.check_existing:
            check_existing_files(service_account_path, folder_id)
        elif args.upload:
            log(f"📁 로컬: {local_dir.absolute()}")
            log("")
            upload_to_drive(service_account_path, folder_id, local_dir)
        else:
            log("❌ --check-existing 또는 --upload 옵션 필요")
            sys.exit(1)
            
    except Exception as e:
        log(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
