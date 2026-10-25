# 승인된 UI 상태 (공유 상태 문서)

후속 세션이 이어받을 때 **가장 먼저 읽는 문서**입니다. 여기 "승인본"으로 적힌 것은
사용자가 실제 배포와 기능 확인을 마친 상태이며, 사용자 추가 요청 없이 바꾸지 않습니다.

마지막 갱신: 2026-10-25

---

## 1. 지도 선택 UI — 승인본

| 항목 | 값 |
|---|---|
| 상태 | ✅ 사용자 승인 · 배포 검증 완료 |
| 확정일 | 2026-10-25 |
| 저장소 | https://github.com/solusupport-bot/land-in-korea-blog |
| 최종 커밋 | `f782d40` (Redesign map region toolbar for clear navigation and mobile taps) |
| 수정 소스 | `automation/homepage/explore.js` |
| 선행 커밋 | `1d5be14`, `b32837c` — **`f782d40`에 포함됨. 중복 적용 금지** |

### 확정된 UI 사양

- 최초 진입은 **All regions** 상태, 전체 가이드 표시
- 지도 위 **View full Korea map →** 복귀 버튼과 짧은 설명(hint / intro)
- **Viewing + 현재 지역** 배지
- 전체 지역 / 지역별 버튼 **최소 44px 높이** (모바일 탭 영역 확보)
- 추가 지역 선택은 **More regions**
- EN / JA / ZH 카피 모두 포함, 모바일 줄바꿈 및 가로 스크롤 처리
- 기존 지도 데이터(`district-data-seed.json`), 게시글 연결, 승인 홈페이지 디자인 유지

### 배포 검증 기록 (사용자 직접 확인)

- landinkorea.com에서 새 UI가 서빙되는 것 확인
- 부산 선택 → `2 guides`
- 전체 지도 복귀 → `64 guides`
- 입력한 검색어가 복귀 시 빈 값으로 초기화
- `node --check` (JavaScript 문법 검사) 통과

---

## 2. 보존 규칙

1. 이 지도 선택 UI를 **사용자 추가 요청 없이 재디자인하거나 이전 버전으로 되돌리지 않는다.**
2. 새 글 발행, 자동 빌드·배포 중에도 `automation/homepage/explore.js` 소스를 유지한다.
   생성된 `site/index.html`을 고정/동결하는 방식은 쓰지 않는다
   (`CLAUDE.md`의 홈페이지 생성 방식 규칙과 동일).
3. 소스 반영 전 `git status`와 최신 `main`을 확인하고 다른 작업으로 덮어쓰지 않는다.
4. 새 디자인 변경이 필요하면 **먼저 사용자에게 구체적인 변경안을 제시**하고 승인을 받는다.

---

## 3. 배포 환경 메모 — Windows 인코딩

- **증상:** 배포 실패. `validate.py` 출력에서 Windows cp949 `UnicodeEncodeError`.
- **해결:** 사용자 PowerShell에 `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8` 적용 후 배포 성공.
- **미확인 사항:** 위 설정은 당시 PowerShell 세션 기준. **작업 스케줄러
  (`DesktopTutorial-BlogDeploy` 등)에 영구 UTF-8 설정이 들어갔는지는 별도 확인 필요.**
- ⚠️ 이 인코딩 문제를 이유로 품질 검증(`validate.py`, `CLAUDE.md`의 배포 안전장치 4단계)을
  건너뛰거나 완화하지 말 것.

---

## 4. 함께 읽을 문서

- `CLAUDE.md` — 저장소 작업 규칙, 승인된 홈페이지 기준, 배포 경로와 안전장치
- `docs/HOST_CHARACTER.md`
- `docs/SEO_ADSENSE_CHECKLIST.md`
