# 📦 부팡 물류 및 주문 처리 자동화 봇 (boopang-agent-bot)

두 가지 모드를 가진 멀티 에이전트 관제 대시보드(`http://127.0.0.1:8899/`)입니다.
1. **📦 물류 변환** — 엑셀/CSV **패킹리스트·주문 데이터**를 업로드하면 **품목별 검수 → 단가 계산 → 배송 상태 분류 → 물류 DB(WMS) 연동 포맷 변환**을 수행합니다.
2. **🧠 소프트웨어 제작 (범용 팩토리)** — 자연어 요구사항을 PRD → 설계 → **다중 파일 소스코드** → 정적 QA·보안 스캔 → 자가 치유 → **ZIP**으로 만듭니다. (Claude API 필요, 아래 참고)

## 빠른 시작

```bash
pip install -r requirements-dev.txt
python -m pytest -q                      # 테스트 (Docker 샌드박스 이미지가 있으면 격리 테스트 포함)
uvicorn boopang.web.app:app --host 127.0.0.1 --port 8899   # 관제 대시보드 → http://127.0.0.1:8899/
```
- **포트는 8899 고정을 권장**합니다 (다른 봇과의 충돌 방지). 다른 값이 필요하면 `--port` 로 지정하세요.
- 같은 동작: `python -m boopang.web` (환경변수 `BOOPANG_HOST`/`BOOPANG_PORT`, 기본 `127.0.0.1:8899`) 또는 `deploy/run_local.sh` (테스트 후 기동)
- 샘플/CLI: `python -m boopang.sample` → `samples/sample_packing_list.xlsx`,
  `python -m boopang.cli samples/sample_packing_list.xlsx --out-dir out`
- Docker: `docker compose up --build -d` → http://localhost (앱 내부 포트는 8899, nginx 가 80 으로 공개)

## 멀티 에이전트 관제 대시보드 (http://127.0.0.1:8899/)
1. **업무 지시 + 파일 업로드** → `에이전트 팀 가동`
2. **12명의 오피스 직원 카드**가 대기 → 구상 중 → 코딩 중 → 검수 중 → 완료(오류/생략 포함)로 실시간 전환됩니다.
   - 직원마다 외모·소품이 다른 SVG 아바타가 책상에서 일합니다 (타이핑, 화이트보드 필기, 서류·체크리스트 검토, 코드 스크롤, 쉴 때는 커피 김 등).
   - **핸드오프 연출**: 한 에이전트가 일을 마치고 다음 에이전트가 시작하면 문서 패킷과 빛줄기가 카드 사이를 날아가 도착 지점에서 터집니다. 일하는 카드는 상태 색(보라/하늘/호박)으로 빛나고, 대기 카드는 채도를 낮춘 대기 모드입니다.
   - 서버 로그가 오면 해당 직원 머리 위에 **말풍선**으로 잠깐 표시됩니다. `prefers-reduced-motion` 설정을 존중합니다(패킷 생략).
3. **실시간 로그 터미널**: 서버가 SSE(Server-Sent Events)로 단계별 로그를 스트리밍합니다. 끊기면 `Last-Event-ID` 로 이어받습니다.
4. 완료되면 요약 카드와 **WMS CSV / JSON / XLSX 리포트 다운로드 버튼**이 나타납니다.

> **정직하게 알아둘 점**
> - 에이전트 카드는 LLM 12개가 아니라, **실제 처리 파이프라인의 단계를 12개 역할로 나눠 보여주는 관제 화면**입니다. 각 단계의 로그 수치(행 수, 이슈 수, 금액, 합계 대조, 수식 인젝션 의심 건수)는 실제 처리 결과입니다.
> - '업무 지시' 입력은 로그와 요약에 **기록만** 되며 처리 방식을 바꾸지 않습니다 (처리 규칙은 환경변수/스키마로 설정).
> - 단계 사이 지연(`BOOPANG_STEP_DELAY`, 기본 0.4초)은 화면 연출용입니다. `0` 이면 즉시 실행됩니다.

| 에이전트 | 실제로 하는 일 |
|----------|----------------|
| 👑 CEO | 지시·파일 접수 기록, 마일스톤 안내, 최종 승인 |
| 🧐 Supervisor | 단계별 승인, **라인 합계 = 주문 합계 대조**(불일치 시 작업 실패) |
| 💡 Ideation | 파일 파싱, 헤더 별칭 매핑, 행 수 확인 |
| 🏗️ Architecture | 정책 설정(VAT·배송비·청구 기준)과 WMS 스키마 로드·검증 |
| 🎨 UI/UX | 결과 요약 화면 구성 (연출 단계) |
| 💻 Development | 값 정규화, 상태 분류, 금액 계산 |
| 🔍 QA / Review | 검수 이슈(ERROR/WARN/INFO) 집계·상위 항목 보고 |
| 🔧 Refinement | 오류 라인은 **자동 수정하지 않고** 수동 수정 필요 항목으로 보고 (없으면 생략) |
| 🛡️ Security | 수식 인젝션 의심 값 탐지, 입력 한도 확인 |
| 🚀 DevOps | CSV/JSON/XLSX 산출물 생성 |
| 📚 Documentation | 주문 상태 분포 요약 |
| 💰 Loop Controller | 재작업 루프(0/3)와 소요 시간 점검 |

운영 특성: 작업 결과는 **서버 메모리에 30분간만** 보관 후 삭제, 동시 처리 3건 제한(초과 시 429), 작업 ID는 추측 불가한 랜덤 토큰.
표 형태로 주문을 훑어보고 싶다면 `/converter` 를 사용하세요.

## 🧠 범용 소프트웨어 팩토리 (소프트웨어 제작 모드)

대시보드에서 **소프트웨어 제작** 탭을 고르고 만들고 싶은 것을 자연어로 적으면 12개 역할이 아래처럼 일합니다.

| 단계 | 에이전트 | 실제 동작 |
|------|----------|-----------|
| 1 | CEO | 요구사항 접수, 위험 요청 거절 게이트, 최종 보고 |
| 2 | Ideation | LLM 이 자연어 → **PRD**(기능·스택·위험·가정) 작성 |
| 3 | Architecture / Supervisor | LLM 이 아키텍처·**파일 구조** 설계, 규칙 기반으로 경로/개수/한도 검증 |
| 4 | UI/UX | 화면 계획 정리 (UI 없으면 생략) |
| 5 | Development | 파일마다 LLM 호출로 **다중 파일 코드** 생성 |
| 6 | QA | 파이썬 `ast`, JSON, `node --check`(있을 때), HTML 태그, 미선언 import 정적 검사 |
| 7 | Security | 정적 보안 스캔: `eval/exec`, `os.system`, `shell=True`, 역직렬화, **SQL 인젝션**, TLS 검증 해제, **하드코딩 시크릿**, **innerHTML XSS** 등 |
| 8 | Refinement | ERROR 가 있으면 LLM 이 해당 파일만 수정 → 재검사, **최대 3회**. 해결 못 하면 **에스컬레이션**(사람 검토 필요로 표기) |
| 9 | DevOps / Documentation | `.gitignore` 보강, README·`FACTORY_REPORT.md` 작성, **ZIP 패키징**(`<slug>/…` 레포 구조) |
| 10 | Loop Controller | 루프 횟수, LLM 호출 수, 토큰 사용량 점검 |

결과 화면에는 **요약 / PRD·설계 / 소스 코드 뷰어(파일 트리)** 탭과 **프로젝트 ZIP 다운로드**가 나옵니다.

### 켜는 방법 (기본은 꺼져 있음 — 유료 호출이고 인증이 없기 때문)
```bash
export ANTHROPIC_API_KEY=sk-ant-...        # 저장소/로그에 남기지 마세요
export BOOPANG_FACTORY_ENABLED=true
uvicorn boopang.web.app:app --host 127.0.0.1 --port 8899
```
키 없이 화면과 파이프라인만 보고 싶다면 `BOOPANG_FACTORY_DEMO=true` — **요청과 무관하게 항상 같은 '할 일 관리' 샘플**을 만듭니다(자가 치유 시연을 위해 SQL 인젝션 결함을 일부러 넣고 고칩니다). 화면과 리포트에 데모라고 명시됩니다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| BOOPANG_FACTORY_ENABLED | false | 소프트웨어 팩토리 허용 (`/api/factory/jobs` 가 403 이 아니게 됨) |
| BOOPANG_FACTORY_DEMO | false | 키 없이 쓰는 고정 샘플 데모 |
| ANTHROPIC_API_KEY | — | Claude API 키 (환경변수로만 읽고 로그·응답에 쓰지 않음) |
| BOOPANG_LLM_MODEL | claude-opus-5-5 | 사용할 Claude 모델 |
| BOOPANG_LLM_EFFORT | medium | low / medium / high / xhigh / max |
| BOOPANG_FACTORY_MAX_FILES | 25 | 프로젝트당 최대 파일 수 |
| BOOPANG_FACTORY_MAX_FILE_BYTES | 60000 | 파일당 최대 크기 |
| BOOPANG_FACTORY_MAX_TOTAL_BYTES | 400000 | 프로젝트 전체 최대 크기 |
| BOOPANG_FACTORY_MAX_CALLS | 40 | 작업 1건당 LLM 호출 상한 (설계가 이보다 크면 시작 전에 거절) |
| BOOPANG_FACTORY_MAX_JOBS_PER_HOUR | 10 | 시간당 제작 건수 제한 (비용 보호) |

### 🆓 로컬 무료 LLM: Ollama
API 비용 없이 로컬 오픈소스 코딩 모델로 생성할 수 있습니다 (Claude 와 같은 파이프라인, 공급자만 교체).
```bash
ollama serve &                                   # https://ollama.com 에서 설치
ollama pull qwen2.5-coder:7b-instruct            # 또는 deepseek-coder 등
export BOOPANG_FACTORY_ENABLED=true BOOPANG_LLM_PROVIDER=ollama
uvicorn boopang.web.app:app --host 127.0.0.1 --port 8899
```
- 구조화 출력(`format` JSON 스키마)을 사용하므로 **Ollama 0.5 이상**이 필요합니다. 형식을 어기면 1회 교정 재시도합니다.
- 기본 컨텍스트(`num_ctx`)는 8192로 올려서 호출합니다 (Ollama 기본값은 코드 생성에 너무 작습니다). CPU 추론은 느려서 타임아웃 기본값이 600초입니다.
- **작은 모델(7B)은 품질이 낮습니다.** 파일 수를 줄이고(`BOOPANG_FACTORY_MAX_FILES=8` 등) 요구사항을 작게 시작하세요. 자가 치유 루프가 일부를 보완하지만 한계가 있습니다. 가능하면 14B 이상을 권장합니다.
- Docker compose 에서 호스트의 Ollama 에 연결하려면 호스트에서 `OLLAMA_HOST=0.0.0.0 ollama serve` 로 띄우세요 (`host.docker.internal` 사용).

| 변수 | 기본값 | 설명 |
|------|--------|------|
| BOOPANG_LLM_PROVIDER | anthropic | `anthropic` \| `ollama` \| `demo` |
| BOOPANG_OLLAMA_HOST (또는 OLLAMA_HOST) | http://localhost:11434 | Ollama 서버 주소 (`host:port` 형태도 허용) |
| BOOPANG_OLLAMA_MODEL | qwen2.5-coder:7b-instruct | 사용할 모델 |
| BOOPANG_OLLAMA_TIMEOUT / BOOPANG_OLLAMA_NUM_CTX | 600 / 8192 | 응답 대기(초) / 컨텍스트 길이 |

### 🔌 MCP 도구 서버 (`boopang/mcp_server.py`)
에이전트는 **MCP(Model Context Protocol, stdio)** 로 파일을 만들고 검증합니다. 작업마다 격리된 임시 디렉터리를 root 로 하는 MCP 서버(`python -m boopang.mcp_server --root …`)를 띄우고, 파이프라인이 MCP 클라이언트로 호출합니다.

| 도구 | 역할 |
|------|------|
| `write_project_file(file_path, content)` | 워크스페이스 안에 파일 생성/덮어쓰기 |
| `read_project_file(file_path)` | 파일 읽기 |
| `list_project_files()` | 파일 목록 + sha256 (Supervisor 가 메모리 결과와 **해시로 디스크 대조**) |
| `run_command(command)` | **허용 목록** 명령만: `pytest [-q -x -v -k 식 경로]`, `python -m py_compile <파일>`, `node --check <파일>` |

> **설계상 일부러 다르게 만든 점**: 흔한 예시처럼 `run_command` 를 `subprocess.run(shell=True)` 로, `write_project_file` 을 임의 경로로 열어두면 LLM(또는 요구사항에 섞인 프롬프트 인젝션)이 호스트에서 임의 명령 실행·임의 파일 덮어쓰기를 할 수 있습니다. 그래서 ① 모든 경로를 워크스페이스 안으로 가두고(`..`·절대경로·심볼릭 링크·`.env`·허용 외 확장자 거부, 파일 수/크기 한도) ② 셸 없이 argv 로 실행하며 허용 목록 밖의 명령(`rm`, `curl`, `pip install`, `python -c`, 파이프/`;` 등)은 거부하고 ③ MCP 서버 프로세스에는 API 키 등 서버 환경변수를 넘기지 않습니다.
> `mcp` 패키지는 2.x 에서 `FastMCP` 가 `MCPServer` 로 바뀌어 `from mcp.server.fastmcp import FastMCP` 가 깨지므로 `mcp>=1.9,<2` 로 고정했습니다.
> MCP 서버를 못 띄우면(미설치 등) 경고를 표시하고 메모리 모드로 계속합니다. `BOOPANG_FACTORY_MCP=false` 로 끌 수 있습니다.

### 🧪 테스트 실행 모드 (`BOOPANG_FACTORY_EXEC`)
| 값 | 동작 |
|----|------|
| `off` (기본) | 생성된 테스트를 **실행하지 않음** (문법 검사·정적 보안 스캔만) |
| `docker` | 생성된 `pytest` 를 **네트워크 차단·비루트·읽기 전용 루트·메모리/CPU/PID 제한** Docker 컨테이너에서 실행. 먼저 `deploy/build_sandbox.sh` 로 `boopang-sandbox` 이미지를 빌드. 컨테이너에 네트워크가 없으므로 이미지에 설치된 패키지(pytest, fastapi, httpx, uvicorn, pydantic, flask, requests, jinja2, sqlalchemy)만 쓸 수 있고, 설계 프롬프트가 이 목록을 모델에 알려줍니다 |
| `local` | **⚠ 격리 없이** 이 서버 프로세스 권한으로 실행(CPU/메모리/파일 크기 rlimit, 환경변수 제거, 타임아웃 시 프로세스 그룹 강제 종료만 적용). 생성된 코드는 네트워크와 파일 시스템에 접근할 수 있으므로 **신뢰할 수 있는 로컬 개발 PC 에서만** 쓰세요 |

실행 결과(통과/실패와 출력)는 QA 의 발견 사항이 되어 **자가 치유 루프로 되돌아갑니다**(최대 3회). `docker`/`local` 은 호스트에서 직접 uvicorn 을 띄울 때 쓰며, 제공된 docker compose(읽기 전용·소켓 없음)에서는 `off` 로 고정되어 있습니다. 환경변수 `BOOPANG_SANDBOX_IMAGE`, `BOOPANG_EXEC_TIMEOUT`(기본 60초), `BOOPANG_WORKSPACE_DIR`.

### 안전장치
- 생성된 코드는 **기본적으로 서버에서 실행하지 않습니다.** 문법·정적 보안 점검만 합니다. (테스트 실행은 아래 *테스트 실행 모드*에서 명시적으로 켠 경우에만)
- LLM 이 만든 경로/내용은 신뢰하지 않습니다: 경로 탐색(`..`, 절대경로, 숨김 파일, `.env`)·허용 확장자·파일 수·크기 검증, ZIP 은 실행 비트 없이 생성, 리파인은 **이미 있는 문제 파일만** 수정 가능.
- 사용자 요구사항은 '데이터'로만 취급하도록 프롬프트에 명시, 명백히 해로운 요청은 PRD 단계에서 거절합니다.
- 오류 메시지에는 API 키나 응답 본문을 넣지 않습니다. 소스 뷰어는 `textContent`/`text/plain` 으로만 표시합니다.

### 한계 (정직하게)
- **정적 점검 통과 ≠ 동작 보장.** 테스트 실행 모드가 꺼져 있으면 런타임 오류·논리 오류·import 경로 문제는 못 잡습니다. 생성 결과는 **초안**이며 직접 검토·테스트해야 합니다. (데모 프로젝트를 풀어 테스트를 돌려 보다가 `pytest` import 경로 결함을 실제로 발견해 고쳤습니다.)
- **실제 Claude 호출 경로는 이 저장소 개발 환경에서 라이브로 검증하지 못했습니다**(API 키 없음). 요청 형태는 SDK 타입과 모킹 테스트로 검증했고, 첫 실제 사용 시 모델 출력이 스키마/한도에 맞는지 확인하세요.
- **Git MCP(자동 커밋/브랜치)**, 프리뷰 URL 발급, GitHub 자동 푸시는 **구현하지 않았습니다.** 저장소 쓰기 토큰·권한 설계가 먼저 필요한 후속 과제입니다.
- **Ollama 경로는 실제 모델로 검증하지 못했습니다**(개발 환경에 Ollama 없음). Ollama 와 같은 HTTP 규격의 가짜 서버로 요청 형태·오류 처리·전체 파이프라인을 검증했습니다. 실제 모델의 출력 품질은 직접 확인하세요.
- 한 번에 큰 시스템(예: 인스타그램 클론 전체)은 파일/호출 한도 안의 MVP 범위로 줄여서 만듭니다.

## Docker 배포 / 운영

```bash
docker compose up --build -d          # http://localhost  (포트 변경: HTTP_PORT=8080 docker compose up -d)
docker compose ps                     # app, nginx 모두 healthy 여야 정상
```
- **포트**: 앱 컨테이너는 8899 로 리슨하고 `nginx` 가 `${HTTP_PORT:-80}` 으로 공개합니다. SSE(실시간 로그)가 끊기지 않도록 nginx 에서 해당 경로의 버퍼링을 껐습니다.
- **Healthcheck**: `app` 은 `/health` 가 200 이어야 healthy, `nginx` 는 `nginx → app` 경로의 `/health` 로 확인합니다.
  `nginx` 는 `app` 이 healthy 가 된 뒤에 기동합니다(`depends_on: service_healthy`).
- **로그** (모두 stdout, json-file 10MB × 3개 로테이션):
  ```bash
  docker compose logs -f app            # uvicorn 요청 로그 / 기동 오류
  docker compose logs -f nginx          # 접속 로그, 413(용량 초과) 등 (/health 는 기록하지 않음)
  docker inspect --format '{{json .State.Health}}' $(docker compose ps -q app)   # 헬스체크 이력
  ```
- **컨테이너 uvicorn 직접 실행** (compose 없이): `docker build -f deploy/Dockerfile -t boopang-bot . && docker run --rm -p 8899:8899 boopang-bot`
  → uvicorn 기동 로그가 터미널에 출력되고 http://localhost:8899 에서 확인합니다.
- **설정/스키마 적용**: `docker-compose.yml` 의 `environment` 를 수정하거나, 스키마는 `volumes` 와 `BOOPANG_SCHEMA_FILE` 주석을 해제해 마운트합니다.
- **보안 설정**: 비루트 사용자, 읽기 전용 루트 FS(+tmpfs /tmp), 모든 capability 제거, `no-new-privileges`, nginx 버전 숨김.

문제 해결

| 증상 | 원인 / 조치 |
|------|-------------|
| `app` 이 계속 `unhealthy`/재시작 | `docker compose logs app` 확인 — 잘못된 환경변수나 스키마 파일 경로가 기동 오류의 대표적 원인 |
| `nginx` 가 시작되지 않음 | `app` 이 healthy 가 아니면 대기합니다. `docker compose ps` 로 app 상태 확인 |
| 업로드 시 413 | nginx `client_max_body_size`(6m) 또는 `BOOPANG_MAX_UPLOAD_BYTES`(5MB) 초과 |
| 실시간 로그가 한꺼번에 늦게 나타남 | 프록시가 SSE 를 버퍼링하는 경우입니다. 별도 리버스 프록시를 쓴다면 `/api/jobs/*/events` 에 `proxy_buffering off` 를 적용하세요 |
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
| BOOPANG_STEP_DELAY | 0.4 | 대시보드 단계 간 연출 지연(초, 0~5) |
| BOOPANG_HOST / BOOPANG_PORT | 127.0.0.1 / 8899 | `python -m boopang.web` 실행 시 바인딩 |
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
| GET | /api/agents?mode=logistics\|software | 에이전트 12개 목록(모드별 역할 설명) |
| GET | /api/factory/status | 소프트웨어 팩토리 사용 가능 상태 (`llm`/`demo`/`unconfigured`/`disabled`) |
| POST | /api/factory/jobs | form `request`(자연어 요구사항) → 제작 파이프라인 시작 (`job_id`) |
| GET | /api/jobs/{id}/files, /file?path= | 생성된 파일 트리 / 파일 내용(text/plain) |
| POST | /api/jobs | multipart `file` + `instruction` → 파이프라인 시작 (`job_id`) |
| GET | /api/jobs/{id} | 작업 상태·요약 |
| GET | /api/jobs/{id}/events | 실시간 로그/상태 SSE 스트림 |
| GET | /api/jobs/{id}/download/{csv\|json\|xlsx\|zip} | 산출물 다운로드 (완료 전 409) |
| POST | /api/process | multipart `file` → JSON (summary, orders[lines], issues) |
| POST | /api/convert?format=csv\|json\|xlsx | multipart `file` → 파일 다운로드 |
| GET | /api/docs | Swagger UI |

오류 코드: 400 잘못된 format · 404 만료/없는 작업 · 409 산출물 미준비 · 413 용량 초과 · 415 확장자 불가 · 422 파일 해석 불가(필수 컬럼 누락 등) · 429 동시 작업 초과

```bash
curl -F file=@samples/sample_packing_list.xlsx "localhost:8899/api/convert?format=csv" -o wms_lines.csv
```

## 보안
파일 디스크 미저장(대시보드 결과는 메모리 30분 보관) · 5MB/20,000행 제한 · 확장자 검증 · XLSX 압축폭탄 차단 · 엑셀 수식 인젝션 방어
(`= + - @` 시작 문자열 앞에 `'` 부착) · 엄격한 CSP/보안 헤더 · UI는 `textContent` 렌더링 · 컨테이너 비루트 실행.
**인증은 포함되어 있지 않습니다.** 사내망/리버스 프록시 인증 뒤에서 운영하세요.

## 한계 / 후속 과제
WMS DB 직접 연동, 사용자 인증, 이력 저장은 범위 밖입니다. 배송비·VAT·청구 수량 규칙은 가정값이므로
실제 정책에 맞게 환경변수로 조정하세요. Docker 이미지 빌드와 `docker compose up`(app + nginx healthy, 프록시 경유 업로드/변환, 413/415 처리, 읽기 전용 FS)은 검증했으나, 운영 서버(HTTPS/TLS 종단, 도메인)에서의 검증은 별도입니다.
