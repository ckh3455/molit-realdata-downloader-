# -*- coding: utf-8 -*-
"""
Google Shared Drive 파일 목록 확인
- 공유드라이브에 있는 파일 목록 조회
- 폴더 구조 확인
"""
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==================== 설정 ====================
# 서비스 계정 정보
# 이메일: naver-crawling-476404@appspot.gserviceaccount.com
# 프로젝트 ID: naver-crawling-476404

# 서비스 계정 파일 경로 (환경 변수 또는 직접 지정)
SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    r"D:\OneDrive\office work\naver crawling\naver-crawling-476404-fcf4b10bc63e 클라우드 서비스계정.txt"
)

# "부동산자료" 폴더 ID (GDRIVE_FOLDER_ID)
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "0APa-MWwUseXzUk9PVA")

# Shared Drive ID (폴더 정보에서 자동으로 가져옴)
SHARED_DRIVE_ID = os.getenv("GOOGLE_SHARED_DRIVE_ID", None)

# 부모 폴더 경로 (GDRIVE_FOLDER_ID가 "부동산자료"이므로 하위 폴더만)
PARENT_FOLDER_PATH = ["부동산 실거래자료"]

SCOPES = ['https://www.googleapis.com/auth/drive']


def init_drive_service():
    """Google Drive API 서비스 초기화"""
    try:
        # 환경 변수 우선 확인 (GitHub Actions용)
        service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        
        if service_account_json:
            creds = service_account.Credentials.from_service_account_info(
                json.loads(service_account_json), scopes=SCOPES
            )
        elif os.path.exists(SERVICE_ACCOUNT_FILE):
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
        else:
            raise FileNotFoundError(
                f"서비스 계정 파일을 찾을 수 없습니다: {SERVICE_ACCOUNT_FILE}\n"
                "또는 GOOGLE_SERVICE_ACCOUNT_JSON 환경 변수를 설정하세요."
            )
        
        service = build('drive', 'v3', credentials=creds)
        print("[OK] Google Drive API 서비스 초기화 완료")
        return service
    except Exception as e:
        print(f"[ERROR] Google Drive API 초기화 실패: {e}")
        raise


def list_drives(service):
    """Shared Drive 목록 조회"""
    try:
        print("\n" + "=" * 70)
        print("Shared Drive 목록 조회")
        print("=" * 70)
        
        results = service.drives().list(pageSize=10).execute()
        drives = results.get('drives', [])
        
        if not drives:
            print("  [WARNING] Shared Drive를 찾을 수 없습니다.")
            return None
        
        print(f"  [OK] 발견된 Shared Drive: {len(drives)}개\n")
        for drive in drives:
            print(f"  - 이름: {drive.get('name')}")
            print(f"    ID: {drive.get('id')}")
            print()
        
        return drives
    except HttpError as e:
        print(f"  [ERROR] Shared Drive 목록 조회 실패: {e}")
        return None


def find_folder_by_name(service, folder_name: str, parent_folder_id: str = None):
    """폴더 이름으로 폴더 ID 찾기"""
    try:
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_folder_id:
            query += f" and '{parent_folder_id}' in parents"
        
        params = {
            'q': query,
            'fields': 'files(id, name, parents, driveId)',
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True,
        }
        
        # files().list()에서 parent_folder_id 조건이 있으면
        # 해당 폴더 내에서만 검색하므로 driveId 불필요
        # supportsAllDrives와 includeItemsFromAllDrives만으로 충분
        
        results = service.files().list(**params).execute()
        items = results.get('files', [])
        
        if items:
            return items[0]
        return None
    except HttpError as e:
        print(f"  ❌ 폴더 검색 실패: {e}")
        return None


def list_files_in_folder(service, folder_id: str, folder_name: str = "", max_results: int = 100):
    """폴더 내 파일 목록 조회"""
    try:
        query = f"'{folder_id}' in parents and trashed=false"
        
        params = {
            'q': query,
            'fields': 'files(id, name, mimeType, size, modifiedTime, driveId)',
            'pageSize': max_results,
            'orderBy': 'name',
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True,
        }
        
        # files().list()에서 특정 폴더 내 검색 시
        # supportsAllDrives와 includeItemsFromAllDrives만으로 충분
        # driveId 파라미터는 사용하지 않음
        
        results = service.files().list(**params).execute()
        items = results.get('files', [])
        
        return items
    except HttpError as e:
        print(f"  [ERROR] 파일 목록 조회 실패: {e}")
        return []


def format_size(size_bytes):
    """파일 크기 포맷팅"""
    if not size_bytes:
        return "N/A"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def main():
    """메인 함수"""
    import sys
    import io
    # UTF-8 인코딩 설정
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    print("=" * 70)
    print("Google Shared Drive 파일 목록 확인")
    print("=" * 70)
    print(f"부동산자료 폴더 ID: {GDRIVE_FOLDER_ID}")
    print()
    
    try:
        drive = init_drive_service()
    except Exception as e:
        print(f"[ERROR] 초기화 실패: {e}")
        return
    
    # 1. "부동산자료" 폴더 확인
    print("=" * 70)
    print("부동산자료 폴더 확인")
    print("=" * 70)
    
    try:
        folder_info = drive.files().get(
            fileId=GDRIVE_FOLDER_ID,
            fields='id, name, driveId',
            supportsAllDrives=True
        ).execute()
        
        global SHARED_DRIVE_ID
        if folder_info.get('driveId'):
            SHARED_DRIVE_ID = folder_info.get('driveId')
        
        print(f"  [OK] 부동산자료 폴더 확인: {folder_info.get('name')} (ID: {GDRIVE_FOLDER_ID})")
        if SHARED_DRIVE_ID:
            print(f"  [INFO] Shared Drive ID: {SHARED_DRIVE_ID}")
    except Exception as e:
        print(f"  [ERROR] 부동산자료 폴더 접근 실패: {e}")
        return
    
    # 2. "부동산 실거래자료" 폴더 찾기
    print("\n" + "=" * 70)
    print("부동산 실거래자료 폴더 찾기")
    print("=" * 70)
    
    folder = find_folder_by_name(drive, PARENT_FOLDER_PATH[0], GDRIVE_FOLDER_ID)
    
    if not folder:
        print(f"  [ERROR] '{PARENT_FOLDER_PATH[0]}' 폴더를 찾을 수 없습니다.")
        return
    
    final_folder_id = folder.get('id')
    final_folder_name = PARENT_FOLDER_PATH[0]
    print(f"  [OK] 찾음: {final_folder_name} (ID: {final_folder_id})")
    
    # 3. 최종 폴더 내 파일 목록 조회
    print("\n" + "=" * 70)
    print(f"'{final_folder_name}' 폴더 내 파일 목록")
    print("=" * 70)
    
    all_files = []
    all_folders = []
    
    items = list_files_in_folder(drive, final_folder_id, final_folder_name, max_results=1000)
    
    for item in items:
        if item.get('mimeType') == 'application/vnd.google-apps.folder':
            all_folders.append(item)
        else:
            all_files.append(item)
    
    print(f"\n[통계]")
    print(f"  - 폴더: {len(all_folders)}개")
    print(f"  - 파일: {len(all_files)}개")
    print()
    
    # 폴더 목록
    if all_folders:
        print("[폴더 목록]")
        for folder in sorted(all_folders, key=lambda x: x.get('name', '')):
            print(f"  - {folder.get('name')} (ID: {folder.get('id')})")
        print()
        
        # 각 폴더 내부 파일 확인
        print("=" * 70)
        print("각 섹션별 폴더 내 파일 확인")
        print("=" * 70)
        for folder in sorted(all_folders, key=lambda x: x.get('name', '')):
            folder_name = folder.get('name')
            folder_id = folder.get('id')
            print(f"\n[{folder_name}] 폴더 내 파일:")
            folder_items = list_files_in_folder(drive, folder_id, folder_name, max_results=1000)
            if folder_items:
                # 파일명으로 정렬
                sorted_items = sorted(folder_items, key=lambda x: x.get('name', ''))
                print(f"  총 {len(sorted_items)}개 파일")
                # 처음 10개만 출력
                for item in sorted_items[:10]:
                    name = item.get('name')
                    size = format_size(int(item.get('size', 0)) if item.get('size') else 0)
                    print(f"    - {name} ({size})")
                if len(sorted_items) > 10:
                    print(f"    ... 외 {len(sorted_items) - 10}개")
            else:
                print("  (파일 없음)")
    
    # 파일 목록 (섹션별로 그룹화)
    if all_files:
        print("[파일 목록]")
        
        # 파일명으로 그룹화 (섹션별)
        sections = {}
        for file in all_files:
            name = file.get('name', '')
            # 파일명에서 섹션 추출 (예: "아파트 200601.xlsx" -> "아파트")
            if ' ' in name:
                section = name.split(' ')[0]
                if section not in sections:
                    sections[section] = []
                sections[section].append(file)
            else:
                if '기타' not in sections:
                    sections['기타'] = []
                sections['기타'].append(file)
        
        # 섹션별 출력
        for section in sorted(sections.keys()):
            files = sections[section]
            print(f"\n  [{section}] - {len(files)}개 파일")
            for file in sorted(files, key=lambda x: x.get('name', '')):
                name = file.get('name')
                size = format_size(int(file.get('size', 0)) if file.get('size') else 0)
                modified = file.get('modifiedTime', 'N/A')[:10] if file.get('modifiedTime') else 'N/A'
                print(f"    - {name} ({size}, 수정: {modified})")
    
    print("\n" + "=" * 70)
    print("[OK] 확인 완료!")
    print("=" * 70)


if __name__ == "__main__":
    main()

