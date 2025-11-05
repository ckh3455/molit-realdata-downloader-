# -*- coding: utf-8 -*-
"""
Google Shared Drive 폴더 생성 테스트
- 부동산 실거래자료 폴더에 8개 섹션별 폴더 생성
"""
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==================== 설정 ====================
# 서비스 계정 파일 경로 (환경 변수 또는 직접 지정)
SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    r"C:\Users\Hello\OneDrive\office work\naver crawling\naver-crawling-476404-fcf4b10bc63e 클라우드 서비스계정.txt"
)

# Shared Drive ID
SHARED_DRIVE_ID = os.getenv("GOOGLE_SHARED_DRIVE_ID", "0APa-MWwUseXzUk9PVA")

# 부모 폴더명
PARENT_FOLDER_NAME = "부동산 실거래자료"

# 생성할 섹션별 폴더 목록 (8개)
SECTION_FOLDERS = [
    "아파트",
    "연립다세대",
    "단독다가구",
    "오피스텔",
    "토지",
    "상업업무용",
    "분양권",
    "입주권"
]

SCOPES = ['https://www.googleapis.com/auth/drive']


def init_drive_service():
    """Google Drive API 서비스 초기화"""
    try:
        if os.path.exists(SERVICE_ACCOUNT_FILE):
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
        else:
            service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            if not service_account_json:
                raise FileNotFoundError(
                    f"서비스 계정 파일을 찾을 수 없습니다: {SERVICE_ACCOUNT_FILE}\n"
                    "또는 GOOGLE_SERVICE_ACCOUNT_JSON 환경 변수를 설정하세요."
                )
            creds = service_account.Credentials.from_service_account_info(
                json.loads(service_account_json), scopes=SCOPES
            )
        
        service = build('drive', 'v3', credentials=creds)
        print("✅ Google Drive API 서비스 초기화 완료")
        return service
    except Exception as e:
        print(f"❌ Google Drive API 초기화 실패: {e}")
        raise


def find_folder_by_name(service, folder_name: str, parent_folder_id: str = None) -> str:
    """폴더 이름으로 폴더 ID 찾기"""
    try:
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_folder_id:
            query += f" and '{parent_folder_id}' in parents"
        
        params = {
            'q': query,
            'fields': 'files(id, name)',
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True,
            'driveId': SHARED_DRIVE_ID,
            'corpora': 'drive',
        }
        
        results = service.files().list(**params).execute()
        items = results.get('files', [])
        
        if items:
            folder_id = items[0]['id']
            print(f"  ✅ 폴더 찾음: {folder_name} (ID: {folder_id})")
            return folder_id
        return None
    except HttpError as e:
        print(f"  ❌ 폴더 검색 실패: {e}")
        return None


def create_folder(service, folder_name: str, parent_folder_id: str = None) -> str:
    """폴더 생성"""
    try:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
        }
        if parent_folder_id:
            file_metadata['parents'] = [parent_folder_id]
        
        folder = service.files().create(
            body=file_metadata,
            fields='id, name',
            supportsAllDrives=True,
            driveId=SHARED_DRIVE_ID,
        ).execute()
        folder_id = folder.get('id')
        print(f"  ✅ 폴더 생성 완료: {folder_name} (ID: {folder_id})")
        return folder_id
    except HttpError as e:
        print(f"  ❌ 폴더 생성 실패: {e}")
        return None


def get_or_create_folder(service, folder_name: str, parent_folder_id: str = None) -> str:
    """폴더 찾기 또는 생성"""
    folder_id = find_folder_by_name(service, folder_name, parent_folder_id)
    if folder_id:
        return folder_id
    print(f"  📁 폴더 생성 중: {folder_name}")
    return create_folder(service, folder_name, parent_folder_id)


def main():
    """메인 함수"""
    print("=" * 70)
    print("🚀 Google Shared Drive 폴더 생성 테스트")
    print("=" * 70)
    print(f"📂 Shared Drive ID: {SHARED_DRIVE_ID}")
    print(f"📁 부모 폴더: {PARENT_FOLDER_NAME}")
    print(f"📊 생성할 섹션: {len(SECTION_FOLDERS)}개\n")
    
    try:
        drive = init_drive_service()
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        return
    
    # 부모 폴더 찾기
    print(f"🔍 부모 폴더 찾기: {PARENT_FOLDER_NAME}")
    parent_folder_id = find_folder_by_name(drive, PARENT_FOLDER_NAME)
    
    if not parent_folder_id:
        print(f"❌ 부모 폴더를 찾을 수 없습니다: {PARENT_FOLDER_NAME}")
        print("   먼저 Google Drive에서 해당 폴더를 생성해주세요.")
        return
    
    print(f"✅ 부모 폴더 ID: {parent_folder_id}\n")
    
    # 각 섹션별 폴더 생성
    print("=" * 70)
    print("📁 섹션별 폴더 생성/확인")
    print("=" * 70)
    
    folder_results = {}
    for idx, section_name in enumerate(SECTION_FOLDERS, 1):
        print(f"\n[{idx}/{len(SECTION_FOLDERS)}] {section_name}")
        folder_id = get_or_create_folder(drive, section_name, parent_folder_id)
        if folder_id:
            folder_results[section_name] = folder_id
    
    # 결과 요약
    print("\n" + "=" * 70)
    print("📊 결과 요약")
    print("=" * 70)
    print(f"✅ 성공: {len(folder_results)}개")
    print(f"❌ 실패: {len(SECTION_FOLDERS) - len(folder_results)}개")
    
    if folder_results:
        print("\n생성/확인된 폴더:")
        for name, folder_id in folder_results.items():
            print(f"  - {name}: {folder_id}")
    
    print("\n" + "=" * 70)
    print("✅ 테스트 완료!")
    print("=" * 70)


if __name__ == "__main__":
    main()

