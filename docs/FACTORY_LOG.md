# 소프트웨어 팩토리 실행 로그

12개 에이전트 역할은 단일 모델이 순차 수행한 **역할 분담 시뮬레이션**입니다.
다만 아래 검증(pytest, 브라우저 E2E, 보안 점검)은 실제로 실행한 결과입니다.

| Phase | 에이전트 | 산출물 / 결과 |
|-------|----------|---------------|
| 1 | 👑 CEO | 마일스톤 M1~M4 설정, 태스크 위임 |
| 1 | 💡 Ideation | `docs/PRD.md` (가정 5개 명시) |
| 1 | 🏗️ Architecture | `docs/ARCHITECTURE.md` (데이터 흐름, WMS 스키마, 상태 규칙) |
| 1 | 🧐 Supervisor | ✅ 승인 — 조건: Decimal 금액, 수식 인젝션 방어 |
| 2 | 🎨 UI/UX | `docs/UI_GUIDE.md` |
| 2 | 💻 Development | `boopang/` 코어 + FastAPI + 웹 UI + CLI |
| 3 | 🔍 QA | 샘플 검수 중 결함 2건 발견 (DISC_RATE 지수 표기, 샘플 수식 셀 소실) |
| 3 | 🔧 Refinement | **루프 1/3** 수정 |
| 3 | 🛡️ Security | 압축폭탄·CSV 초대형 셀·손상 XLSX 3건 발견 |
| 3 | 🔧 Refinement | **루프 2/3** 수정 + 회귀 테스트 3건 |
| 3 | 💰 Loop Controller | 최대 3회 이내 종결, 에스컬레이션 없음 |
| 4 | 🚀 DevOps | Dockerfile(비루트), nginx.conf, docker-compose.yml, run_local.sh |
| 4 | 📚 Documentation | README.md |
| 4 | 👑 CEO | 최종 보고 |

## 후속 작업 (2차)
| 에이전트 | 내용 |
|----------|------|
| 🏗️ Architecture / 💻 Development | 정책 설정 확장(VAT 적용 여부, 청구 수량 기준), 환경변수 fail-fast 검증, `schema_config.json` 기반 WMS 컬럼 매핑 (테스트 49 → 80건) |
| 🚀 DevOps | 실제 `docker build` / `docker compose up` 검증. Healthcheck 체인, `service_healthy` 기동 순서, 로그 로테이션, 권한 축소 |
| 📚 Documentation | README Docker 운영·로그·문제 해결, 스키마 가이드 |

## 검증 결과
- pytest 80건 통과 (파서/검수/금액/상태/내보내기/보안/웹 API/정책 설정/스키마)
- Playwright E2E: 업로드 → 주문 9건 렌더링 → 이슈 탭 → CSV 다운로드, JS 오류 0
- 시크릿·위험 함수(eval/exec/subprocess/pickle) 검색 결과 없음

## 미검증 / 남은 위험
- 운영 서버(TLS 종단, 도메인)에서의 배포는 미검증. Docker 빌드/compose 기동은 이 환경에서 검증함 (빌드는 샌드박스 프록시 CA 를 넣은 임시 컨텍스트 사용)
- 배송비·VAT·청구 수량 규칙은 가정값 (실제 정책 확인 필요)
- 인증 없음, WMS DB 직접 연동 없음
