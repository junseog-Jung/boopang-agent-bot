# 아키텍처 — 부팡 물류 및 주문 처리 자동화 봇

작성: 🏗️ Architecture 에이전트

## 데이터 흐름
```
업로드(.xlsx/.csv)
   │
   ▼
parser.py ── 헤더 별칭 매핑 / 인코딩 판별 ──► RawRow[]
   │
   ▼
inspector.py ── 값 정규화 + 검수 규칙 ──► Line[] + Issue[]
   │
   ▼
pricing.py ── Decimal 금액 계산 ──► Line 금액, Order 합계
   │
   ▼
status.py ── 라인/주문 상태 분류 ──► Line.status, Order.status
   │
   ▼
exporter.py ──► WMS CSV / JSON / 리포트 XLSX
```
`pipeline.process(bytes, filename, config)` 가 위 전 과정을 묶는 단일 진입점이며,
CLI(`boopang/cli.py`)와 웹(`boopang/web/app.py`)이 이를 공유한다.

## 모듈
| 모듈 | 책임 |
|------|------|
| config.py | 환경변수 기반 설정(VAT, 배송비, 제한값) |
| models.py | Line / Order / Issue / Result 데이터클래스 |
| parser.py | 파일 → 정규화된 행 (ParseError) |
| inspector.py | 형 변환 + 검수 이슈 생성 |
| pricing.py | 금액 계산 |
| status.py | 상태 분류 및 주문 롤업 |
| schema.py | WMS 출력 스키마(schema_config.json) 로드·검증 |
| exporter.py | 내부 표준 레코드 → 스키마 매핑 후 CSV/JSON/XLSX 출력, 수식 인젝션 방어 |
| web/app.py | FastAPI (보안 헤더, 변환 API, 잡 API) |
| web/jobs.py | 관제 대시보드: 잡 매니저(TTL 30분, 동시 3건)와 12단계 에이전트 파이프라인 실행기, SSE 이벤트 |
| web/static/dashboard.* | 관제 UI (에이전트 카드, 로그 터미널, 다운로드) — 기본 포트 8899 |

## 물류 DB(WMS) 라인 스키마 (CSV)
`ORDER_NO, LINE_NO, SKU, ITEM_NAME, ORD_QTY, RCV_QTY, UNIT_PRICE, DISC_RATE, SUPPLY_AMT, VAT_AMT, TOTAL_AMT, STATUS_CD, RECIPIENT, TEL, ADDR, SHIP_DT, TRACKING_NO, ISSUE_CNT`

## API
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | /health | 상태 확인 |
| GET | /api/sample | 샘플 xlsx 다운로드 |
| POST | /api/process | multipart `file` → JSON 결과 |
| POST | /api/convert?format=csv\|json\|xlsx | multipart `file` → 변환 파일 |

## 상태 분류 규칙 (우선순위 순)
1. ERROR 이슈 존재 → HOLD
2. 원본 상태 문구: 취소→CANCELLED, 반품→RETURNED, 완료→DELIVERED, 배송중→SHIPPING
3. 운송장 있음 → SHIPPING
4. 검수수량 0 → BACKORDER, 0 < 검수 < 주문 → PARTIAL
5. 검수수량 ≥ 주문 → READY, 검수수량 공란 → INSPECT_PENDING
주문 롤업: 취소 제외 후, HOLD 있으면 HOLD / 단일 상태면 그 상태 / SHIPPING·DELIVERED 혼합이면 SHIPPING / 그 외 PARTIAL
