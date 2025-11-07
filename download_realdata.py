def upload_processed(file_path: Path, prop_kind: str):
    """전처리된 파일을 기존 공유드라이브 경로로 덮어쓰기 업로드.
    - 폴더는 새로 만들지 않음(경로 없으면 스킵하고 로그 남김).
    경로 규칙: DRIVE_ROOT_ID /(GDRIVE_BASE_PATH 또는 자동탐지)/ <종목폴더>
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

    # ① 베이스 경로 자동 결정
    base_parent_id = detect_base_parent_id(svc)
    if not base_parent_id:
        log(f"  - drive: skip (base path not found): {GDRIVE_BASE_PATH}")
        return

    # ② 종목 폴더 찾기 (새로 만들지 않음)
    subfolder = FOLDER_MAP.get(prop_kind, prop_kind)
    folder_id = find_child_folder_id(svc, base_parent_id, subfolder)
    if not folder_id:
        log(f"  - drive: skip (category folder missing): {GDRIVE_BASE_PATH or '자동탐지 베이스'}/{subfolder}")
        return

    name = file_path.name
    media = MediaFileUpload(
        file_path.as_posix(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

    # 현재 베이스 경로/루트의 '이름'을 로깅에 포함 (가독성 향상)
    try:
        root_meta = svc.files().get(fileId=DRIVE_ROOT_ID, fields='id,name').execute()
        base_meta = svc.files().get(fileId=base_parent_id, fields='id,name,parents').execute()
        base_name = base_meta.get('name','')
        root_name = root_meta.get('name','')
    except Exception:
        base_name = GDRIVE_BASE_PATH or ''
        root_name = ''

    q = f"name='{name}' and '{folder_id}' in parents and trashed=false"
    resp = svc.files().list(
        q=q, spaces='drive', fields='files(id,name)',
        supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    files = resp.get('files', [])

    # 풀 경로 형태 로그: [루트]/[베이스]/[종목]/파일명
    path_parts = [p for p in [root_name, base_name, subfolder, name] if p]
    full_path_for_log = "/".join(path_parts) if path_parts else f"{subfolder}/{name}"
    log(f"  - drive target: {full_path_for_log} (https://drive.google.com/drive/folders/{folder_id})")

    if files:
        fid = files[0]['id']
        svc.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
        log(f"  - drive: overwritten (update) -> {full_path_for_log}")
    else:
        meta = {'name': name, 'parents': [folder_id]}
        svc.files().create(body=meta, media_body=media, fields='id', supportsAllDrives=True).execute()
        log(f"  - drive: uploaded (create) -> {full_path_for_log}")
