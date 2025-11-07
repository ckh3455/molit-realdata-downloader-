# --- runtime dep bootstrap: install pandas/numpy/openpyxl etc. if missing ---
import sys, subprocess
try:
    import pandas  # noqa: F401
    import numpy   # noqa: F401
    import openpyxl  # noqa: F401
except ModuleNotFoundError:
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--upgrade",
        "pandas", "numpy", "openpyxl",
        "google-api-python-client", "google-auth", "google-auth-httplib2", "google-auth-oauthlib",
        "python-dateutil", "pytz", "tzdata", "et-xmlfile"
    ])
# ---------------------------------------------------------------------------
# 공유드라이브 업로드 개선 — 전처리된 파일을 종목별 폴더에 덮어쓰기
# - 각 종목(아파트, 단독다가구 등)은 동일 이름의 하위 폴더로 분류됨
# - 전처리 후 파일은 해당 폴더에 동일 이름으로 덮어쓰기됨

from pathlib import Path
import pandas as pd
import numpy as np
import json, os, base64
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials

def log(msg):
    print(msg, flush=True)

# 폴더 매핑 (공유드라이브 내부 구조)
FOLDER_MAP = {
    '아파트': '아파트',
    '단독다가구': '단독다가구',
    '연립다세대': '연립다세대',
    '오피스텔': '오피스텔',
    '상업업무용': '상업업무용',
    '토지': '토지',
    '공장창고등': '공장창고등'
}

DRIVE_ROOT_ID = os.getenv('GDRIVE_FOLDER_ID', '').strip()

def load_sa():
    raw = os.getenv('GCP_SERVICE_ACCOUNT_KEY', '').strip()
    if not raw:
        raise RuntimeError('Service account key missing')
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(base64.b64decode(raw).decode('utf-8'))
    return Credentials.from_service_account_info(data, scopes=['https://www.googleapis.com/auth/drive'])

def ensure_subfolder(svc, parent_id: str, name: str):
    q = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    resp = svc.files().list(q=q, spaces='drive', fields='files(id,name)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = resp.get('files', [])
    if files:
        return files[0]['id']
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
    f = svc.files().create(body=meta, fields='id', supportsAllDrives=True).execute()
    return f['id']

def upload_processed(file_path: Path, prop_kind: str):
    """전처리된 파일을 종목별 하위 폴더에 덮어쓰기 업로드.
    파일이 없거나 루트 폴더 ID/SA가 없으면 스킵하고 로그만 남김.
    """
    if not file_path.exists():
        log(f"  - drive: skip (file not found): {file_path}")
        return
    if not DRIVE_ROOT_ID:
        log("  - drive: skip (missing DRIVE_ROOT_ID/GDRIVE_FOLDER_ID)")
        return
    try:
        creds = load_sa()
    except Exception as e:
        log(f"  - drive: skip (SA load error): {e}")
        return

    svc = build('drive', 'v3', credentials=creds, cache_discovery=False)
    subfolder = FOLDER_MAP.get(prop_kind, prop_kind)
    folder_id = ensure_subfolder(svc, DRIVE_ROOT_ID, subfolder)

    name = file_path.name
    media = MediaFileUpload(
        file_path.as_posix(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

    q = f"name='{name}' and '{folder_id}' in parents and trashed=false"
    resp = svc.files().list(
        q=q, spaces='drive', fields='files(id,name)',
        supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    files = resp.get('files', [])

    log(f"  - drive target: {subfolder}/{name} (https://drive.google.com/drive/folders/{folder_id})")

    if files:
        fid = files[0]['id']
        svc.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
        log(f"  - drive: overwritten (update) → {subfolder}/{name}")
    else:
        meta = {'name': name, 'parents': [folder_id]}
        svc.files().create(body=meta, media_body=media, fields='id', supportsAllDrives=True).execute()
        log(f"  - drive: uploaded (create) → {subfolder}/{name}")

