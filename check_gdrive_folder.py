#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Drive 폴더 내용 확인 스크립트
"""
import os
import json
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build

def main():
    service_account_path = os.getenv('SERVICE_ACCOUNT_JSON', 'service-account.json')
    folder_id = os.getenv('GDRIVE_FOLDER_ID', '1x3lHLwrixnqVFpUoxkEzqgmcn19Jhw19')
    
    if not Path(service_account_path).exists():
        print(f"❌ 서비스 계정 파일이 없습니다: {service_account_path}")
        return
    
    print(f"📂 Google Drive 폴더 확인: {folder_id}")
    print("=" * 70)
    
    try:
        SCOPES = ['https://www.googleapis.com/auth/drive.file']
        creds = service_account.Credentials.from_service_account_file(
            service_account_path, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        
        # 폴더 목록 조회
        query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=100
        ).execute()
        folders = results.get('files', [])
        
        print(f"📁 폴더 {len(folders)}개 발견:\n")
        
        for folder in folders:
            folder_name = folder['name']
            folder_id_sub = folder['id']
            print(f"  📂 {folder_name} (ID: {folder_id_sub})")
            
            # 각 폴더의 파일 목록
            query = f"'{folder_id_sub}' in parents and trashed=false"
            results = service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, size, modifiedTime)',
                pageSize=1000,
                orderBy='name'
            ).execute()
            files = results.get('files', [])
            
            if files:
                print(f"     파일 {len(files)}개:")
                for f in files[:20]:  # 최대 20개만 표시
                    size = f.get('size', 'N/A')
                    modified = f.get('modifiedTime', 'N/A')[:10]  # 날짜만
                    print(f"       - {f['name']} ({size} bytes, {modified})")
                if len(files) > 20:
                    print(f"       ... 외 {len(files) - 20}개")
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
