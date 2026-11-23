# nuti-pipeline

공공 API 13개를 주기적으로 받아 34컬럼 제품 데이터를 계산하고, `{DATA_DIR}/csv/product_master_YYYYMMDD.csv`로 내보낸다.
api(Spring)는 이 CSV만 읽어 RDS에 반영한다. 파이프라인은 DB에 접속하지 않는다.

## 로컬 실행 준비

```powershell
python -m venv .venv              # 이 프로젝트 전용 Python 환경을 .venv 폴더에 만든다 (처음 한 번)
.\.venv\Scripts\Activate.ps1      # 지금 터미널이 .venv 의 Python 을 쓰게 한다 (터미널 열 때마다)
pip install -e . pytest           # 필요한 패키지와 pipeline 명령을 설치한다 (pyproject.toml 이 바뀌면 다시)
copy .env.example .env            # 인증키 설정 파일을 만들고 키를 채운다
pipeline init                     # 상태 저장 파일 data\state.db 를 만든다 (처음 한 번)
```

## 테스트

```powershell
pytest                            # 규칙 테스트. 실제 API 는 부르지 않는다
pytest -m live -s                 # 실제 공공 API 13개를 1페이지씩 불러 확인 (호출 18회, 기관 한도에 들어감)
pytest -m live -s -k HACCP        # 실제 API 확인을 하나만 (-k 뒤 이름이 들어간 테스트만 실행)
pytest -m live -s -k datagokr     # 공공데이터포털 5개만
pytest -m live -s -k foodsafety   # 식품안전나라 8개만 (증분 조건 확인 포함, 호출 13회)
```

## API 확인

```powershell
pipeline probe C002                          # API 한 페이지만 불러 결과 코드 · 행 수 · 첫 행을 보여 준다 (저장 안 함, 호출 1회)
pipeline probe C002 --cond 20261121          # 조건을 넣어 부른다 (증분은 YYYYMMDD, 생산실적 I0300 · I1420 은 YYYY)
pipeline probe C002 --page 3                 # 3페이지를 부른다
pipeline probe HACCP --raw                   # 응답 본문을 그대로 보여 준다 (기본 앞 2000자, --raw 5000 처럼 길이 지정)
pipeline probe HACCP --param returnType=json # 공공데이터포털 요청에 파라미터를 더해 본다 (여러 번 가능, 수집에는 안 씀)
```

API 이름: `NUTRI_DB` `FOODQR_LIST` `FOODQR_NUTRITION` `FOODQR_MAKER` `HACCP` `C005` `I2570` `C002` `C006` `I2852` `I0490` `I0300` `I1420` (대소문자 무관)

## 수집

```powershell
pipeline collect                             # 주기가 된 API 의 회차를 열고, 받는 중인 회차를 한도 안에서 이어 받는다 (서버에서는 cron 10분마다)
pipeline status                              # API 별 마지막 회차 · 마지막 전량 완료 시각 · 원본 행 수 · 오늘 호출 수
pipeline round start NUTRI_DB FULL           # 회차를 손으로 연다 (전량)
pipeline round start C002 INCREMENTAL 20261121  # 회차를 손으로 연다 (증분, 오늘부터 7일 전까지만)
pipeline round start I0300 FULL 2025         # 회차를 손으로 연다 (생산실적은 연도 필요)
pipeline round step 3                        # 3번 회차를 손으로 이어 받는다 (밤 제한 무시, 호출 한도는 지킴, 최대 100회)
pipeline round step 3 --max 10               # 최대 10회만 이어 받는다
pipeline round cancel 3                      # 3번 회차를 취소하고 받은 행을 지운다
```

## 계산 · CSV

```powershell
pipeline verify-hash product_master.csv      # CSV 의 content_hash 를 다시 계산해 파일 값과 같은지 확인 (불일치 0행이어야 함)
pipeline build                               # 34컬럼 계산 → CSV (미구현, 서버에서는 cron 하루 1회 07시 전후)
pipeline build --again                       # 오늘 파일이 이미 있으면 _2 를 붙여 다시 만든다 (미구현)
pipeline categorize export                   # 분류가 안 정해진 새 제품 목록을 CSV 로 내보낸다 (미구현)
pipeline categorize import 파일.csv          # 분류를 채운 CSV 를 넣는다 (미구현)
```

## 상태 저장

상태(회차, 호출 기록, 원본, 묶음, 분류표)는 전부 `{DATA_DIR}/state.db`(SQLite) 하나에 있다. 표 설명은 `src/nuti_pipeline/state/db.py` 맨 위에 있다.