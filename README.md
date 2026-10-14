# 📦 부팡 물류 및 주문 처리 자동화 봇 (boopang-agent-bot)

엑셀/CSV **패킹리스트·주문 데이터**를 업로드하면 자동으로
**품목별 검수 → 단가 계산 → 배송 상태 분류 → 물류 DB(WMS) 연동 포맷 변환**을 수행합니다.

## 빠른 시작

```bash
pip install -r requirements-dev.txt
python -m pytest -q                      # 테스트 80건
python -m boopang.sample                 # samples/sample_packing_list.xlsx 생성
python -m boopang.cli samples/sample_packing_list.xlsx --out-dir out   # CLI
uvicorn boopang.web.app:app --port 8000  # 웹 UI → http://localhost:8000
```
한 번에: `deploy/run_local.sh` · Docker: `docker compose up --build` (http://localhost)

## Docker 배포 / 운영

```bash
docker compose up --build -d          # http://localhost  (포트 변경: HTTP_PORT=8080 docker compose up -d)
docker compose ps                     # app, nginx 모두 healthy 여야 정상
```
- **Healthcheck**: `app` 은 `/health` 가 200 이어야 healthy, `nginx` 는 `nginx → app` 경로의 `/health` 로 확인합니다.
  `nginx` 는 `app` 이 healthy 가 된 뒤에 기동합니다(`depends_on: service_healthy`).
- **로그** (모두 stdout, json-file 10MB × 3개 로테이션):
  ```bash
  docker compose logs -f app            # uvicorn 요청 로그 / 기동 오류
  docker compose logs -f nginx          # 접속 로그, 413(용량 초과) 등 (/health 는 기록하지 않음)
  docker inspect --format '{{json .State.Health}}' $(docker compose ps -q app)   # 헬스체크 이력
  ```
- **컨테이너 uvicorn 직접 실행** (compose 없이): `docker build -f deploy/Dockerfile -t boopang-bot . && docker run --rm -p 8000:8000 boopang-bot`
  → uvicorn 기동 로그가 터미널에 출력되고 http://localhost:8000 에서 확인합니다.
- **설정/스키마 적용**: `docker-compose.yml` 의 `environment` 를 수정하거나, 스키마는 `volumes` 와 `BOOPANG_SCHEMA_FILE` 주석을 해제해 마운트합니다.
- **보안 설정**: 비루트 사용자, 읽기 전용 루트 FS(+tmpfs /tmp), 모든 capability 제거, `no-new-privileges`, nginx 버전 숨김.

문제 해결

| 증상 | 원인 / 조치 |
|------|-------------|
| `app` 이 계속 `unhealthy`/재시작 | `docker compose logs app` 확인 — 잘못된 환경변수나 스키마 파일 경로가 기동 오류의 대표적 원인 |
| `nginx` 가 시작되지 않음 | `app` 이 healthy 가 아니면 대기합니다. `docker compose ps` 로 app 상태 확인 |
| 업로드 시 413 | nginx `client_max_body_size`(6m) 또는 `BOOPANG_MAX_UPLOAD_BYTES`(5MB) 초과 |
| 80 포트 충돌 | `HTTP_PORT=8080 docker compose up -d` |
| 빌드 중 `pip` SSL 인증서 오류 | 사내/보안 프록시가 인증서를 재서명하는 환경입니다. 프록시 CA 를 이미지에 추가하고 `PIP_CERT` 로 지정하세요 (검증 비활성화 금지) |

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
| BOOPANG_VAT_ENABLED | true | false 면 부가세 0 (면세/영세율) |
| BOOPANG_VAT_RATE | 0.10 | 부가세율 (0~1) |
| BOOPANG_PRICE_INCLUDES_VAT | false | 단가가 VAT 포함이면 true |
| BOOPANG_BILL_BASIS | received | 청구 수량 기준. `received`: min(검수, 주문)·미검수는 주문 / `ordered`: 항상 주문수량 |
| BOOPANG_FREE_SHIPPING_THRESHOLD | 50000 | 이 금액 이상 무료배송 (0이면 배송비 없음) |
| BOOPANG_SHIPPING_FEE | 3000 | 배송비 |
| BOOPANG_MAX_UPLOAD_BYTES | 5242880 | 업로드 최대 크기 |
| BOOPANG_MAX_ROWS | 20000 | 최대 행 수 |
| BOOPANG_SCHEMA_FILE | (내장 기본) | WMS 출력 스키마 JSON 경로 — 아래 참고 |

잘못된 값(숫자 아님, 음수, 허용되지 않은 옵션 등)은 **서버 기동 시점에** 원인 변수명과 함께 오류로 중단됩니다.

## WMS 출력 스키마 커스터마이즈 (`schema_config.json`)
내장 기본 스키마(`boopang/wms_schema.default.json`) 위에 덮어쓰는 방식입니다. 바꾸고 싶은 항목만 적으면 됩니다.
전체 예시는 [schema_config.example.json](schema_config.example.json) (cp949, 상태코드 숫자화, 창고코드 상수 포함).

| 키 | 설명 |
|----|------|
| `line_columns` / `order_columns` | 출력 컬럼 목록(순서 = 출력 순서). 항목은 `{"column": 출력명, "source": 내부필드}` 또는 `{"column": 출력명, "value": 고정값}` |
| `status_codes` | 내부 상태 → WMS 상태코드 (예: `"READY": "10"`) |
| `csv.delimiter`, `csv.encoding` | 구분자(한 글자), 인코딩 (`utf-8-sig`, `utf-8`, `cp949` …) |
| `date_format` | 출력 날짜 형식 (strftime, 예: `%Y%m%d`) |
| `schema_version` | JSON 루트의 `schema_version` 값 |

사용 가능한 `source`
- 라인: `order_id line_no sku name qty_ordered qty_received unit_price discount_pct supply_amt vat_amt total_amt status status_label recipient phone address ship_date tracking_no issue_count`
- 주문: `order_id status status_label line_count supply_amt vat_amt shipping_fee total_amt`

```bash
BOOPANG_SCHEMA_FILE=./schema_config.example.json uvicorn boopang.web.app:app
python -m boopang.cli 입력.xlsx --schema schema_config.example.json   # CLI 는 --schema 로도 지정 가능
```
컬럼 중복, 알 수 없는 `source`, 잘못된 인코딩 등은 오류로 거부됩니다. 출력 인코딩으로 표현할 수 없는 문자(예: cp949 에서 이모지)는
조용히 깨뜨리지 않고 오류(웹 API 422)로 알립니다. 웹 UI 미리보기는 스키마와 무관하게 고정 키를 사용합니다.

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
실제 정책에 맞게 환경변수로 조정하세요. Docker 이미지 빌드와 `docker compose up`(app + nginx healthy, 프록시 경유 업로드/변환, 413/415 처리, 읽기 전용 FS)은 검증했으나, 운영 서버(HTTPS/TLS 종단, 도메인)에서의 검증은 별도입니다.
