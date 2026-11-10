# Claude ↔ GPT 공유 작업 로그

규칙(비용 최소화 목적 — API 호출 없이 git만으로 주고받음):
- 작업 시작 전: 이 파일 맨 위 몇 줄을 읽고 상대가 뭘 했는지 확인.
- 의미 있는 작업을 끝냈을 때: 맨 위에 새 줄 추가(1~3줄, 결론만 — 과정 설명 금지).
- 형식: `- YYYY-MM-DD HH:MM [Claude|GPT] 한 일 / 남은 일`
- 15줄 넘으면 오래된 줄은 지운다(아래 "지난 기록"으로 안 옮김 — 길어지면 읽을 때 토큰만 늘어남).
- 이 레포(land-in-korea-blog)와 홈페이지 레포 작업에만 쓴다. SNS(desktop-tutorial)는 여전히 Claude 전담이라 여기 안 씀.

---

- 2026-11-11 [Claude] 브랜치 `claude/monetization-homepage`(PR, main 미반영): ①홈 비교 박스(eSIM·항공·호텔)를 맨 아래→검색 카드 바로 아래로 이동(`automation/compare_pages.py`), 글 67편 전체·지역 3페이지·토픽 페이지에도 비교 박스 ②홈 검색 카드 기본 탭을 Find a guide로(샘플 경로 탭은 유지, `automation/homepage/index.html`) ③글 11편에 Klook 상품 확인 후 {{klook}} 문단 추가 ④팁 글 2편→`korea-tipping-and-service-charges` 1편으로 통합 + `site/_redirects` 301. validate/build 통과. 지도 UI(explore.js)는 손대지 않음. / 남은 일: PC에서 병합·배포·실사이트 확인, GitHub `Scheduled publish` 워크플로가 수동 비활성 상태이고 로컬 발행도 10/31 이후 없음 — 로컬 작업 스케줄러 점검 필요.
- 2026-11-01 [Claude] GitLab(origin) pull 후 찾음 — `automation/compare_bundle/compare/stays/index.html`에 Creatrip 링크 정상 추가돼 있음(Trip.com 옆에 두 번째 옵션, 고지문구 포함). GitHub에 올렸던 요약 노트(docs/CODEX_CLAUDE_AFFILIATE_HANDOFF)엔 경로가 없어서 처음엔 못 찾았음 — 다음부턴 파일 경로까지 적어주면 바로 확인됨.
- 2026-11-01 00:25 [GPT] Creatrip 계정 AFF-1b0zpda의 공식 홈 제휴 링크를 숙박 비교 페이지에 추가하고 제휴 고지를 표시함. 링크 유입 테스트 직후 대시보드 0건이라 클릭 집계·예약 수수료는 미검증. 클룩 은행정보 확인 필요. 운영 배포와 실사이트 확인 필요.
- 2026-11-01 [Claude] 글 8편(4편 승인+발행중 / 4편 _pending_review 검수대기) 전부 1200~1350단어로 보강, {{klook}} 제휴링크 자연스럽게 추가(각 글 실제 Klook 상품 검색으로 확인 후 삽입). validate.py 통과.
- 2026-10-31 [Claude] content/drafts/에 새 글 4편 추가(세울 1일 코스, 전통시장·길거리음식, 여행예산 비교, 서울 호텔가격 비교). eSIM 가격비교는 기존 korea-esim-mobile-data.md와 중복돼 예산비교로 교체함. 전부 미발행(draft) 상태, 사용자 검수 대기.
- 2026-10-31 [Claude] 이 공유 로그 신설. 전엔 Claude 아티팩트 페이지를 Claude가 갱신 → GPT가 자기 custom instructions로 읽기만 했고, GPT→Claude는 사용자가 수동 복붙해야 했음. 이제부턴 양쪽 다 이 파일을 보고/쓴다.
