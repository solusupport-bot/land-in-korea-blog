#!/usr/bin/env python3
"""
Cloudflare 배포용 폴더를 준비한다: site/의 빌드 결과를 대상 폴더로 복사하고,
media-assets 브랜치(raw.githubusercontent.com 절대 URL)에 있는 워터마크 이미지를
로컬 media/images/로 받아와 HTML의 이미지 경로를 상대 경로로 바꾼다.
(2026-09-27: Netlify 크레딧 소진 + GitHub Actions 계정 정지로 Cloudflare로 이전하며
 이 변환이 필요해짐 — reference_cloudflare_deploy 메모리 참고)

Usage:
  python3 localize_media.py <target_dir>
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAW_PREFIX = "https://raw.githubusercontent.com/solusupport-bot/land-in-korea-blog/media-assets/images/"
LOCAL_PREFIX = "/media/images/"


def main(target_dir: str):
    repo_root = Path(__file__).resolve().parent.parent
    site = repo_root / "site"
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    for item in site.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    print(f"copied site -> {target}")

    media_dir = target / "media" / "images"
    media_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", tmp, "origin/media-assets"],
            cwd=repo_root, check=True,
        )
        try:
            for f in (Path(tmp) / "images").iterdir():
                shutil.copy2(f, media_dir / f.name)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", tmp], cwd=repo_root, check=True)
    print(f"exported media-assets images -> {media_dir}")

    rewritten_files = 0
    rewritten_refs = 0
    for html in list((target / "posts").glob("*.html")) + [target / "index.html"]:
        text = html.read_text(encoding="utf-8")
        n = text.count(RAW_PREFIX)
        if n:
            html.write_text(text.replace(RAW_PREFIX, LOCAL_PREFIX), encoding="utf-8")
            rewritten_files += 1
            rewritten_refs += n
    print(f"rewrote {rewritten_refs} image refs across {rewritten_files} files")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: localize_media.py <target_dir>")
    main(sys.argv[1])
