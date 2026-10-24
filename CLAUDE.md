# CLAUDE.md — land-in-korea-blog 작업 규칙

이 저장소에서 작업할 때 자동으로 적용되는 규칙입니다. 자동 발행/배포 쪽 운영 규칙은
`desktop-tutorial/CLAUDE.md`에 있으며, 이 파일은 그 중 **이 저장소 안에서** 지켜야 할
부분을 동일하게 기록합니다.

---

## 🏠 승인된 홈페이지 = "신버전 지역 아틀라스" (2026-10-24 사용자 최종 확정)

**main 브랜치의 홈페이지는 아래 기준으로 고정합니다. 다른 디자인으로 되돌리지 마세요.**

- 첫 화면: 부산 광안대교 바다 사진 히어로 (`automation/homepage/assets/busan.jpg`)
- `/home-ui/` 구조 (`automation/homepage.py` + `automation/homepage/` 소스 →
  `site/home-ui/`로 빌드됨)
- 전국 지역 지도 → 구·동 세부지도 → 관련 블로그 글 연결 (`district-data-seed.json`)
- EN/JA/ZH 언어 전환 (`automation/homepage/app.js`의 `copy.en/ja/zh`, `localStorage`에
  `lik-language`로 저장)
- 모바일 최신 글에 울릉도·성산일출봉·독도 실제 사진
- 진주 지도 아래 실제 진주·남강 사진

**생성 방식 (중요 — 동결하지 말 것):** `automation/build.py`의 `build()`가
`from homepage import write_homepage`로 `automation/homepage.py`의 `write_homepage()`를
호출해서 매 빌드마다 `site/index.html` + `site/home-ui/`를 **새로 생성**합니다. 글이
추가되면 `write_homepage(cfg, base, posts, ...)`에 전달되는 `posts`가 자동으로 반영되어
글 목록/카테고리/지역 연결만 갱신되고, 디자인(`styles.css`, `app.js`,
`district-data-seed.json` 등 템플릿/자산)은 그대로입니다 — **생성된 `site/index.html`
파일 자체를 손으로 고정/동결하는 방식은 쓰지 마세요.** 디자인을 바꾸려면
`automation/homepage/` 소스를 고치고, 글 데이터만 바뀌는 건 정상 동작입니다.

**이전 경위(참고, 실수였음):** 2026-10-24 오전에 main을 이전 커밋(`a6c5982`)으로 되돌리는
과정에서 `automation/homepage.py`/`automation/homepage/`가 main에서 잠깐 빠져, 그 결과
build.py가 구버전 `discovery_home()`(Nami Island 히어로, 인라인 Korea map SVG)으로
되돌아간 상태였다. 삭제된 게 아니라 `future-work` 브랜치(커밋 `ffd7e26`)에 그대로
보존돼 있었고, 최신 수동 백업/zip과 바이트 단위로 동일함을 확인한 뒤 홈페이지 관련
파일만(다른 진행 중 작업 — 리뷰 사진, candidate_photos, 새 블로그 글 초안 — 은 제외)
`homepage-approved` 브랜치로 분리해 main에 반영함. **구버전 `discovery_home()` 결과가
"승인본"이라고 오인하지 말 것** — 그건 일시적 사고 상태였을 뿐 승인된 디자인이 아니었다.

**자동배포 연결:** 이 저장소는 로컬 작업 스케줄러 작업
`DesktopTutorial-BlogDeploy`(`desktop-tutorial/ops/local-cron/deploy-blog-cloudflare.ps1`)가
main을 pull → `automation/build.py` 실행 → `automation/localize_media.py`로 바탕화면
`landinkorea_site`(Cloudflare Worker `land-in-korea`가 서빙)에 복사 → `wrangler deploy`
하는 경로로만 실제 `landinkorea.com`을 바꿉니다. GitHub Actions(`.github/workflows/
blog-build.yml`)는 GitHub Pages로만 배포되고 운영 도메인과 무관합니다.

**배포 안전장치 (`deploy-blog-cloudflare.ps1`에 구현됨, 완화/건너뛰기 금지):**
1. 빌드 전: 승인된 홈페이지 필수 소스 10개 파일이 전부 있는지, `build.py`가 반드시
   `from homepage import write_homepage`를 쓰는지 **무조건** 검사(조건부 아님).
2. 빌드 후: `site/index.html`이 실제로 `home-ui/app.js`를 참조하는지 검사.
3. `localize_media.py` 복사 후: 배포 폴더에 home-ui의 JS/CSS/사진/폰트가 전부 있는지 검사.
4. 하나라도 실패하면 빌드/배포 전체를 중단.

홈페이지 관련 작업으로 이 저장소의 브랜치를 전환/reset/병합할 때는 **먼저
`DesktopTutorial-BlogDeploy`, `DesktopTutorial-SchedulerDispatch`, `DesktopTutorial-DailyTopic`
작업 스케줄러 작업이 중간에 끼어들지 않는지 확인**하고(`Get-ScheduledTask`), 필요하면
작업 전 `Disable-ScheduledTask`로 일시 중지한 뒤 끝나고 다시 `Enable-ScheduledTask`로 켤 것.
