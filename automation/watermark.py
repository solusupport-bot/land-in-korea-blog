#!/usr/bin/env python3
"""
Meta가 2026년 5월부터 사진에도 "재사용 콘텐츠" 단속을 확대했다 — 워터마크는
Meta가 정의하는 "그래픽 추가"(실질적 편집)에 해당한다. desktop-tutorial의
SNS 파이프라인(lib/media/watermark.js)과 완전히 동일한 로직 — 로고를 이미지
너비의 16%로 리사이즈해 우측 하단(여백 2%)에 합성한다. 블로그 글 대표
이미지가 SNS(특히 Facebook) 링크 공유 시 미리보기 썸네일로도 쓰이기 때문에,
SNS 캡션과 동일한 워터마크 기준을 여기도 적용한다(2026-08-30 사용자 요청).

assets/logo-mark.png는 desktop-tutorial/assets/logo-mark.png와 동일한 파일
(사용자가 직접 만든 실제 "Land in Korea" 브랜드 로고)을 그대로 복사한 것.
"""
import io
import os
import urllib.request

from PIL import Image

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo-mark.png")
LOGO_ASPECT_RATIO = 530 / 700  # assets/logo-mark.png 실제 비율(가로 700 x 세로 530)


def apply_watermark(image_url):
    """이미지 URL을 다운로드해 우측 하단에 로고를 합성한 JPEG 바이트를 반환한다.
    실패하면 None을 반환한다(호출부에서 원본 URL 그대로 쓰는 폴백 처리)."""
    try:
        req = urllib.request.Request(
            image_url, headers={"User-Agent": "Mozilla/5.0 (compatible; LandInKoreaBlogBot/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=20) as res:
            base = Image.open(io.BytesIO(res.read())).convert("RGB")

        width, height = base.size
        logo_width = round(width * 0.16)
        logo_height = round(logo_width * LOGO_ASPECT_RATIO)
        margin = round(width * 0.02)

        logo = Image.open(LOGO_PATH).convert("RGBA").resize((logo_width, logo_height), Image.LANCZOS)
        base.paste(logo, (width - logo_width - margin, height - logo_height - margin), logo)

        buf = io.BytesIO()
        base.save(buf, format="JPEG", quality=90)
        print(f"  워터마크 합성 완료 ({width}x{height})")
        return buf.getvalue()
    except Exception as err:
        print(f"  !! 워터마크 합성 실패: {err}")
        return None
