# -*- coding: utf-8 -*-
"""
Microsoft Graph API를 사용한 OneDrive 클라이언트
"""
import os
import json
import time
from pathlib import Path
from typing import Optional, List, Set
from msal import ConfidentialClientApplication
import requests


class OneDriveClient:
    """Microsoft Graph API를 사용한 OneDrive 클라이언트"""
    
    def __init__(self, client_id: str, client_secret: str, tenant_id: str):
        """
        Args:
            client_id: Azure AD 앱 등록의 Client ID
            client_secret: Azure AD 앱 등록의 Client Secret
            tenant_id: Azure AD Tenant ID
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]
        self.graph_endpoint = "https://graph.microsoft.com/v1.0"
        
        # MSAL 앱 생성
        self.app = ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority
        )
        
        self._access_token = None
        self._token_expiry = 0
    
    def _get_access_token(self) -> str:
        """액세스 토큰 가져오기 (필요시 갱신)"""
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token
        
        # 토큰 획득
        result = self.app.acquire_token_for_client(scopes=self.scope)
        
        if "access_token" not in result:
            error = result.get("error_description", "알 수 없는 오류")
            raise Exception(f"토큰 획득 실패: {error}")
        
        self._access_token = result["access_token"]
        # 만료 시간 5분 전에 갱신하도록 설정
        expires_in = result.get("expires_in", 3600)
        self._token_expiry = time.time() + expires_in - 300
        
        return self._access_token
    
    def _get_headers(self) -> dict:
        """API 요청 헤더"""
        token = self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def _get_item_path(self, folder_path: str) -> str:
        """OneDrive 경로를 Graph API 경로로 변환"""
        # 경로 정규화
        folder_path = folder_path.strip("/")
        if not folder_path:
            return "/drive/root"
        
        # 한글 경로 인코딩
        parts = folder_path.split("/")
        encoded_parts = []
        for part in parts:
            if part:
                encoded_parts.append(part)
        
        if not encoded_parts:
            return "/drive/root"
        
        # 경로 조합
        path = "/" + "/".join(encoded_parts)
        return f"/drive/root:{path}"
    
    def upload_file(self, local_path: Path, remote_path: str) -> bool:
        """
        파일 업로드
        
        Args:
            local_path: 로컬 파일 경로
            remote_path: OneDrive 상대 경로 (예: "office work/부동산 실거래 데이터/아파트/파일명.xlsx")
        
        Returns:
            성공 여부
        """
        try:
            if not local_path.exists():
                print(f"  ❌ 파일 없음: {local_path}")
                return False
            
            # 파일 크기 확인
            file_size = local_path.stat().st_size
            if file_size == 0:
                print(f"  ❌ 빈 파일: {local_path}")
                return False
            
            # 경로 분리
            path_parts = remote_path.replace("\\", "/").strip("/").split("/")
            filename = path_parts[-1]
            folder_path = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
            
            # 폴더 경로 생성 (없으면)
            if folder_path:
                self._ensure_folder_exists(folder_path)
                item_path = self._get_item_path(folder_path)
            else:
                item_path = "/drive/root"
            
            # 업로드 URL 가져오기
            upload_url = f"{self.graph_endpoint}{item_path}:/{filename}:/createUploadSession"
            
            headers = self._get_headers()
            
            # 업로드 세션 생성
            session_response = requests.post(
                upload_url,
                headers=headers,
                json={
                    "item": {
                        "@microsoft.graph.conflictBehavior": "replace",
                        "name": filename
                    }
                }
            )
            
            if session_response.status_code not in [200, 201]:
                print(f"  ❌ 업로드 세션 생성 실패: {session_response.status_code}")
                print(f"     {session_response.text[:200]}")
                return False
            
            upload_url_value = session_response.json()["uploadUrl"]
            
            # 파일 업로드 (5MB 이하면 한 번에)
            if file_size <= 5 * 1024 * 1024:
                # 작은 파일은 한 번에 업로드
                with open(local_path, "rb") as f:
                    upload_response = requests.put(
                        upload_url_value,
                        headers={"Content-Length": str(file_size)},
                        data=f.read()
                    )
                
                if upload_response.status_code in [200, 201]:
                    print(f"  ✅ 업로드 완료: {remote_path}")
                    return True
                else:
                    print(f"  ❌ 업로드 실패: {upload_response.status_code}")
                    return False
            else:
                # 큰 파일은 청크 단위로 업로드
                chunk_size = 4 * 1024 * 1024  # 4MB
                with open(local_path, "rb") as f:
                    offset = 0
                    while offset < file_size:
                        chunk_end = min(offset + chunk_size - 1, file_size - 1)
                        chunk_data = f.read(chunk_size)
                        
                        chunk_headers = {
                            "Content-Length": str(len(chunk_data)),
                            "Content-Range": f"bytes {offset}-{chunk_end}/{file_size}"
                        }
                        
                        chunk_response = requests.put(
                            upload_url_value,
                            headers=chunk_headers,
                            data=chunk_data
                        )
                        
                        if chunk_response.status_code not in [200, 201, 202]:
                            print(f"  ❌ 청크 업로드 실패: {chunk_response.status_code}")
                            return False
                        
                        offset = chunk_end + 1
                
                print(f"  ✅ 업로드 완료: {remote_path}")
                return True
                
        except Exception as e:
            print(f"  ❌ 업로드 오류: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _ensure_folder_exists(self, folder_path: str) -> bool:
        """폴더가 존재하는지 확인하고 없으면 생성"""
        try:
            parts = folder_path.replace("\\", "/").strip("/").split("/")
            current_path = ""
            
            for part in parts:
                if not part:
                    continue
                
                if current_path:
                    current_path = f"{current_path}/{part}"
                else:
                    current_path = part
                
                item_path = self._get_item_path(current_path)
                check_url = f"{self.graph_endpoint}{item_path}"
                
                headers = self._get_headers()
                check_response = requests.get(check_url, headers=headers)
                
                if check_response.status_code == 404:
                    # 폴더 없음 - 생성
                    parent_path = current_path.rsplit("/", 1)[0] if "/" in current_path else ""
                    if parent_path:
                        parent_item_path = self._get_item_path(parent_path)
                    else:
                        parent_item_path = "/drive/root"
                    
                    create_url = f"{self.graph_endpoint}{parent_item_path}:/children"
                    
                    create_response = requests.post(
                        create_url,
                        headers=headers,
                        json={
                            "name": part,
                            "folder": {},
                            "@microsoft.graph.conflictBehavior": "rename"
                        }
                    )
                    
                    if create_response.status_code not in [200, 201]:
                        print(f"  ⚠️  폴더 생성 실패: {part} ({create_response.status_code})")
                        # 계속 진행 (이미 존재할 수 있음)
                
        except Exception as e:
            print(f"  ⚠️  폴더 확인/생성 오류: {e}")
            # 계속 진행
        
        return True
    
    def list_files(self, folder_path: str) -> Set[str]:
        """
        폴더 내 파일 목록 가져오기
        
        Args:
            folder_path: OneDrive 상대 경로
        
        Returns:
            파일명 집합
        """
        try:
            item_path = self._get_item_path(folder_path)
            list_url = f"{self.graph_endpoint}{item_path}:/children"
            
            headers = self._get_headers()
            files = set()
            
            while list_url:
                response = requests.get(list_url, headers=headers)
                
                if response.status_code != 200:
                    print(f"  ⚠️  파일 목록 조회 실패: {response.status_code}")
                    break
                
                data = response.json()
                items = data.get("value", [])
                
                for item in items:
                    if "folder" not in item:  # 파일만
                        files.add(item["name"])
                
                # 다음 페이지
                list_url = data.get("@odata.nextLink")
                if list_url:
                    # 전체 URL이 아닌 경우 Graph 엔드포인트 추가
                    if not list_url.startswith("http"):
                        list_url = f"{self.graph_endpoint}{list_url}"
            
            return files
            
        except Exception as e:
            print(f"  ⚠️  파일 목록 조회 오류: {e}")
            return set()
    
    def file_exists(self, file_path: str) -> bool:
        """
        파일 존재 여부 확인
        
        Args:
            file_path: OneDrive 상대 경로 (예: "office work/부동산 실거래 데이터/아파트/파일명.xlsx")
        
        Returns:
            존재 여부
        """
        try:
            path_parts = file_path.replace("\\", "/").strip("/").split("/")
            filename = path_parts[-1]
            folder_path = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
            
            files = self.list_files(folder_path)
            return filename in files
            
        except Exception as e:
            print(f"  ⚠️  파일 존재 확인 오류: {e}")
            return False
    
    def download_file(self, remote_path: str, local_path: Path) -> bool:
        """
        파일 다운로드
        
        Args:
            remote_path: OneDrive 상대 경로
            local_path: 로컬 저장 경로
        
        Returns:
            성공 여부
        """
        try:
            path_parts = remote_path.replace("\\", "/").strip("/").split("/")
            filename = path_parts[-1]
            folder_path = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
            
            item_path = self._get_item_path(remote_path)
            download_url = f"{self.graph_endpoint}{item_path}:/content"
            
            headers = self._get_headers()
            response = requests.get(download_url, headers=headers, stream=True)
            
            if response.status_code == 200:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                with open(local_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
            else:
                print(f"  ❌ 다운로드 실패: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"  ❌ 다운로드 오류: {e}")
            return False
