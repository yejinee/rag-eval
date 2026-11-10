"""
평가 모드 테스트 + 가짜 API 로 엑셀 생성까지 도는 통합 테스트. (실제 API·LLM 호출 없음)

- LLM OFF: judge 모듈이 import 되지 않고 ChatOpenAI 가 만들어지지 않는다
  (ragas 가 내부적으로 langchain_openai 를 import 하므로 '모듈 미로드'가 아니라 'Judge 미생성'을 확인한다)
- LLM ON 인데 Judge 설정이 없으면 API 호출 전에 중단한다
- rescore 로 같은 수집 결과를 OFF → ON 으로 다시 채점한다 (API 재호출 없음)
"""
import json
import math
import sys

import httpx
from openpyxl import load_workbook

from eval import run
from eval.report import NOT_AVAILABLE, SHEET_DETAIL, SHEET_DOCUMENTS, SHEET_RUN_INFO, SHEET_SUMMARY
from tests.conftest import write_golden

GOLDEN_ROWS = [
    {
        "ID": "LAW-001", "검색대상": "LAW", "질문": "음주측정을 거부하면?",
        "모범답안": "도로교통법 제148조의2에 따라 1년 이상 5년 이하의 징역에 처한다.",
        "정답근거": "제148조의2(벌칙) 측정에 응하지 아니하는 사람은 1년 이상 5년 이하의 징역",
        "기대출처_문서명": "도로교통법", "기대출처_목차": "제148조의2",
        "필수키워드": "1년 이상 5년 이하, 벌금", "금지키워드": "과태료",
    },
    {"ID": "LAW-002", "검색대상": "LAW", "질문": "서버 오류가 나는 질문", "모범답안": "답"},
    {"ID": "NA-001", "검색대상": "MANUAL", "질문": "구내식당 메뉴는?", "결과없음기대": "Y"},
    {"ID": "LAW-003", "검색대상": "LAW", "질문": "타임아웃 나는 질문"},
]

DOCUMENTS = [
    {"doc_type": "LAW", "doc_name": "형법", "section_path": "제1조", "content": "제1조(범죄의 성립과 처벌)",
     "score": 0.71, "rerank_score": 0.2, "used_yn": "N"},
    {"doc_type": "LAW", "doc_name": "도로교통법", "section_path": "제148조의2",
     "content": "제148조의2(벌칙) 측정에 응하지 아니하는 사람은 1년 이상 5년 이하의 징역",
     "score": 0.93, "rerank_score": 0.98, "used_yn": "Y"},
    {"doc_type": "LAW", "doc_name": "도로교통법", "section_path": "제44조",
     "content": "제44조(술에 취한 상태에서의 운전 금지)", "score": 0.88, "rerank_score": 0.75, "used_yn": "Y"},
]


def _handler(question: str, payload: dict):
    if "음주측정" in question:
        return 200, {
            "request_id": "r1", "doc_type": "LAW",
            "answer": "=제148조의2에 따라 1년이상 5년 이하의 징역에 처하며, 제999조도 적용됩니다.",
            "reason": "제148조의2 벌칙 조항에 근거함",
            "sources": [{"label": "도로교통법 > 제148조의2", "name": "도로교통법",
                         "section_path": "제148조의2", "page": None, "source_url": ""}],
            "documents": DOCUMENTS, "fallback_yn": "N", "model": "answer-model",
        }
    if "서버 오류" in question:
        return 504, {"request_id": "r2", "error_code": "LLM_TIMEOUT", "message": "응답이 지연되고 있습니다"}
    if "구내식당" in question:
        return 200, {"request_id": "r3", "doc_type": "MANUAL", "answer": "관련 자료를 찾지 못했습니다.",
                     "reason": "", "sources": [], "documents": [], "fallback_yn": "Y", "model": "answer-model"}
    raise httpx.ReadTimeout("시간 초과")


def _sheet_rows(sheet) -> list[dict]:
    """1행을 헤더로 보고 {헤더: 값} 목록으로 읽는다."""
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def _evaluate(tmp_path, *extra: str) -> int:
    golden = write_golden(tmp_path, GOLDEN_ROWS)
    return run.main(["evaluate", "--input", str(golden), "--api", "http://test",
                     "--results-dir", str(tmp_path / "results"), *extra])


def _only_run_dir(tmp_path):
    (run_dir,) = (tmp_path / "results").iterdir()
    return run_dir


def test_OFF_모드_가짜_API로_엑셀_생성까지(tmp_path, clean_env, fake_api, monkeypatch):
    calls = fake_api(_handler)
    sys.modules.pop("eval.judge", None)

    # Judge 가 만들어지면 바로 실패하게 한다
    import langchain_openai

    def forbidden(*args, **kwargs):
        raise AssertionError("LLM OFF 모드에서 ChatOpenAI 가 생성되었습니다")

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", forbidden)

    assert _evaluate(tmp_path, "--no-llm") == 0

    # OFF 모드: judge 모듈은 import 되지 않는다 (Judge 설정이 전부 비어 있어도 정상 동작)
    assert "eval.judge" not in sys.modules
    assert len(calls) == 4
    assert calls[0] == {"question": "음주측정을 거부하면?", "doc_type": "LAW", "history": []}

    run_dir = _only_run_dir(tmp_path)
    raw_lines = (run_dir / "raw.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 4
    raws = {record["id"]: record for record in map(json.loads, raw_lines)}
    assert raws["LAW-002"]["error_code"] == "LLM_TIMEOUT"
    assert raws["LAW-003"]["error_code"] == "EVAL_TIMEOUT"

    (report,) = run_dir.glob("평가결과_*_NOLLM.xlsx")
    workbook = load_workbook(report)
    assert workbook.sheetnames == [SHEET_SUMMARY, SHEET_DETAIL, SHEET_DOCUMENTS, SHEET_RUN_INFO]

    # ── 상세 ──
    detail_sheet = workbook[SHEET_DETAIL]
    headers = [cell.value for cell in detail_sheet[1]]
    assert headers[-2:] == ["검수결과", "검수의견"]
    assert "Faithfulness" not in headers and "FactualCorrectness" not in headers and "Judge 오류" not in headers
    assert len(detail_sheet.data_validations.dataValidation) == 1      # 검수결과 드롭다운
    assert detail_sheet.conditional_formatting                          # 낮은 점수 강조

    detail = {row["ID"]: row for row in _sheet_rows(detail_sheet)}
    assert list(detail) == ["LAW-001", "LAW-002", "NA-001", "LAW-003"]   # 골든셋 순서 유지

    # 점수형 지표는 퍼센트로 표시(값은 0~1 숫자 그대로), 여부형·개수형 건별 값은 숫자 그대로
    formats = {header: detail_sheet.cell(row=2, column=index).number_format
               for index, header in enumerate(headers, start=1)}
    assert formats["필수키워드 포함률"] == "0.0%" and formats["MRR"] == "0.0%"
    assert formats["Hit@3"] == "General" and formats["환각 인용 수"] == "General"

    law = detail["LAW-001"]
    assert law["답변"].startswith("=제148조의2")           # '=' 로 시작해도 문자열로 저장
    assert law["필수키워드 포함률"] == 0.5
    assert law["누락 필수키워드"] == "벌금"
    assert law["금지키워드 검출"] == 0
    assert law["환각 인용 수"] == 1
    assert law["환각 인용 목록"] == "제999조"
    assert law["Hit@3"] == 1 and law["Hit@5"] == 1
    assert law["MRR"] == 0.5                                # 기대 출처가 2순위
    assert law["Fallback 일치"] == 1
    assert law["이유 작성"] == 1
    assert law["근거 Recall(문자열)"] == 1
    assert law["출처 Recall(ID)"] == 1
    assert law["출처 Precision(ID)"] == 0.5                 # 근거 2건 중 1건이 기대 출처
    assert 0 < law["답변 유사도(참고)"] < 1
    assert law["검수결과"] is None and law["검수의견"] is None
    assert law["오류코드"] is None

    # API 오류 건: error_code 기록, 지표는 전부 N/A
    failed = detail["LAW-002"]
    assert failed["오류코드"] == "LLM_TIMEOUT"
    assert failed["필수키워드 포함률"] == NOT_AVAILABLE and failed["Fallback 일치"] == NOT_AVAILABLE
    assert detail["LAW-003"]["오류코드"] == "EVAL_TIMEOUT"

    # fallback 건: 근거·환각·이유 지표는 N/A, fallback 일치는 채점
    fallback = detail["NA-001"]
    assert fallback["Fallback 일치"] == 1
    assert fallback["환각 인용 수"] == NOT_AVAILABLE
    assert fallback["이유 작성"] == NOT_AVAILABLE
    assert fallback["Hit@3"] == NOT_AVAILABLE              # 기대 출처 없음

    # ── 검색결과 ──
    documents = _sheet_rows(workbook[SHEET_DOCUMENTS])
    assert [(row["ID"], row["순위"], row["기대출처 일치"]) for row in documents] == [
        ("LAW-001", 1, "N"), ("LAW-001", 2, "Y"), ("LAW-001", 3, "N"),
    ]

    # ── 요약 ──
    summary = list(workbook[SHEET_SUMMARY].iter_rows(values_only=True))
    assert summary[1][:4] == ("구분", "전체", "LAW", "MANUAL")
    assert summary[2][:4] == ("골든셋 건수", 4, 3, 1)
    assert summary[3][:4] == ("오류 건수 (API 오류·타임아웃)", 2, 2, 0)
    by_label = {row[0]: row for row in summary if row[0]}
    assert by_label["필수키워드 포함률"][2] == 0.5           # N/A 는 평균에서 제외
    assert by_label["Fallback 일치"][2] == 1
    assert "Faithfulness" not in by_label

    # ── 실행정보 ──
    info = {row[0]: row[1] for row in workbook[SHEET_RUN_INFO].iter_rows(values_only=True) if row[0]}
    assert info["평가 모드"].startswith("NOLLM")
    assert info["API URL"] == "http://test"
    assert info["답변 모델 (API 응답 model)"] == "answer-model"
    assert info["입력 파일"] == "golden.xlsx"

    # 지표 정의 표: 지표 / RAGAS 지표 / 정의 / 분류 / LLM 미사용 시 계산 불가 / 이번 결과
    table = {row[0]: row for row in workbook[SHEET_RUN_INFO].iter_rows(values_only=True) if row[0]}
    assert table["지표"][:6] == ("지표", "RAGAS 지표", "정의", "분류", "LLM 미사용 시 계산 불가", "이번 결과")
    assert table["필수키워드 포함률"][1] == "— (자체 규칙 지표)" and table["필수키워드 포함률"][4] is None
    assert table["근거 Recall(문자열)"][1] == "NonLLMContextRecall" and table["근거 Recall(문자열)"][5] == "포함"
    # LLM 지표는 OFF 모드 결과에 없어도 표에는 나오고, 계산 불가 표시가 붙는다
    assert table["Faithfulness"][4] == "✔" and table["Faithfulness"][5].startswith("미포함")
    # 이 도구가 계산하지 않는 RAGAS 지표도 참고용으로 싣는다
    assert table["답변 관련성"][1].startswith("ResponseRelevancy") and table["답변 관련성"][4] == "✔"
    assert table["의미 유사도"][4] is None and table["의미 유사도"][5].startswith("미지원")


def test_ON_인데_Judge_설정이_없으면_API_호출_전에_중단(tmp_path, clean_env, fake_api, capsys):
    calls = fake_api(_handler)

    assert _evaluate(tmp_path, "--llm") == run.EXIT_INPUT_ERROR

    assert calls == []                                   # API 를 한 번도 부르지 않았다
    assert not (tmp_path / "results").exists()
    error = capsys.readouterr().err
    assert "EVAL_JUDGE_BASE_URL" in error and "EVAL_JUDGE_MODEL" in error


def test_env_의_EVAL_USE_LLM_보다_CLI_no_llm_이_우선(tmp_path, clean_env, fake_api, monkeypatch):
    fake_api(_handler)
    monkeypatch.setenv("EVAL_USE_LLM", "true")           # Judge 설정은 비어 있다
    assert _evaluate(tmp_path, "--no-llm") == 0
    assert list(_only_run_dir(tmp_path).glob("평가결과_*_NOLLM.xlsx"))


class _FakeMetric:
    """Judge 호출 없이 정해진 결과를 내는 가짜 LLM 지표."""

    def __init__(self, outcome):
        self.outcome = outcome

    async def single_turn_ascore(self, sample):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_rescore_로_OFF_결과를_ON_으로_재채점(tmp_path, clean_env, fake_api, monkeypatch):
    calls = fake_api(_handler)
    assert _evaluate(tmp_path, "--no-llm") == 0
    run_dir = _only_run_dir(tmp_path)
    calls.clear()

    from eval import ragas_eval

    monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "http://judge/v1")
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "judge-model")
    monkeypatch.setattr(ragas_eval, "_build_llm_metrics", lambda config: {
        ragas_eval.FAITHFULNESS: _FakeMetric(0.8),
        ragas_eval.LLM_CONTEXT_RECALL: _FakeMetric(math.nan),                 # 채점 JSON 해석 실패
        ragas_eval.LLM_CONTEXT_PRECISION: _FakeMetric(TimeoutError("Judge 응답 없음")),
        ragas_eval.FACTUAL_CORRECTNESS: _FakeMetric(0.25),
    })

    golden = tmp_path / "golden.xlsx"
    assert run.main(["rescore", "--raw", str(run_dir / "raw.jsonl"), "--input", str(golden), "--llm"]) == 0

    assert calls == []                                   # API 재호출 없음
    assert len(list(run_dir.glob("평가결과_*_NOLLM.xlsx"))) == 1
    (report,) = run_dir.glob("평가결과_*_LLM.xlsx")
    workbook = load_workbook(report)

    detail = {row["ID"]: row for row in _sheet_rows(workbook[SHEET_DETAIL])}
    law = detail["LAW-001"]
    assert law["Faithfulness"] == 0.8
    assert law["FactualCorrectness"] == 0.25
    assert law["LLM 근거 Recall"] == NOT_AVAILABLE        # NaN → N/A
    assert law["LLM 근거 Precision"] == NOT_AVAILABLE     # Judge 오류 → N/A
    assert "llm_context_recall: NaN" in law["Judge 오류"]
    assert "TimeoutError" in law["Judge 오류"]
    assert law["필수키워드 포함률"] == 0.5                 # 공통 지표는 OFF 와 동일
    # fallback 건·오류 건은 LLM 지표도 N/A
    assert detail["NA-001"]["Faithfulness"] == NOT_AVAILABLE
    assert detail["LAW-002"]["Faithfulness"] == NOT_AVAILABLE

    summary = {row[0]: row for row in workbook[SHEET_SUMMARY].iter_rows(values_only=True) if row[0]}
    header = summary["지표"]
    attempts, failures = header.index("LLM 채점 시도"), header.index("LLM 실패(NaN) 건수")
    assert (summary["Faithfulness"][attempts], summary["Faithfulness"][failures]) == (1, 0)
    assert (summary["LLM 근거 Recall"][attempts], summary["LLM 근거 Recall"][failures]) == (1, 1)
    assert (summary["LLM 근거 Precision"][attempts], summary["LLM 근거 Precision"][failures]) == (1, 1)
    assert summary["LLM 근거 Recall"][2] == NOT_AVAILABLE   # 실패 건은 평균에서 제외

    info = {row[0]: row[1] for row in workbook[SHEET_RUN_INFO].iter_rows(values_only=True) if row[0]}
    assert info["평가 모드"].startswith("LLM")
    assert info["Judge 모델"] == "judge-model"
    table = {row[0]: row for row in workbook[SHEET_RUN_INFO].iter_rows(values_only=True) if row[0]}
    assert table["Faithfulness"][4] == "✔" and table["Faithfulness"][5] == "포함"


def test_Judge_생성은_설정값을_그대로_쓴다(clean_env):
    """Judge 객체 생성만 확인한다 (네트워크 호출 없음)."""
    from eval.config import load_config
    from eval.judge import build_judge

    config = load_config(env={
        "EVAL_USE_LLM": "true", "EVAL_JUDGE_BASE_URL": "http://judge/v1", "EVAL_JUDGE_MODEL": "judge-model",
        "EVAL_JUDGE_TEMPERATURE": "0", "EVAL_JUDGE_TIMEOUT_SECONDS": "30", "EVAL_JUDGE_CONCURRENCY": "3",
    })
    judge = build_judge(config)

    chat = judge.langchain_llm
    assert chat.model_name == "judge-model"
    assert chat.temperature == 0
    assert chat.max_retries == 0
    assert judge.bypass_temperature is True
    assert judge.run_config.max_retries == 2
