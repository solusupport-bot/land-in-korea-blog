#!/usr/bin/env python3
"""
desktop-tutorial의 lib/publishing/github_raw_host.js와 동일한 방식 —
GitHub Release 자산은 objects.githubusercontent.com 리다이렉트를 거쳐서
가끔 비-브라우저 요청을 거부하는 게 확인됐다(2026-08-29 실측). 대신 이
저장소 자체의 media-assets 브랜치(소스 코드와 무관, 최초 1회 생성해둠)에
Contents API로 직접 커밋하고 raw.githubusercontent.com URL로 서빙한다.
"""
import base64
import json
import os
import urllib.request

GITHUB_API = "https://api.github.com"
MEDIA_BRANCH = "media-assets"


def upload_media_file(github_token, buffer, relative_path):
    """buffer(bytes)를 media-assets 브랜치에 relative_path로 커밋하고
    raw.githubusercontent.com URL을 반환한다."""
    repo_full_name = os.environ.get("GITHUB_REPOSITORY", "solusupport-bot/land-in-korea-blog")
    owner, repo = repo_full_name.split("/", 1)

    body = json.dumps({
        "message": f"chore: host media asset {relative_path}",
        "content": base64.b64encode(buffer).decode("ascii"),
        "branch": MEDIA_BRANCH,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{relative_path}",
        data=body,
        method="PUT",
        headers={
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        res.read()

    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{MEDIA_BRANCH}/{relative_path}"
    print(f"  미디어를 raw.githubusercontent.com으로 호스팅: {raw_url}")
    return raw_url
