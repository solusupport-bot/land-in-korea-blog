# 제휴 수익화·SEO 안내 변경 인계 — 2026-10-25 KST

사용자 승인: 홈페이지·블로그는 GPT도 직접 수정할 수 있다. 제휴 수익화를 진행하되 기존 SEO·콘텐츠·사진 품질 규칙을 지킨다. SNS 발행은 이번 범위에 없다.

## 저장된 변경

- 7ecaf7324c29c528eef45a9a6e7293242d6545ab — automation/homepage.py: affiliate_notice()를 공통 안내로 추가. 설정의 affiliate_links_enabled를 읽어 영어 기본 안내와 EN/JA/ZH 안내 데이터를 HTML에 생성한다. JavaScript 없이도 안내가 존재한다.
- 90b515908e728d7b167de356ca61a51f6d69069b — automation/homepage/editorial.js: 언어 변경 시 빌드된 안내를 읽는다. 활성 제휴가 있는데 “제휴 링크 없음”으로 되돌아가던 문제를 수정했다.
- ae32cbf3ce41f14ecb2ab61a0f17f7857c6fb7cd — automation/build.py: 홈페이지와 글·About·Disclosure가 같은 안내를 사용한다. 실제 sponsored 링크가 있는 글에만 본문 앞 짧은 제휴 안내를 추가했다. 일반 플랫폼 주소와 추적 파라미터가 있는 제휴 주소를 구분하고 실제 호스트를 검사한다. Klook aid / Trip.com allianceid / GetYourGuide partner_id를 인식한다. 새 플랫폼 승인이나 제휴 ID를 생성한 작업이 아니다.
- Disclosure의 미래형 문구를 현재 상태에 맞게 정리하고 실제 수정일 2026-10-25를 기록했다. 공통 레이아웃 수정일도 2026-10-25로 갱신했다. 원문 게시일과 글 수정일은 조작하지 않았다.

현재 확인한 config: affiliate_links_enabled=true, Klook aid=132967. Trip.com·GetYourGuide는 일반 주소이며, 두 플랫폼을 활성 제휴사라고 표시하던 열거 문구를 제거했다. 기존 공항 이동/eSIM/DMZ 글의 Klook 연결과 추적 ID를 보존했다. 상품·가격·재고·할인·검증되지 않은 제휴 ID를 새로 만들지 않았다.

## 검증한 범위

- 저장 직전 현재 GitHub 소스를 다시 읽어 다른 변경이 없는지 확인했고, 저장된 세 파일이 검사한 로컬 수정본과 일치함을 확인했다.
- Python 문법 및 editorial.js 문법 검사 통과.
- 실제 최신 eSIM Markdown을 테스트 입력으로 사용해 제휴 활성/비활성, sponsored·추적 ID 유무, 원문 HTML 연속 보존과 canonical 링크 정규화를 확인했다. 테스트 입력만 읽었으며 원문 파일은 수정하지 않았다.
- 홈페이지 생성 테스트에서 JavaScript 없이 안내가 존재하며 canonical을 보존하는지 확인했다. EN/JA/ZH 데이터와 활성/비활성 안내, About·Disclosure의 일치를 검사했다.
- JavaScript 테스트에서 영어→일본어→중국어→영어 전환 후에도 올바른 제휴 안내가 유지됨을 확인했다.
- 앞선 모바일 펼침/접힘·목차·인쇄·검색·더보기 회귀 테스트도 통과했다.
- 홈페이지 생성 시험의 지도 데이터·사진 자산은 축소 테스트 입력이었다. 이것은 실제 전체 미디어·지도·64편 빌드 검증이 아니다.

## 남은 실행

실제 Windows 최신 저장소 전체 64편의 validate.py / build.py / verify_seo.py와 Cloudflare 배포는 이번 환경에서 실행하지 못했다. 위 커밋이 저장됐다는 사실을 landinkorea.com 반영 완료로 보고하지 않는다.

기존 배포 스크립트를 품질 검사 그대로 유지해 실행하고 결과를 확인해야 한다. cp949 오류 방지용 현재 PowerShell 세션:
```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& "$env:USERPROFILE\OneDrive\문서\GitHub\desktop-tutorial\ops\local-cron\deploy-blog-cloudflare.ps1"
```

배포 후 홈페이지 EN/JA/ZH 하단, 제휴 링크가 있는 글 앞 안내, 제휴가 없는 글의 안내 미표시, 일반 링크/제휴 링크 rel, Disclosure, 모바일 접기와 전체 읽기를 실제 화면에서 확인한다. 제휴 귀속·예약·수익은 Klook 파트너 보고서에서 별도로 확인해야 하며 이번 수정으로 매출 발생을 주장하지 않는다.

## 유지해야 할 기준

- 앞선 본문 보존 오류 수정 07fc98b와 지도 승인본 f782d40 및 이후 승인된 모바일 변경을 임의로 되돌리지 않는다.
- verify_seo.py 및 사진·워터마크·중복·발행 품질 검사를 끄거나 완화하지 않았다. 전체 배포에서 그대로 통과해야 한다.
- 원문·기존 URL·내부 링크·canonical·구조화 데이터·RSS·사진을 보존한다. 제휴 확대를 위해 의미 없는 문장을 추가하거나 같은 상품 카드를 반복하지 않는다.
- Klook 외 제휴 활성화는 실제 승인·추적 주소가 확보된 뒤 해당 상태를 확인해서 진행한다.
- 기존 90개 이상 로컬 변경은 이번 환경에서 확인한 적 없다. git status와 차이를 먼저 확인하고 덮어쓰기/reset하지 않는다.
- GitHub SEO 자료 fire-your-seo-agency의 현재 적용판과 Claude 최신 기록을 함께 대조한다. 공유 상태 페이지의 오래된 커밋·역할 기록은 최신 사용자 지시·현재 소스·실제 배포 결과와 구분한다.
- 이번에 Claude 채팅으로 메시지를 보내거나 Windows 바탕화면 폴더에 저장한 것은 아니다. 이 문서는 같은 저장소에서 다음 담당자가 읽기 위한 기록이다.

참고 공식 문서: https://developers.google.com/search/docs/crawling-indexing/qualify-outbound-links
