# 📦 부팡 물류 및 주문 처리 자동화 봇 (boopang-agent-bot)

엑셀/CSV **패킹리스트·주문 데이터**를 업로드하면 자동으로
**품목별 검수 → 단가 계산 → 배송 상태 분류 → 물류 DB(WMS) 연동 포맷 변환**을 수행합니다.

## 빠른 시작

```bash
pip install -r requirements-dev.txt
python -m pytest -q                      # 테스트 49건
python -m boopang.sample                 # samples/sample_packing_list.xlsx 생성
python -m boopang.cli samples/sample_packing_list.xlsx --out-dir out   # CLI
uvicorn boopang.web.app:app --port 8000  # 웹 UI → http://localhost:8000
```
한 번에: `deploy/run_local.sh` · Docker: `docker compose up --build` (http://localhost)

## 입력 형식
첫 시트(또는 CSV)의 헤더를 한글/영문 별칭으로 자동 인식합니다.

| 필수 | 선택 |
|------|------|
| 주문번호, SKU(품목코드), 주문수량, 단가 | 품목명, 검수수량(입고수량), 할인율, 수령인, 연락처, 주소, 출고일, 운송장번호, 배송상태, 비고 |

CSV 는 UTF-8(BOM 포함) 또는 CP949 지원. 상단에 제목 행이 있어도 처음 10행 안에서 헤더를 찾습니다.

## 처리 규칙
**검수** — `ERROR`(해당 라인 HOLD): 주문번호/SKU 누락, 수량·단가·할인율·검수수량 형식 오류.
`WARN`: 입고 부족/초과, 품목명 누락, 0원 단가, 연락처·출고일 형식 오류, 중복 SKU, 상태·운송장 불일치.
`INFO`: 검수수량 공란(미검수).

**단가 계산** — `Decimal`, 원 단위 HALF_UP. 기본은 단가 VAT 별도, 청구 수량 = min(검수수량, 주문수량)
(미검수면 주문수량). 취소·반품·HOLD 라인은 금액 0. 주문 합계 50,000원 미만이면 배송비 3,000원.

**배송 상태** — READY 출고대기 · SHIPPING 배송중 · DELIVERED 배송완료 · PARTIAL 부분입고 ·
BACKORDER 입고대기 · INSPECT_PENDING 검수대기 · HOLD 보류(오류) · CANCELLED 취소 · RETURNED 반품.
세부 우선순위는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**출력** — `wms_lines.csv`(고정 18컬럼, UTF-8 BOM), `wms_orders.json`(schema_version 1.0, 주문→라인 중첩),
`inspection_report.xlsx`(요약/주문/WMS라인/검수이슈).

## 설정 (환경변수)
| 변수 | 기본값 | 설명 |
|------|--------|------|
| BOOPANG_VAT_RATE | 0.10 | 부가세율 |
| BOOPANG_PRICE_INCLUDES_VAT | false | 단가가 VAT 포함이면 true |
| BOOPANG_FREE_SHIPPING_THRESHOLD | 50000 | 이 금액 이상 무료배송 (0이면 배송비 없음) |
| BOOPANG_SHIPPING_FEE | 3000 | 배송비 |
| BOOPANG_MAX_UPLOAD_BYTES | 5242880 | 업로드 최대 크기 |
| BOOPANG_MAX_ROWS | 20000 | 최대 행 수 |

## API
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | /health | 상태 확인 |
| GET | /api/sample | 샘플 xlsx |
| POST | /api/process | multipart `file` → JSON (summary, orders[lines], issues) |
| POST | /api/convert?format=csv\|json\|xlsx | multipart `file` → 파일 다운로드 |
| GET | /api/docs | Swagger UI |

오류 코드: 400 잘못된 format · 413 용량 초과 · 415 확장자 불가 · 422 파일 해석 불가(필수 컬럼 누락 등)

```bash
curl -F file=@samples/sample_packing_list.xlsx "localhost:8000/api/convert?format=csv" -o wms_lines.csv
```

## 보안
무상태(파일 미저장) · 5MB/20,000행 제한 · 확장자 검증 · XLSX 압축폭탄 차단 · 엑셀 수식 인젝션 방어
(`= + - @` 시작 문자열 앞에 `'` 부착) · 엄격한 CSP/보안 헤더 · UI는 `textContent` 렌더링 · 컨테이너 비루트 실행.
**인증은 포함되어 있지 않습니다.** 사내망/리버스 프록시 인증 뒤에서 운영하세요.

## 한계 / 후속 과제
WMS DB 직접 연동, 사용자 인증, 이력 저장은 범위 밖입니다. 배송비·VAT·청구 수량 규칙은 가정값이므로
실제 정책에 맞게 환경변수로 조정하세요. Docker 이미지는 이 개발 환경에서 빌드 검증하지 못했습니다.
