# rag-eval

RAG 질의응답 API(wisePolice Agent API)의 품질을 **골든셋**으로 정량 평가하는 개발·운영 보조 도구.

담당자가 엑셀 템플릿에 골든셋을 작성하면, 평가 도구가 API(`POST /query/sync`)를 호출해 채점하고 결과를 엑셀로 내보낸다.
API 는 HTTP 로만 호출하므로 마스킹·입력 정제·DB 프롬프트까지 운영 경로 그대로 평가된다.

## 설치

Python 3.12 기준.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # EVAL_API_URL 등을 환경에 맞게 수정
```

`requirements.txt` 의 `langchain-community==0.3.31` 고정은 지우지 말 것
(0.4 이상에서는 `import ragas` 가 실패한다).

## 폐쇄망 반입·설치 (Windows / Linux)

인터넷이 되는 PC 에서 설치 파일(wheel)을 받아 두고, 폐쇄망에는 소스와 함께 복사한다.
`requirements.lock.txt` 는 전이 의존성까지 버전을 고정한 목록이라 두 OS 에 같은 버전이 설치된다.
`requirements.txt` 를 바꿨다면 새 venv 에 설치해 테스트한 뒤 `pip freeze > requirements.lock.txt` 로 다시 만든다.

**1. 인터넷 PC 에서 설치 파일 받기** (Python 3.12, Linux 는 x86_64 기준 — 어느 OS 의 PC 에서 받아도 된다)

```powershell
# Windows 용 → wheelhouse-win/
pip download --only-binary=:all: --no-deps -r requirements.lock.txt -d wheelhouse-win

# Linux 용 → wheelhouse-linux/
pip download --only-binary=:all: --no-deps -r requirements.lock.txt -d wheelhouse-linux `
  --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 `
  --platform manylinux_2_5_x86_64 --platform manylinux1_x86_64 `
  --python-version 3.12 --implementation cp --abi cp312 --abi abi3 --abi none
```

Linux 는 `--platform` 을 위처럼 여러 개 줘야 한다(하나만 주면 해석 충돌이 난다).
Linux 용에서 요구하는 glibc 는 최대 2.28 이라 RHEL/Rocky 8 계열에서 설치된다.

**2. 폐쇄망으로 복사할 것**: `eval/`, `tests/`, `requirements.txt`, `requirements.lock.txt`, `.env.example`,
`README.md`, 쓰는 OS 의 `wheelhouse-*/` 폴더, 골든셋 엑셀. (`.venv/`, `eval/results/`, `.env` 는 제외)

**3. 폐쇄망에서 설치** — 모든 설치에 `--no-index` 를 붙여 인터넷을 찾지 않게 한다.

```powershell
# Windows (Python 3.12 필요)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse-win -r requirements.lock.txt
copy .env.example .env
```

```bash
# Linux (python3.12 필요)
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse-linux -r requirements.lock.txt
cp .env.example .env
```

**4. 확인**: `python -m pytest -q` (실제 API·LLM 호출 없이 통과해야 한다), `python -m eval.run template --out t.xlsx`.

주의
- **Python 버전**: 설치 파일은 3.12(cp312)용이다. 3.11 등 다른 버전이면 `--python-version`·`--abi` 를 바꿔 다시 받아야 한다
  (RHEL 8.8 기본 Python 은 3.6/3.9 라 3.12 를 따로 설치해야 한다).
- Linux CPU 가 aarch64 이면 `--platform` 을 `manylinux_2_28_aarch64` 등으로 바꿔 받아야 한다.
- 폐쇄망에서는 외부 호출이 없다. 평가가 접속하는 곳은 `EVAL_API_URL`(평가 대상 API)과
  LLM ON 일 때의 `EVAL_JUDGE_BASE_URL` 뿐이다 (ragas 사용 통계 전송은 코드에서 꺼 두었다).

## 사용법

```powershell
# 1) 빈 템플릿 생성 → 골든셋 작성 (작성법은 템플릿의 '작성가이드' 시트)
python -m eval.run template --out golden_template.xlsx

# 2) 평가: API 호출 → 채점 → 결과 엑셀
python -m eval.run evaluate --input 골든셋.xlsx --no-llm
python -m eval.run evaluate --input 골든셋.xlsx --llm --api http://10.0.0.5:8000 --concurrency 1

# 3) 재채점: 저장된 수집 결과를 API 재호출 없이 다시 채점 (예: NOLLM 결과를 LLM 으로)
python -m eval.run rescore --raw eval/results/<run_id>/raw.jsonl --input 골든셋.xlsx --llm
```

- 평가 전에 대상 API 서버가 떠 있어야 한다 (`EVAL_API_URL`, 기본 `http://localhost:8000`).
- 결과는 `eval/results/<run_id>/` 에 생긴다 (git 제외).
  - `raw.jsonl` — API 응답 원본 (1줄 = 골든셋 1건), `meta.json` — 수집 정보
  - `평가결과_<run_id>_NOLLM.xlsx` 또는 `평가결과_<run_id>_LLM.xlsx`
- 골든셋에 오류(필수값 누락, 검색대상 오류, ID 중복, 기대출처 개수 불일치 등)가 있으면
  행 번호와 함께 한 번에 보고하고 API 호출 없이 중단한다.
- API 오류·타임아웃이 나도 전체를 멈추지 않는다. 해당 건은 `오류코드`를 기록하고 지표를 N/A 로 둔다.
- 골든셋 예시: `golden_example.xlsx`

## 평가 모드

| | LLM OFF (기본, `--no-llm`) | LLM ON (`--llm`) |
|---|---|---|
| 지표 | 공통 지표 | 공통 지표 + LLM 채점 지표 4개 |
| LLM 호출 | 없음 | 채점용 LLM(Judge) 호출 |
| 필요한 설정 | `EVAL_API_URL` | + `EVAL_JUDGE_BASE_URL`, `EVAL_JUDGE_MODEL` (OpenAI 호환 서버) |
| 속도 | 빠름 | 건당 Judge 호출 여러 번 |

- 모드는 `.env` 의 `EVAL_USE_LLM` 으로 고르고, CLI 의 `--llm` / `--no-llm` 이 있으면 그것이 우선한다.
- LLM ON 인데 Judge 설정이 비어 있으면 API 호출을 시작하기 전에 중단한다.
- LLM OFF 에서는 Judge 가 만들어지지 않으며 Judge 설정이 비어 있어도 된다.
- 사내 LLM 서버 보호를 위해 API 동시 호출 수(`EVAL_API_CONCURRENCY`)와
  Judge 동시 호출 수(`EVAL_JUDGE_CONCURRENCY`)를 각각 제한한다 (기본 2).

## 골든셋 작성

`골든셋` 시트에 1행 = 1건. 필수는 **ID / 검색대상 / 질문** 셋뿐이고, 나머지는 적은 만큼 지표가 계산된다.
비워 둔 기대값에 해당하는 지표는 0점이 아니라 **N/A** 가 되어 평균에서 빠진다.

| 열 | 적으면 계산되는 지표 |
|---|---|
| 모범답안 | 답변 유사도, LLM 근거 Recall / Precision, FactualCorrectness |
| 정답근거 (여러 건은 `---` 줄로 구분) | 근거 Recall / Precision(문자열) |
| 기대출처_문서명 / 기대출처_목차 | Hit@k, MRR, 출처 Recall / Precision(ID) |
| 필수키워드 / 금지키워드 (쉼표 구분) | 필수키워드 포함률 / 금지키워드 검출 |
| 결과없음기대 (Y/N) | Fallback 일치 |
| 이전질문 / 이전답변 | 지표 없음. MANUAL 의 후속 질문 평가용으로 API 에 history 로 전달 |

- 기대출처는 **검색엔진에 저장된 표기**를 써야 한다. 한 번 평가를 돌린 뒤 결과 엑셀 `검색결과` 시트의
  문서명·목차를 복사해 쓰는 것이 가장 확실하다.
- 목차는 전체 경로(`제2편 각칙 > 제38장 … > 제329조(절도)`)를 적어도 되고,
  한 단계만(`제329조(절도)` 또는 `제329조`) 적어도 일치로 본다. 통째로 비우면 문서명만 비교한다.
- 필수키워드는 결론을 가르는 핵심어(수치·조문·요건)로 쓴다.

## 지표 해석

**공통 지표 (항상 계산)**

| 지표 | 뜻 | 방향 |
|---|---|---|
| 필수키워드 포함률 | 필수키워드 중 답변에 들어 있는 비율 (공백·대소문자 무시) | 높을수록 좋음 |
| 금지키워드 검출 | 금지키워드가 답변에 나오면 1 | 0 이어야 함 |
| 환각 인용 수 | 답변이 인용한 조문·사건번호 중 근거 청크에 없는 것의 개수 | 0 이어야 함 |
| Hit@k | 기대 출처가 검색 결과 상위 k개 안에 있으면 1 | 높을수록 좋음 |
| MRR | 1 / (기대 출처가 처음 나온 순위) | 높을수록 좋음 |
| Fallback 일치 | 결과없음기대와 실제 fallback_yn 이 같으면 1 | 1 이어야 함 |
| 이유 작성 | 응답의 이유(reason)가 비어 있지 않으면 1 | 1 이어야 함 |
| 근거 Recall / Precision(문자열) | 정답근거와 답변 근거 청크의 문자열 유사도 기반 (RAGAS Non-LLM) | 높을수록 좋음 |
| 출처 Recall / Precision(ID) | 기대 출처와 답변 근거 청크의 출처 비교 (RAGAS ID 기반) | 높을수록 좋음 |
| 답변 유사도(참고) | 답변과 모범답안의 글자 단위 유사도. 표현이 다르면 정답도 낮게 나온다 | 참고용 |

**LLM 지표 (LLM ON 일 때만)** — RAGAS 의 Judge 채점

| 지표 | 뜻 |
|---|---|
| Faithfulness | 답변의 주장 중 근거 청크로 뒷받침되는 비율 (낮으면 근거 없는 내용을 지어냄) |
| LLM 근거 Recall | 모범답안의 내용 중 근거 청크에서 찾을 수 있는 비율 (낮으면 검색이 부족) |
| LLM 근거 Precision | 근거 청크 중 유용한 청크가 상위에 있는 정도 (낮으면 불필요한 근거가 섞임) |
| FactualCorrectness | 답변과 모범답안의 사실 일치도 (F1) |

읽는 순서 권장: `요약` 시트에서 평균과 **채점 건수**를 함께 보고 → `상세` 시트에서 색칠된 칸(낮은 점수)을 확인 →
`검색결과` 시트에서 그 건의 검색 순위와 근거 사용 여부(used_yn)를 본다.
검색 지표(Hit@k, 출처 Recall)가 낮으면 검색·재정렬 문제, 검색은 맞는데 답변 지표가 낮으면 프롬프트·LLM 문제로 볼 수 있다.

`상세` 시트 맨 뒤의 `검수결과`(정답/부분정답/오답)·`검수의견`은 사람이 채우는 칸이다.

## 한계

- **판례(PREC) 근거 누락**: 근거는 응답 `documents` 중 `used_yn=Y` 청크의 본문이다. 서버가 DB 에서 보강한
  판결요지·주문·이유 텍스트는 응답에 없어, PREC 의 근거·환각·Faithfulness 지표가 실제보다 낮게 나올 수 있다.
- **출처 일치는 문자열 비교**: 응답에 chunk id 가 없어 `문서명 + 목차`로 판단한다. 표기가 다르면 불일치다.
- **문자열 지표의 한계**: 같은 뜻을 다른 표현으로 쓰면 낮게 나온다. 답변이 표·목록 형식이면 더 낮아진다.
- **환각 인용 검사 범위**: 조문(`제N조`, `제N조의M`)과 사건번호 형식만 검사한다.
- **fallback 건**: `fallback_yn=Y` 건은 근거·환각·이유 지표가 N/A 다.
- **LLM 지표**: Judge 의 판단이라 실행마다 조금 달라질 수 있고, Judge 가 답변 모델과 같으면 점수가 후할 수 있다.
  RAGAS 채점 프롬프트는 영어이며, Judge 가 채점 JSON 형식을 못 맞추면 그 지표는 N/A 가 되고
  `요약` 시트의 "LLM 실패(NaN) 건수"에 집계된다.
- 결과 엑셀의 git commit 은 이 저장소의 값이다. 평가 대상 API 서버의 버전은 기록되지 않는다.

## 구조

```
eval/
 ├── config.py      EVAL_* 설정 로드 (CLI > 환경변수 > .env > 기본값)
 ├── template.py    골든셋 템플릿 정의·생성
 ├── loader.py      엑셀 → GoldenItem (오류는 행 번호와 함께 모아서 보고)
 ├── collect.py     API 호출 → raw.jsonl
 ├── metrics.py     자체 규칙 지표 (순수 함수)
 ├── ragas_eval.py  RAGAS 지표 실행 (Non-LLM 은 항상, LLM 은 LLM ON 일 때만)
 ├── judge.py       Judge LLM 생성 (LLM ON 일 때만 import)
 ├── scoring.py     골든셋 + 수집 결과 → 항목별 지표, 요약
 ├── report.py      결과 엑셀 생성
 └── run.py         CLI
tests/              pytest (실제 API·LLM 호출 없음)
```

테스트: `python -m pytest -q`
