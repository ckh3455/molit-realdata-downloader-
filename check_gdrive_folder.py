#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Drive 폴더 내용 확인 스크립트
"""
import os
import json
import re
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build

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

def parse_filename(filename: str):
    """파일명에서 날짜 추출"""
    match = re.match(r'^(.+?)\s+(\d{4})(\d{2})\.xlsx$', filename)
    if match:
        year = int(match.group(2))
        month = int(match.group(3))
        return (year, month)
    return None

def main():
    service_account_path = os.getenv('SERVICE_ACCOUNT_JSON', 'service-account.json')
    folder_id_raw = os.getenv('GDRIVE_FOLDER_ID', '1x3lHLwrixnqVFpUoxkEzqgmcn19Jhw19')
    
    # 폴더 ID 추출
    folder_id = extract_folder_id(folder_id_raw)
    
    if not Path(service_account_path).exists():
        print(f"❌ 서비스 계정 파일이 없습니다: {service_account_path}")
        return
    
    print(f"📂 Google Drive 폴더 확인")
    if folder_id_raw != folder_id:
        print(f"   원본: {folder_id_raw}")
    print(f"   폴더 ID: {folder_id}")
    print("=" * 70)
    
    try:
        SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive.readonly']
        creds = service_account.Credentials.from_service_account_file(
            service_account_path, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        
        # 폴더 목록 조회
        query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        print(f"🔎 쿼리: {query}\n")
        
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=100
        ).execute()
        folders = results.get('files', [])
        
        print(f"📁 폴더 {len(folders)}개 발견:\n")
        
        if len(folders) == 0:
            print("⚠️  폴더가 없습니다. 폴더 ID가 올바른지 확인하세요.")
            print(f"   현재 폴더 ID: {folder_id}")
            return
        
        for folder in folders:
            folder_name = folder['name']
            folder_id_sub = folder['id']
            print(f"  📂 {folder_name} (ID: {folder_id_sub})")
            
            # 각 폴더의 파일 목록 (페이지네이션)
            query = f"'{folder_id_sub}' in parents and trashed=false"
            files = []
            page_token = None
            page_num = 0
            
            while True:
                page_num += 1
                results = service.files().list(
                    q=query,
                    spaces='drive',
                    fields='nextPageToken, files(id, name, size, modifiedTime)',
                    pageSize=1000,
                    pageToken=page_token,
                    orderBy='name'
                ).execute()
                
                page_files = results.get('files', [])
                files.extend(page_files)
                page_token = results.get('nextPageToken')
                if not page_token:
                    break
            
            if files:
                print(f"     파일 {len(files)}개:")
                
                # 파일명에서 날짜 추출하여 정렬
                parsed_files = []
                for f in files:
                    parsed = parse_filename(f['name'])
                    if parsed:
                        parsed_files.append((parsed, f))
                
                if parsed_files:
                    parsed_files.sort(key=lambda x: (x[0][0], x[0][1]))
                    print(f"     최초: {parsed_files[0][1]['name']} ({parsed_files[0][0][0]}-{parsed_files[0][0][1]:02d})")
                    print(f"     최신: {parsed_files[-1][1]['name']} ({parsed_files[-1][0][0]}-{parsed_files[-1][0][1]:02d})")
                    
                    # 년도별 샘플 출력
                    print(f"     샘플:")
                    for year in [2006, 2010, 2015, 2020, 2024, 2025]:
                        for parsed, f in parsed_files:
                            if parsed[0] == year:
                                size = f.get('size', 'N/A')
                                modified = f.get('modifiedTime', 'N/A')[:10]
                                print(f"       - {f['name']} ({size} bytes, {modified})")
                                break
                else:
                    # 날짜 파싱 불가능한 파일들
                    for f in files[:10]:
                        size = f.get('size', 'N/A')
                        modified = f.get('modifiedTime', 'N/A')[:10]
                        print(f"       - {f['name']} ({size} bytes, {modified})")
                    if len(files) > 10:
                        print(f"       ... 외 {len(files) - 10}개")
            else:
                print(f"     (파일 없음)")
            print()
        
        print("=" * 70)
        print(f"✅ 확인 완료")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
