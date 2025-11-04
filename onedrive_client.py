"""
Microsoft Graph API를 사용한 OneDrive 클라이언트
"""
import os
import json
import requests
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging

try:
    from msal import ConfidentialClientApplication, PublicClientApplication
except ImportError:
    raise ImportError("msal 패키지가 필요합니다. pip install msal로 설치하세요.")

logger = logging.getLogger(__name__)


class OneDriveClient:
    """Microsoft Graph API를 사용한 OneDrive 클라이언트"""
    
    GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"
    
    def __init__(
        self,
        client_id: str,
        client_secret: Optional[str] = None,
        tenant_id: Optional[str] = None,
        authority: Optional[str] = None,
        use_device_code: bool = False
    ):
        """
        OneDrive 클라이언트 초기화
        
        Args:
            client_id: Azure AD 앱 등록의 클라이언트 ID
            client_secret: 클라이언트 시크릿 (서비스 주체 사용 시)
            tenant_id: 테넌트 ID (서비스 주체 사용 시)
            authority: 인증 기관 URL (기본값: https://login.microsoftonline.com/{tenant_id})
            use_device_code: Device Code Flow 사용 여부 (클라이언트 시크릿 없을 때)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id or "common"
        
        if authority:
            self.authority = authority
        else:
            self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        
        self.use_device_code = use_device_code or (client_secret is None)
        self.access_token: Optional[str] = None
        
        # 앱 초기화
        if self.client_secret:
            # 서비스 주체 방식 (Confidential Client)
            self.app = ConfidentialClientApplication(
                client_id=self.client_id,
                client_credential=self.client_secret,
                authority=self.authority
            )
        else:
            # Device Code Flow 방식 (Public Client)
            self.app = PublicClientApplication(
                client_id=self.client_id,
                authority=self.authority
            )
    
    def authenticate(self, scopes: Optional[List[str]] = None) -> bool:
        """
        인증 및 액세스 토큰 획득
        
        Args:
            scopes: 요청할 권한 범위 (기본값: Files.ReadWrite.All)
        
        Returns:
            인증 성공 여부
        """
        if scopes is None:
            scopes = ["Files.ReadWrite.All"]
        
        # 기존 토큰 확인
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(scopes, account=accounts[0])
            if result and "access_token" in result:
                self.access_token = result["access_token"]
                logger.info("기존 토큰으로 인증 성공")
                return True
        
        # 새 토큰 획득
        if self.client_secret:
            # 서비스 주체 방식
            result = self.app.acquire_token_for_client(scopes=scopes)
        else:
            # Device Code Flow
            flow = self.app.initiate_device_flow(scopes=scopes)
            if "user_code" in flow:
                print(f"디바이스 코드를 입력하세요: {flow['user_code']}")
                print(f"URL: {flow['verification_uri']}")
                result = self.app.acquire_token_by_device_flow(flow)
            else:
                logger.error(f"Device Code Flow 초기화 실패: {flow.get('error_description')}")
                return False
        
        if result and "access_token" in result:
            self.access_token = result["access_token"]
            logger.info("인증 성공")
            return True
        else:
            error = result.get("error", "Unknown error")
            error_description = result.get("error_description", "")
            logger.error(f"인증 실패: {error} - {error_description}")
            return False
    
    def _get_headers(self) -> Dict[str, str]:
        """API 요청 헤더 생성"""
        if not self.access_token:
            raise ValueError("액세스 토큰이 없습니다. authenticate()를 먼저 호출하세요.")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def _get_item_path(self, folder_path: str) -> str:
        """
        OneDrive 폴더 경로를 Graph API 경로로 변환
        
        Args:
            folder_path: 예) "office work/부동산 실거래 데이터/아파트"
        
        Returns:
            API 경로: "me/drive/root:/office work/부동산 실거래 데이터/아파트:"
        """
        # 경로 정규화
        path = folder_path.strip("/").replace("\\", "/")
        return f"me/drive/root:/{path}:"
    
    def list_files(self, folder_path: str = "root") -> List[Dict[str, Any]]:
        """
        폴더 내 파일 목록 조회
        
        Args:
            folder_path: 조회할 폴더 경로 (기본값: root)
        
        Returns:
            파일/폴더 정보 리스트
        """
        if folder_path == "root":
            api_path = "me/drive/root/children"
        else:
            item_path = self._get_item_path(folder_path)
            api_path = f"{item_path}/children"
        
        url = f"{self.GRAPH_API_ENDPOINT}/{api_path}"
        headers = self._get_headers()
        
        files = []
        while url:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            files.extend(data.get("value", []))
            
            # 다음 페이지 URL
            url = data.get("@odata.nextLink")
        
        return files
    
    def file_exists(self, file_path: str) -> bool:
        """
        파일 존재 여부 확인
        
        Args:
            file_path: 파일 경로 (예: "office work/부동산 실거래 데이터/아파트/파일명.xlsx")
        
        Returns:
            파일 존재 여부
        """
        try:
            item_path = self._get_item_path(file_path)
            url = f"{self.GRAPH_API_ENDPOINT}/{item_path}"
            headers = self._get_headers()
            
            response = requests.get(url, headers=headers)
            return response.status_code == 200
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return False
            raise
    
    def upload_file(
        self,
        local_file_path: str,
        remote_file_path: str,
        overwrite: bool = True
    ) -> bool:
        """
        파일 업로드
        
        Args:
            local_file_path: 로컬 파일 경로
            remote_file_path: OneDrive 파일 경로 (예: "office work/부동산 실거래 데이터/아파트/파일명.xlsx")
            overwrite: 기존 파일 덮어쓰기 여부
        
        Returns:
            업로드 성공 여부
        """
        if not os.path.exists(local_file_path):
            logger.error(f"로컬 파일이 없습니다: {local_file_path}")
            return False
        
        # 파일 크기 확인
        file_size = os.path.getsize(local_file_path)
        
        # 4MB 미만이면 단순 업로드
        if file_size < 4 * 1024 * 1024:
            return self._upload_file_simple(local_file_path, remote_file_path, overwrite)
        else:
            # 4MB 이상이면 세션 업로드
            return self._upload_file_session(local_file_path, remote_file_path, overwrite)
    
    def _upload_file_simple(
        self,
        local_file_path: str,
        remote_file_path: str,
        overwrite: bool
    ) -> bool:
        """단순 업로드 (4MB 미만)"""
        item_path = self._get_item_path(remote_file_path)
        url = f"{self.GRAPH_API_ENDPOINT}/{item_path}/content"
        
        headers = self._get_headers()
        headers.pop("Content-Type")  # 파일 업로드 시 Content-Type은 자동 설정
        
        with open(local_file_path, "rb") as f:
            file_content = f.read()
        
        if not overwrite:
            # 파일 존재 확인
            if self.file_exists(remote_file_path):
                logger.info(f"파일이 이미 존재하여 스킵: {remote_file_path}")
                return True
        
        response = requests.put(url, headers=headers, data=file_content)
        
        if response.status_code in (200, 201):
            logger.info(f"파일 업로드 성공: {remote_file_path}")
            return True
        else:
            logger.error(f"파일 업로드 실패: {response.status_code} - {response.text}")
            return False
    
    def _upload_file_session(
        self,
        local_file_path: str,
        remote_file_path: str,
        overwrite: bool
    ) -> bool:
        """세션 업로드 (4MB 이상)"""
        # 1. 업로드 세션 생성
        item_path = self._get_item_path(remote_file_path)
        create_session_url = f"{self.GRAPH_API_ENDPOINT}/{item_path}/createUploadSession"
        
        headers = self._get_headers()
        
        file_size = os.path.getsize(local_file_path)
        body = {
            "item": {
                "@microsoft.graph.conflictBehavior": "replace" if overwrite else "fail"
            }
        }
        
        response = requests.post(create_session_url, headers=headers, json=body)
        if response.status_code != 200:
            logger.error(f"업로드 세션 생성 실패: {response.status_code} - {response.text}")
            return False
        
        upload_url = response.json()["uploadUrl"]
        
        # 2. 청크 단위로 업로드
        chunk_size = 320 * 1024  # 320KB
        with open(local_file_path, "rb") as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                
                chunk_end = offset + len(chunk) - 1
                content_range = f"bytes {offset}-{chunk_end}/{file_size}"
                
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": content_range
                }
                
                response = requests.put(upload_url, headers=headers, data=chunk)
                
                if response.status_code not in (200, 201, 202):
                    logger.error(f"청크 업로드 실패: {response.status_code} - {response.text}")
                    return False
                
                offset += len(chunk)
        
        logger.info(f"파일 업로드 성공 (세션): {remote_file_path}")
        return True
    
    def create_folder(self, folder_path: str) -> bool:
        """
        폴더 생성
        
        Args:
            folder_path: 생성할 폴더 경로
        
        Returns:
            생성 성공 여부
        """
        # 부모 폴더와 폴더명 분리
        parts = folder_path.strip("/").split("/")
        if len(parts) == 1:
            parent_path = "root"
            folder_name = parts[0]
        else:
            parent_path = "/".join(parts[:-1])
            folder_name = parts[-1]
        
        parent_item_path = self._get_item_path(parent_path) if parent_path != "root" else "me/drive/root"
        url = f"{self.GRAPH_API_ENDPOINT}/{parent_item_path}/children"
        
        headers = self._get_headers()
        body = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "rename"
        }
        
        response = requests.post(url, headers=headers, json=body)
        
        if response.status_code in (200, 201):
            logger.info(f"폴더 생성 성공: {folder_path}")
            return True
        else:
            # 이미 존재하는 경우는 성공으로 간주
            if response.status_code == 409:
                logger.info(f"폴더가 이미 존재함: {folder_path}")
                return True
            logger.error(f"폴더 생성 실패: {response.status_code} - {response.text}")
            return False
