"""
결과 엑셀 생성 (시트: 요약 / 상세 / 검색결과 / 실행정보).

LLM OFF 모드에서는 LLM 지표 열·행을 만들지 않는다 (지표 목록 자체에 LLM 지표가 없다).
"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from eval.ragas_catalog import UNSUPPORTED_RAGAS_METRICS
from eval.scoring import GROUP_LLM, KIND_FLAG, KIND_SCORE, MetricDef, ScoreOutput, summarize

SHEET_SUMMARY = "요약"
SHEET_DETAIL = "상세"
SHEET_DOCUMENTS = "검색결과"
SHEET_RUN_INFO = "실행정보"

NOT_AVAILABLE = "N/A"

# 사람 검수 열
REVIEW_RESULT = "검수결과"
REVIEW_COMMENT = "검수의견"
REVIEW_CHOICES = ("정답", "부분정답", "오답")

# 조건부 서식 기준: 점수형 지표는 이 값 미만이면 강조
LOW_SCORE_THRESHOLD = 0.5

# 점수 표시 형식 (셀 값은 0~1 숫자, 화면에는 50.0% 로 보인다)
PERCENT_FORMAT = "0.0%"

# 실행정보 시트의 지표 정의 표
METRIC_TABLE_TITLES = ("지표", "RAGAS 지표", "정의", "분류", "LLM 미사용 시 계산 불가", "이번 결과")
CHECK_MARK = "✔"
NOT_RAGAS = "— (자체 규칙 지표)"

# 엑셀 셀 최대 글자 수(32,767)보다 조금 작게 자른다
_MAX_CELL_LENGTH = 32000
_TRUNCATED_MARK = " …(잘림)"

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="4F81BD")
_LLM_HEADER_FILL = PatternFill("solid", fgColor="8064A2")
_REVIEW_HEADER_FILL = PatternFill("solid", fgColor="E46C0A")
_LOW_FILL = PatternFill("solid", bgColor="F8CBAD")
_LLM_REQUIRED_FILL = PatternFill("solid", fgColor="E4DFEC")
_SUBHEADER_FILL = PatternFill("solid", fgColor="DCE6F1")
_SECTION_FONT = Font(bold=True, size=12)
_WRAP_TOP = Alignment(wrap_text=True, vertical="top")
_TOP = Alignment(vertical="top")
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

# 한계 사항 (실행정보 시트)
LIMITATIONS = (
    "근거(retrieved_contexts)는 응답 documents 중 used_yn=Y 인 청크의 content 다. "
    "판례(PREC)는 서버가 DB 에서 보강한 판결요지·주문·이유 텍스트가 응답에 없어 근거가 일부 누락된다 "
    "→ PREC 의 근거·환각·Faithfulness 지표는 실제보다 낮게 나올 수 있다.",
    "응답에 chunk id 가 없어 출처 일치는 '문서명 + 목차' 문자열로 판단한다. "
    "문서명은 완전 일치(공백·대소문자 무시), 목차는 전체 경로 또는 경로의 한 단계와 일치해야 한다. "
    "골든셋의 기대출처 표기가 검색엔진 저장값과 다르면 불일치로 나온다.",
    "문자열 기반 지표(근거 Recall/Precision(문자열), 답변 유사도)는 글자 단위 유사도라 "
    "같은 뜻을 다른 표현으로 쓰면 낮게 나온다.",
    "환각 인용 수는 조문(제N조, 제N조의M)과 사건번호 형식만 검사한다. "
    "그 밖의 수치·사실 오류는 잡지 못한다.",
    "fallback_yn=Y(관련 자료 없음) 건은 근거가 없어 근거·환각·이유 지표를 N/A 로 둔다. "
    "Hit@k·MRR 은 관련 문서 목록(documents) 전체 기준이라 fallback 건도 계산한다.",
    "기대값이 비어 있거나 계산에 실패한 지표는 N/A 이며 평균에서 제외한다. "
    "평균을 볼 때는 '채점 건수'를 함께 확인할 것.",
    "git commit 은 평가 도구(rag-eval) 저장소의 값이다. 평가 대상 API 서버의 버전은 기록되지 않는다.",
)
LLM_LIMITATIONS = (
    "LLM 지표는 Judge 모델의 판단이라 실행할 때마다 조금씩 달라질 수 있다. "
    "Judge 가 답변 모델과 같은 모델이면 자기 답변을 후하게 채점할 수 있다.",
    "RAGAS 채점 프롬프트는 영어다 (한국어 변환 미적용). "
    "Judge 가 채점 JSON 형식을 못 맞추면 해당 지표는 N/A 가 되고 '실패(NaN) 건수'에 집계된다.",
)


def write_report(path: Path | str, output: ScoreOutput, defs: list[MetricDef], all_defs: list[MetricDef],
                 run_info: dict) -> Path:
    """
    결과 엑셀을 만든다.

    defs: 이번 실행에서 계산한 지표 (요약·상세 시트의 행·열)
    all_defs: 이 도구가 계산할 수 있는 모든 지표 (실행정보 시트의 지표 정의 표)
    run_info: 실행정보 시트에 그대로 적을 {항목: 값} (순서 유지).
    """
    path = Path(path)
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = SHEET_SUMMARY
    _build_summary(summary_sheet, output, defs)
    _build_detail(workbook.create_sheet(SHEET_DETAIL), output, defs)
    _build_documents(workbook.create_sheet(SHEET_DOCUMENTS), output)
    _build_run_info(workbook.create_sheet(SHEET_RUN_INFO), defs, all_defs, run_info)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


# ── 셀 쓰기 공통 ──

def _put(sheet, row: int, column: int, value, alignment: Alignment | None = _TOP):
    """셀에 값을 쓴다. 문자열은 엑셀이 못 받는 제어문자 제거·길이 제한·수식 해석 방지를 적용한다."""
    cell = sheet.cell(row=row, column=column)
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
        if len(value) > _MAX_CELL_LENGTH:
            value = value[:_MAX_CELL_LENGTH] + _TRUNCATED_MARK
        cell.value = value
        cell.data_type = "s"   # '=' 로 시작하는 답변이 수식으로 해석되지 않게 한다
    else:
        cell.value = value
    if alignment is not None:
        cell.alignment = alignment
    return cell


def _header(sheet, row: int, titles: list[str], fills: list[PatternFill] | None = None) -> None:
    for index, title in enumerate(titles, start=1):
        cell = _put(sheet, row, index, title, _CENTER)
        cell.font = _HEADER_FONT
        cell.fill = fills[index - 1] if fills else _HEADER_FILL


def _score(value: float | None):
    return NOT_AVAILABLE if value is None else round(value, 4)


def _put_metric(sheet, row: int, column: int, value: float | None, definition: MetricDef, *, average: bool):
    """
    지표 값을 쓴다. 셀 값은 0~1 숫자 그대로 두고 표시 형식만 퍼센트로 바꾼다 (정렬·필터·조건부 서식 유지).

    - 점수형(0~1): 항상 퍼센트
    - 여부형(1/0): 건별 값은 1/0 그대로, 평균(= 해당 건의 비율)은 퍼센트
    - 개수형: 숫자 그대로
    """
    cell = _put(sheet, row, column, _score(value))
    if value is not None and (definition.kind == KIND_SCORE or (average and definition.kind == KIND_FLAG)):
        cell.number_format = PERCENT_FORMAT
    return cell


def _yes_no(value: bool) -> str:
    return "Y" if value else "N"


def _set_widths(sheet, widths: list[float]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _highlight_low(sheet, column: int, first_row: int, last_row: int, definition: MetricDef) -> None:
    """낮은 점수 강조. 높을수록 좋은 지표는 기준 미만, 낮을수록 좋은 지표는 0 초과를 칠한다."""
    if last_row < first_row:
        return
    letter = get_column_letter(column)
    cell = f"{letter}{first_row}"   # 범위의 첫 칸 기준 상대 참조
    if definition.higher_is_better:
        threshold = LOW_SCORE_THRESHOLD if definition.kind == KIND_SCORE else 1
        condition = f"{cell}<{threshold}"
    else:
        condition = f"{cell}>0"
    # 엑셀은 문자열을 숫자보다 크다고 보므로 'N/A' 가 걸리지 않게 숫자인 칸만 검사한다
    rule = FormulaRule(formula=[f"AND(ISNUMBER({cell}),{condition})"], fill=_LOW_FILL)
    sheet.conditional_formatting.add(f"{letter}{first_row}:{letter}{last_row}", rule)


# ── 요약 ──

def _build_summary(sheet, output: ScoreOutput, defs: list[MetricDef]) -> None:
    summaries = summarize(output.results, defs)
    names = [summary.name for summary in summaries]

    _put(sheet, 1, 1, "건수").font = _SECTION_FONT
    _header(sheet, 2, ["구분"] + names)
    count_rows = (
        ("골든셋 건수", [summary.count for summary in summaries]),
        ("오류 건수 (API 오류·타임아웃)", [summary.errors for summary in summaries]),
        ("fallback 건수 (관련 자료 없음 응답)", [summary.fallbacks for summary in summaries]),
        ("평균 응답시간(초)", [None if s.mean_elapsed is None else round(s.mean_elapsed, 2) for s in summaries]),
    )
    row = 3
    for title, values in count_rows:
        _put(sheet, row, 1, title)
        for index, value in enumerate(values, start=2):
            _put(sheet, row, index, NOT_AVAILABLE if value is None else value)
        row += 1

    row += 1
    _put(sheet, row, 1, "지표별 평균 (N/A 제외)").font = _SECTION_FONT
    row += 1
    has_llm = any(definition.group == GROUP_LLM for definition in defs)
    titles = ["지표", "분류"] + [f"{name} 평균" for name in names] + ["채점 건수(전체)"]
    if has_llm:
        titles += ["LLM 채점 시도", "LLM 실패(NaN) 건수"]
    _header(sheet, row, titles)
    first_metric_row = row + 1
    for definition in defs:
        row += 1
        _put(sheet, row, 1, definition.label)
        _put(sheet, row, 2, definition.group)
        for index, summary in enumerate(summaries, start=3):
            _put_metric(sheet, row, index, summary.means[definition.key], definition, average=True)
            _highlight_low(sheet, index, row, row, definition)
        column = 3 + len(summaries)
        _put(sheet, row, column, summaries[0].scored[definition.key])
        if has_llm and definition.group == GROUP_LLM:
            _put(sheet, row, column + 1, output.llm_attempts.get(definition.key, 0))
            _put(sheet, row, column + 2, output.llm_failures.get(definition.key, 0))

    row += 2
    _put(sheet, row, 1, "※ 지표 정의와 한계 사항은 '실행정보' 시트 참고. "
                        "색칠된 칸 = 낮은 점수(점수형 50% 미만, 여부형 100% 미만) 또는 검출·환각 있음.")
    _set_widths(sheet, [34, 8] + [14] * len(summaries) + [16, 14, 18])
    sheet.freeze_panes = sheet.cell(row=first_metric_row, column=3)


# ── 상세 ──

def _build_detail(sheet, output: ScoreOutput, defs: list[MetricDef]) -> None:
    has_llm = any(definition.group == GROUP_LLM for definition in defs)
    input_titles = ["ID", "검색대상", "질문", "모범답안", "기대출처", "필수키워드", "금지키워드", "결과없음기대"]
    output_titles = ["답변", "이유", "fallback_yn", "출처"]
    metric_titles = [definition.label for definition in defs]
    tail_titles = ["누락 필수키워드", "검출 금지키워드", "환각 인용 목록", "오류코드"]
    if has_llm:
        tail_titles.append("Judge 오류")
    tail_titles += ["응답시간(초)", REVIEW_RESULT, REVIEW_COMMENT]
    titles = input_titles + output_titles + metric_titles + tail_titles

    fills = (
        [_HEADER_FILL] * (len(input_titles) + len(output_titles))
        + [_LLM_HEADER_FILL if definition.group == GROUP_LLM else _HEADER_FILL for definition in defs]
        + [_HEADER_FILL] * (len(tail_titles) - 2)
        + [_REVIEW_HEADER_FILL] * 2
    )
    _header(sheet, 1, titles, fills)

    first_metric_column = len(input_titles) + len(output_titles) + 1
    for row, result in enumerate(output.results, start=2):
        item = result.item
        response = result.response
        sources = "\n".join(str(source.get("label") or "") for source in response.get("sources") or [])
        expected = "\n".join(
            f"{source.doc_name} | {source.section_path}" if source.section_path else source.doc_name
            for source in item.expected_sources
        )
        values = [
            item.id, item.doc_type, item.question, item.reference, expected,
            ", ".join(item.required_keywords), ", ".join(item.forbidden_keywords), _yes_no(item.expect_fallback),
            str(response.get("answer") or ""), str(response.get("reason") or ""),
            str(response.get("fallback_yn") or ""), sources,
        ]
        metric_start = len(values) + 1
        values += [None] * len(defs)   # 지표 칸은 아래에서 표시 형식과 함께 쓴다
        values += [
            ", ".join(result.missing_keywords), ", ".join(result.forbidden_hits),
            ", ".join(result.hallucinated), result.error_code,
        ]
        if has_llm:
            values.append("\n".join(result.judge_errors))
        values += [result.elapsed_seconds, "", ""]   # 검수 열은 비워서 출력
        for column, value in enumerate(values, start=1):
            _put(sheet, row, column, value, _WRAP_TOP)
        for offset, definition in enumerate(defs):
            _put_metric(sheet, row, metric_start + offset, result.metrics.get(definition.key), definition,
                        average=False)

    last_row = len(output.results) + 1
    for offset, definition in enumerate(defs):
        _highlight_low(sheet, first_metric_column + offset, 2, last_row, definition)

    # 검수결과 드롭다운 (넉넉히 데이터 행까지)
    review_column = get_column_letter(titles.index(REVIEW_RESULT) + 1)
    validation = DataValidation(type="list", formula1='"' + ",".join(REVIEW_CHOICES) + '"', allow_blank=True)
    sheet.add_data_validation(validation)
    validation.add(f"{review_column}2:{review_column}{max(last_row, 2)}")

    widths = [12, 10, 40, 40, 28, 22, 22, 10, 60, 45, 10, 32] + [11] * len(defs) + [22, 22, 22, 18]
    if has_llm:
        widths.append(30)
    widths += [10, 12, 40]
    _set_widths(sheet, widths)
    sheet.row_dimensions[1].height = 42
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(titles))}{last_row}"


# ── 검색결과 ──

def _build_documents(sheet, output: ScoreOutput) -> None:
    titles = ["ID", "검색대상", "질문", "순위", "문서명", "목차", "score", "rerank_score", "used_yn",
              "기대출처 일치", "content"]
    _header(sheet, 1, titles)
    row = 1
    for result in output.results:
        has_expected = bool(result.item.expected_sources)
        for rank, document in enumerate(result.documents, start=1):
            row += 1
            matched = NOT_AVAILABLE if not has_expected else _yes_no(rank in result.matched_ranks)
            values = [
                result.item.id, result.item.doc_type, result.item.question, rank,
                str(document.get("doc_name") or ""), str(document.get("section_path") or ""),
                document.get("score"), document.get("rerank_score"), str(document.get("used_yn") or ""),
                matched, str(document.get("content") or ""),
            ]
            for column, value in enumerate(values, start=1):
                _put(sheet, row, column, value, _WRAP_TOP if column in (3, 11) else _TOP)
    _set_widths(sheet, [12, 10, 36, 6, 28, 30, 10, 12, 9, 12, 100])
    sheet.freeze_panes = "E2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(titles))}{max(row, 1)}"


# ── 실행정보 ──

def _build_run_info(sheet, defs: list[MetricDef], all_defs: list[MetricDef], run_info: dict) -> None:
    _put(sheet, 1, 1, "실행 정보").font = _SECTION_FONT
    row = 2
    for key, value in run_info.items():
        _put(sheet, row, 1, key).font = Font(bold=True)
        _put(sheet, row, 2, value if isinstance(value, (int, float)) else str(value), _WRAP_TOP)
        row += 1

    row += 1
    row = _build_metric_table(sheet, row, defs, all_defs)

    row += 2
    _put(sheet, row, 1, "한계 사항").font = _SECTION_FONT
    limitations = LIMITATIONS + (LLM_LIMITATIONS if any(d.group == GROUP_LLM for d in defs) else ())
    for number, text in enumerate(limitations, start=1):
        row += 1
        _put(sheet, row, 1, number)
        cell = _put(sheet, row, 2, text, _WRAP_TOP)
        sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=len(METRIC_TABLE_TITLES))
        # 병합 셀은 줄 수에 맞춰 높이가 자동으로 늘지 않으므로 글자 수로 어림해 잡는다
        sheet.row_dimensions[row].height = 16 * (1 + len(cell.value) // 110)
    _set_widths(sheet, [28, 44, 70, 14, 14, 44])


def _build_metric_table(sheet, row: int, defs: list[MetricDef], all_defs: list[MetricDef]) -> int:
    """
    지표 정의 표를 쓰고 마지막으로 쓴 행 번호를 돌려준다.

    ① 이 도구가 계산하는 지표 (LLM OFF 여서 이번 결과에 없는 LLM 지표도 표시)
    ② RAGAS 에 있지만 이 도구가 계산하지 않는 지표 (참고)
    """
    included = {definition.key for definition in defs}

    _put(sheet, row, 1, "지표 정의").font = _SECTION_FONT
    row += 1
    _put(sheet, row, 1, f"※ '{METRIC_TABLE_TITLES[4]}' 에 {CHECK_MARK} 가 있는 지표는 채점용 LLM(Judge)이 있어야 계산된다. "
                        "LLM OFF 모드 결과에는 나오지 않는다.")
    row += 1
    _header(sheet, row, list(METRIC_TABLE_TITLES))

    def write(values: list[str], needs_llm: bool) -> None:
        for column, value in enumerate(values, start=1):
            cell = _put(sheet, row, column, value, _CENTER if column == 5 else _WRAP_TOP)
            if needs_llm and column == 5:
                cell.fill = _LLM_REQUIRED_FILL
                cell.font = Font(bold=True)

    def subheader(title: str) -> None:
        cell = _put(sheet, row, 1, title)
        cell.font = Font(bold=True)
        for column in range(1, len(METRIC_TABLE_TITLES) + 1):
            sheet.cell(row=row, column=column).fill = _SUBHEADER_FILL

    row += 1
    subheader("① 이 도구가 계산하는 지표")
    for definition in all_defs:
        row += 1
        needs_llm = definition.group == GROUP_LLM
        status = "포함" if definition.key in included else "미포함 — LLM OFF 모드로 실행"
        write([definition.label, definition.ragas_name or NOT_RAGAS, definition.description, definition.category,
               CHECK_MARK if needs_llm else "", status], needs_llm)

    row += 1
    subheader("② RAGAS 에 있지만 이 도구가 계산하지 않는 지표 (참고)")
    for info in UNSUPPORTED_RAGAS_METRICS:
        row += 1
        write([info.label, info.ragas_name, info.description, info.category,
               CHECK_MARK if info.needs_llm else "", f"미지원 — {info.reason}"], info.needs_llm)
    return row
