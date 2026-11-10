"""
골든셋 엑셀 템플릿 정의 + 빈 템플릿 생성.

열 이름·시트 이름·코드값은 여기 상수가 기준이다 (loader.py 가 이 상수를 가져다 쓴다).
"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

SHEET_GOLDEN = "골든셋"
SHEET_GUIDE = "작성가이드"
SHEET_CODES = "코드값"

# ── 열 이름 ──
COL_ID = "ID"
COL_DOC_TYPE = "검색대상"
COL_QUESTION = "질문"
COL_REFERENCE = "모범답안"
COL_REFERENCE_CONTEXTS = "정답근거"
COL_SOURCE_NAMES = "기대출처_문서명"
COL_SOURCE_SECTIONS = "기대출처_목차"
COL_REQUIRED_KEYWORDS = "필수키워드"
COL_FORBIDDEN_KEYWORDS = "금지키워드"
COL_EXPECT_FALLBACK = "결과없음기대"
COL_PREV_QUESTION = "이전질문"
COL_PREV_ANSWER = "이전답변"
COL_NOTE = "비고"

REQUIRED_COLUMNS = (COL_ID, COL_DOC_TYPE, COL_QUESTION)

# ── 코드값 (드롭다운 원본) ──
DOC_TYPES = ("MANUAL", "LAW", "ADMRUL", "PREC")
YES_NO = ("Y", "N")

# 정답근거 셀 안에서 여러 건을 나누는 구분 줄
CONTEXT_SEPARATOR = "---"

# (열 이름, 필수 여부, 열 너비, 작성법, 예시) — 골든셋 시트의 열 순서
_COLUMN_SPECS = (
    (COL_ID, True, 12, "행마다 고유한 값. 결과 엑셀에서 이 값으로 행을 찾는다.", "MAN-001"),
    (COL_DOC_TYPE, True, 12, "드롭다운에서 선택: MANUAL(매뉴얼) / LAW(법령) / ADMRUL(행정규칙) / PREC(판례)", "LAW"),
    (COL_QUESTION, True, 45, "API 에 그대로 보낼 질문.", "음주측정을 거부하면 어떤 처벌을 받나요?"),
    (COL_REFERENCE, False, 45,
     "사람이 쓴 정답. 문자열 유사도와 LLM 지표(Recall / FactualCorrectness)에 쓴다. "
     "★ LLM 모드로 평가하려면 반드시 작성.",
     "도로교통법 제148조의2 제2항에 따라 1년 이상 5년 이하의 징역 또는 500만원 이상 2천만원 이하의 벌금에 처한다."),
    (COL_REFERENCE_CONTEXTS, False, 45,
     f"정답의 근거가 되는 원문 발췌. 여러 건이면 셀 안에서 '{CONTEXT_SEPARATOR}' 만 있는 줄로 구분 (Alt+Enter 로 줄바꿈).",
     "제148조의2(벌칙) ② 술에 취한 상태에 있다고 인정할 만한…\n---\n제44조(술에 취한 상태에서의 운전 금지) ②…"),
    (COL_SOURCE_NAMES, False, 28,
     "검색되어야 할 문서명. 여러 건이면 줄바꿈으로 구분. "
     "★ 검색엔진에 저장된 문서명 표기를 그대로 쓸 것 (결과 엑셀 '검색결과' 시트의 문서명 참고).",
     "도로교통법\n도로교통법"),
    (COL_SOURCE_SECTIONS, False, 28,
     "위 문서명과 같은 순서·같은 개수로 목차(section_path)를 줄바꿈으로 구분. "
     "★ 검색엔진에 저장된 목차 표기를 그대로 쓸 것. "
     "전체 경로(제2편 각칙 > 제38장 … > 제329조(절도))를 적어도 되고, "
     "한 단계만(제329조(절도) 또는 제329조) 적어도 일치로 본다. "
     "통째로 비우면 문서명만으로 일치 여부를 판단한다.",
     "제148조의2\n제44조"),
    (COL_REQUIRED_KEYWORDS, False, 24,
     "답변에 반드시 들어가야 하는 말. 쉼표로 구분. "
     "★ 흔한 단어가 아니라 결론을 가르는 핵심어(수치·조문·요건)로 쓸 것.",
     "제148조의2, 1년 이상 5년 이하"),
    (COL_FORBIDDEN_KEYWORDS, False, 24, "답변에 나오면 안 되는 말(틀린 결론·다른 조문 등). 쉼표로 구분.", "과태료, 제148조의3"),
    (COL_EXPECT_FALLBACK, False, 12,
     "Y = 자료가 없어 '관련 자료 없음'으로 답해야 정답인 질문. 비우면 N.", "N"),
    (COL_PREV_QUESTION, False, 30,
     "멀티턴(후속 질문) 평가용 직전 질문 1턴. 검색대상이 MANUAL 일 때만 사용된다. 이전답변과 함께 작성.", ""),
    (COL_PREV_ANSWER, False, 30, "직전 질문에 대한 답변 본문(출처 제외).", ""),
    (COL_NOTE, False, 24, "자유 메모. 평가에 쓰지 않는다.", ""),
)

COLUMNS = tuple(spec[0] for spec in _COLUMN_SPECS)

# 작성가이드 시트 상단 강조 문구
_GUIDE_NOTES = (
    "★ 기대출처(문서명·목차)는 검색엔진에 저장된 표기를 그대로 써야 일치로 판정됩니다. "
    "먼저 한 번 평가를 돌린 뒤 결과 엑셀 '검색결과' 시트의 문서명·목차를 복사해 쓰는 것을 권장합니다.",
    "★ 필수키워드는 결론을 가르는 핵심어로 쓰세요. '경찰', '법률' 같은 흔한 단어는 오답에도 들어가 변별력이 없습니다.",
    "★ LLM 모드(EVAL_USE_LLM=true)로 평가하려면 모범답안이 필요합니다. 비어 있으면 해당 지표는 N/A 로 처리됩니다.",
    "· 비워 둔 기대값은 0점이 아니라 N/A 로 처리되어 평균에서 빠집니다.",
    "· 시트 이름과 1행 헤더는 바꾸지 마세요. 열 순서는 바꿔도 됩니다.",
)

# 드롭다운을 미리 적용해 둘 데이터 행 범위
_MAX_DATA_ROW = 2000

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_REQUIRED_FILL = PatternFill("solid", fgColor="C0504D")   # 필수 열
_OPTIONAL_FILL = PatternFill("solid", fgColor="4F81BD")   # 선택 열
_NOTE_FILL = PatternFill("solid", fgColor="FFF2CC")
_WRAP_TOP = Alignment(wrap_text=True, vertical="top")
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def create_template(path: Path | str) -> Path:
    """빈 골든셋 템플릿(.xlsx)을 만들어 저장하고 경로를 돌려준다."""
    path = Path(path)
    workbook = Workbook()

    golden = workbook.active
    golden.title = SHEET_GOLDEN
    _build_golden_sheet(golden)
    _build_guide_sheet(workbook.create_sheet(SHEET_GUIDE))
    _build_codes_sheet(workbook.create_sheet(SHEET_CODES))

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _build_golden_sheet(sheet) -> None:
    for index, (name, required, width, _guide, _example) in enumerate(_COLUMN_SPECS, start=1):
        cell = sheet.cell(row=1, column=index, value=name)
        cell.font = _HEADER_FONT
        cell.fill = _REQUIRED_FILL if required else _OPTIONAL_FILL
        cell.alignment = _CENTER
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = width
        # 긴 글이 한 줄로 늘어지지 않게 열 기본 서식으로 줄바꿈 적용
        sheet.column_dimensions[letter].alignment = _WRAP_TOP
    sheet.freeze_panes = "D2"   # 헤더 행 + ID/검색대상/질문 열 고정

    _add_dropdown(sheet, COL_DOC_TYPE, f"='{SHEET_CODES}'!$A$2:$A${len(DOC_TYPES) + 1}",
                  "검색대상은 목록에서 선택하세요 (MANUAL / LAW / ADMRUL / PREC).")
    _add_dropdown(sheet, COL_EXPECT_FALLBACK, f"='{SHEET_CODES}'!$B$2:$B${len(YES_NO) + 1}",
                  "Y 또는 N 만 입력할 수 있습니다.")


def _add_dropdown(sheet, column_name: str, formula: str, error_message: str) -> None:
    letter = get_column_letter(COLUMNS.index(column_name) + 1)
    validation = DataValidation(type="list", formula1=formula, allow_blank=True, showErrorMessage=True)
    validation.errorTitle = "입력값 오류"
    validation.error = error_message
    sheet.add_data_validation(validation)
    validation.add(f"{letter}2:{letter}{_MAX_DATA_ROW}")


def _build_guide_sheet(sheet) -> None:
    row = 1
    for note in _GUIDE_NOTES:
        cell = sheet.cell(row=row, column=1, value=note)
        cell.alignment = _WRAP_TOP
        if note.startswith("★"):
            cell.fill = _NOTE_FILL
            cell.font = Font(bold=True)
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        sheet.row_dimensions[row].height = 34
        row += 1

    row += 1
    for index, title in enumerate(("열", "필수", "작성법", "예시"), start=1):
        cell = sheet.cell(row=row, column=index, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _OPTIONAL_FILL
        cell.alignment = _CENTER
    for name, required, _width, guide, example in _COLUMN_SPECS:
        row += 1
        values = (name, "필수" if required else "", guide, example)
        for index, value in enumerate(values, start=1):
            sheet.cell(row=row, column=index, value=value).alignment = _WRAP_TOP

    for letter, width in zip("ABCD", (20, 8, 70, 60)):
        sheet.column_dimensions[letter].width = width


def _build_codes_sheet(sheet) -> None:
    for index, (title, values) in enumerate(((COL_DOC_TYPE, DOC_TYPES), (COL_EXPECT_FALLBACK, YES_NO)), start=1):
        header = sheet.cell(row=1, column=index, value=title)
        header.font = Font(bold=True)
        for offset, value in enumerate(values, start=2):
            sheet.cell(row=offset, column=index, value=value)
        sheet.column_dimensions[get_column_letter(index)].width = 16
