# -*- coding: utf-8 -*-
"""
Google Shared Drive 파일 업로드 모듈
- 폴더 자동 생성/찾기
- 파일 업로드 (Shared Drive 지원)
"""
import os
import json
from pathlib import Path
from typing import Optional, Dict
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# ==================== 설정 ====================
# 서비스 계정 파일 경로 (환경 변수 또는 직접 지정)
SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    r"C:\Users\Hello\OneDrive\office work\naver crawling\naver-crawling-476404-fcf4b10bc63e 클라우드 서비스계정.txt"
)

# Shared Drive ID (환경 변수 또는 직접 지정)
SHARED_DRIVE_ID = os.getenv("GOOGLE_SHARED_DRIVE_ID", "0APa-MWwUseXzUk9PVA")

# 부모 폴더 경로 (부동산자료 > 부동산 실거래자료)
PARENT_FOLDER_PATH = ["부동산자료", "부동산 실거래자료"]

# Google Drive API 스코프
SCOPES = ['https://www.googleapis.com/auth/drive']


class DriveUploader:
    """Google Drive 파일 업로드 클래스"""
    
    def __init__(self):
        self.drive = None
        self._folder_cache: Dict[str, str] = {}  # 폴더명 -> 폴더ID 캐시
        self._initialized = False
    
    def init_service(self):
        """Google Drive API 서비스 초기화"""
        if self._initialized:
            return True
            
        try:
            # 서비스 계정 파일 읽기
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                creds = service_account.Credentials.from_service_account_file(
                    SERVICE_ACCOUNT_FILE,
                    scopes=SCOPES
                )
            else:
                # 환경 변수에서 JSON 문자열로 읽기 (GitHub Actions용)
                service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
                if not service_account_json:
                    raise FileNotFoundError(
                        f"서비스 계정 파일을 찾을 수 없습니다: {SERVICE_ACCOUNT_FILE}\n"
                        "또는 GOOGLE_SERVICE_ACCOUNT_JSON 환경 변수를 설정하세요."
                    )
                creds = service_account.Credentials.from_service_account_info(
                    json.loads(service_account_json),
                    scopes=SCOPES
                )
            
            self.drive = build('drive', 'v3', credentials=creds)
            self._initialized = True
            return True
        except Exception as e:
            print(f"❌ Google Drive API 초기화 실패: {e}")
            return False
    
    def find_folder_by_name(self, folder_name: str, parent_folder_id: str = None) -> Optional[str]:
        """폴더 이름으로 폴더 ID 찾기"""
        try:
            # 캐시 확인
            cache_key = f"{parent_folder_id or 'root'}:{folder_name}"
            if cache_key in self._folder_cache:
                return self._folder_cache[cache_key]
            
            # 검색 쿼리 구성
            query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            
            if parent_folder_id:
                query += f" and '{parent_folder_id}' in parents"
            
            params = {
                'q': query,
                'fields': 'files(id, name)',
                'supportsAllDrives': True,
                'includeItemsFromAllDrives': True,
            }
            
            if SHARED_DRIVE_ID:
                params['driveId'] = SHARED_DRIVE_ID
                params['corpora'] = 'drive'
            
            results = self.drive.files().list(**params).execute()
            items = results.get('files', [])
            
            if items:
                folder_id = items[0]['id']
                self._folder_cache[cache_key] = folder_id
                return folder_id
            
            return None
        except HttpError as e:
            print(f"  ❌ 폴더 검색 실패: {e}")
            return None
    
    def create_folder(self, folder_name: str, parent_folder_id: str = None) -> Optional[str]:
        """폴더 생성"""
        try:
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
            }
            
            if parent_folder_id:
                file_metadata['parents'] = [parent_folder_id]
            
            params = {
                'body': file_metadata,
                'fields': 'id, name',
                'supportsAllDrives': True,
            }
            
            if SHARED_DRIVE_ID:
                params['driveId'] = SHARED_DRIVE_ID
            
            folder = self.drive.files().create(**params).execute()
            folder_id = folder.get('id')
            
            # 캐시에 저장
            cache_key = f"{parent_folder_id or 'root'}:{folder_name}"
            self._folder_cache[cache_key] = folder_id
            
            return folder_id
        except HttpError as e:
            print(f"  ❌ 폴더 생성 실패: {e}")
            return None
    
    def get_or_create_folder(self, folder_name: str, parent_folder_id: str = None) -> Optional[str]:
        """폴더 찾기 또는 생성"""
        # 먼저 찾기 시도
        folder_id = self.find_folder_by_name(folder_name, parent_folder_id)
        
        if folder_id:
            return folder_id
        
        # 없으면 생성
        return self.create_folder(folder_name, parent_folder_id)
    
    def get_folder_path_ids(self) -> Optional[Dict[str, str]]:
        """부모 폴더 경로의 각 폴더 ID 가져오기"""
        folder_ids = {}
        current_parent = None
        
        for folder_name in PARENT_FOLDER_PATH:
            folder_id = self.find_folder_by_name(folder_name, current_parent)
            
            if not folder_id:
                # 폴더가 없으면 생성
                folder_id = self.create_folder(folder_name, current_parent)
            
            if not folder_id:
                print(f"❌ 폴더 경로를 찾거나 생성할 수 없습니다: {folder_name}")
                return None
            
            folder_ids[folder_name] = folder_id
            current_parent = folder_id
        
        return folder_ids
    
    def upload_file(self, local_file_path: Path, file_name: str, section_folder_name: str) -> Optional[str]:
        """파일 업로드"""
        if not self.drive:
            print("❌ Drive 서비스가 초기화되지 않았습니다.")
            return None
        
        try:
            # 1. 부모 폴더 경로 확인
            path_ids = self.get_folder_path_ids()
            if not path_ids:
                return None
            
            # 2. 섹션별 폴더 찾기 또는 생성
            section_parent_id = path_ids[PARENT_FOLDER_PATH[-1]]  # "부동산 실거래자료" 폴더 ID
            section_folder_id = self.get_or_create_folder(section_folder_name, section_parent_id)
            
            if not section_folder_id:
                print(f"❌ 섹션 폴더를 찾거나 생성할 수 없습니다: {section_folder_name}")
                return None
            
            # 3. 파일 업로드
            file_metadata = {
                'name': file_name,
                'parents': [section_folder_id],
            }
            
            media = MediaFileUpload(
                str(local_file_path),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                resumable=True
            )
            
            params = {
                'body': file_metadata,
                'media': media,
                'fields': 'id, name, webViewLink, size',
                'supportsAllDrives': True,
            }
            
            if SHARED_DRIVE_ID:
                params['driveId'] = SHARED_DRIVE_ID
            
            file = self.drive.files().create(**params).execute()
            file_id = file.get('id')
            
            print(f"  ✅ Google Drive 업로드 완료: {file_name}")
            print(f"     파일 ID: {file_id}")
            print(f"     링크: {file.get('webViewLink', 'N/A')}")
            
            return file_id
            
        except HttpError as e:
            print(f"  ❌ 파일 업로드 실패: {e}")
            if e.resp.status == 404:
                print("     파일을 찾을 수 없습니다.")
            elif e.resp.status == 403:
                print("     권한이 없습니다. Shared Drive 멤버 권한을 확인하세요.")
            return None
        except Exception as e:
            print(f"  ❌ 업로드 중 오류 발생: {e}")
            return None
    
    def check_file_exists(self, file_name: str, section_folder_name: str) -> bool:
        """파일이 이미 존재하는지 확인"""
        try:
            # 부모 폴더 경로 확인
            path_ids = self.get_folder_path_ids()
            if not path_ids:
                return False
            
            # 섹션별 폴더 찾기
            section_parent_id = path_ids[PARENT_FOLDER_PATH[-1]]
            section_folder_id = self.find_folder_by_name(section_folder_name, section_parent_id)
            
            if not section_folder_id:
                return False
            
            # 파일 검색
            query = f"name='{file_name}' and '{section_folder_id}' in parents and trashed=false"
            
            params = {
                'q': query,
                'fields': 'files(id, name)',
                'supportsAllDrives': True,
                'includeItemsFromAllDrives': True,
            }
            
            if SHARED_DRIVE_ID:
                params['driveId'] = SHARED_DRIVE_ID
                params['corpora'] = 'drive'
            
            results = self.drive.files().list(**params).execute()
            items = results.get('files', [])
            
            return len(items) > 0
            
        except Exception as e:
            print(f"  ⚠️  파일 존재 확인 실패: {e}")
            return False


# 전역 인스턴스
_uploader_instance = None

def get_uploader() -> DriveUploader:
    """DriveUploader 싱글톤 인스턴스 가져오기"""
    global _uploader_instance
    if _uploader_instance is None:
        _uploader_instance = DriveUploader()
        _uploader_instance.init_service()
    return _uploader_instance

