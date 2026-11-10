"""
골든셋 엑셀 → GoldenItem 목록.

오류(필수값 누락, 검색대상 오류, ID 중복, 기대출처 개수 불일치 등)는 발견 즉시 멈추지 않고
행 번호와 함께 모두 모아 GoldenLoadError 한 번으로 보고한다.
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from eval.template import (
    COL_DOC_TYPE,
    COL_EXPECT_FALLBACK,
    COL_FORBIDDEN_KEYWORDS,
    COL_ID,
    COL_NOTE,
    COL_PREV_ANSWER,
    COL_PREV_QUESTION,
    COL_QUESTION,
    COL_REFERENCE,
    COL_REFERENCE_CONTEXTS,
    COL_REQUIRED_KEYWORDS,
    COL_SOURCE_NAMES,
    COL_SOURCE_SECTIONS,
    COLUMNS,
    DOC_TYPES,
    REQUIRED_COLUMNS,
    SHEET_GOLDEN,
    YES_NO,
)

logger = logging.getLogger(__name__)

# 정답근거 구분 줄: 하이픈 3개 이상만 있는 줄
_CONTEXT_SPLIT = re.compile(r"^[ \t]*-{3,}[ \t]*$", re.MULTILINE)
# 키워드 구분: 쉼표(전각 포함) 또는 줄바꿈
_KEYWORD_SPLIT = re.compile(r"[,，\n]")

# 멀티턴(이전질문/이전답변)을 쓰는 검색대상
_MULTITURN_DOC_TYPE = "MANUAL"


class GoldenLoadError(Exception):
    """골든셋 엑셀 검증 실패. errors 에 전체 오류 목록이 들어 있다."""

    def __init__(self, path: Path | str, errors: list[str]):
        self.errors = errors
        lines = "\n".join(f" - {e}" for e in errors)
        super().__init__(f"골든셋 파일에 오류가 {len(errors)}건 있습니다 ({path}):\n{lines}")


@dataclass(frozen=True)
class ExpectedSource:
    """기대 출처 1건. section_path 가 빈 문자열이면 문서명만으로 일치를 판단한다."""
    doc_name: str
    section_path: str = ""


@dataclass(frozen=True)
class GoldenItem:
    """골든셋 1건 (엑셀 1행)."""
    id: str
    doc_type: str
    question: str
    row: int                                    # 엑셀 행 번호 (오류·결과 대조용)
    reference: str = ""                         # 모범답안
    reference_contexts: tuple[str, ...] = ()    # 정답근거
    expected_sources: tuple[ExpectedSource, ...] = ()
    required_keywords: tuple[str, ...] = ()
    forbidden_keywords: tuple[str, ...] = ()
    expect_fallback: bool = False               # 결과없음기대
    prev_question: str = ""
    prev_answer: str = ""
    note: str = ""
    history: tuple[dict, ...] = field(default=(), compare=False)   # API 요청용 history


def load_golden(path: Path | str) -> list[GoldenItem]:
    """골든셋 엑셀을 읽어 GoldenItem 목록을 돌려준다. 오류가 하나라도 있으면 GoldenLoadError."""
    path = Path(path)
    if not path.is_file():
        raise GoldenLoadError(path, ["파일을 찾을 수 없습니다"])

    workbook = load_workbook(path, data_only=True)
    try:
        if SHEET_GOLDEN not in workbook.sheetnames:
            raise GoldenLoadError(path, [f"'{SHEET_GOLDEN}' 시트가 없습니다 (있는 시트: {', '.join(workbook.sheetnames)})"])
        rows = list(workbook[SHEET_GOLDEN].iter_rows(values_only=True))
    finally:
        workbook.close()

    if not rows:
        raise GoldenLoadError(path, [f"'{SHEET_GOLDEN}' 시트가 비어 있습니다"])

    # 열 순서가 바뀌어도 되도록 헤더 이름으로 위치를 찾는다
    header = {_text(value): index for index, value in enumerate(rows[0]) if _text(value)}
    missing = [name for name in COLUMNS if name not in header]
    if missing:
        raise GoldenLoadError(path, [f"1행 헤더에 없는 열: {', '.join(missing)}"])

    errors: list[str] = []
    items: list[GoldenItem] = []
    first_row_by_id: dict[str, int] = {}

    for row_number, row in enumerate(rows[1:], start=2):
        cells = {name: _text(row[header[name]]) if header[name] < len(row) else "" for name in COLUMNS}
        if not any(cells.values()):
            continue   # 빈 행은 건너뛴다

        row_errors = _validate_row(cells)

        item_id = cells[COL_ID]
        if item_id:
            if item_id in first_row_by_id:
                row_errors.append(f"ID 중복: '{item_id}' ({first_row_by_id[item_id]}행과 같음)")
            else:
                first_row_by_id[item_id] = row_number

        sources, source_error = _parse_sources(cells[COL_SOURCE_NAMES], cells[COL_SOURCE_SECTIONS])
        if source_error:
            row_errors.append(source_error)

        if row_errors:
            errors.extend(f"{row_number}행: {e}" for e in row_errors)
            continue

        doc_type = cells[COL_DOC_TYPE].upper()
        history: tuple[dict, ...] = ()
        if cells[COL_PREV_QUESTION]:
            if doc_type == _MULTITURN_DOC_TYPE:
                history = (
                    {"role": "user", "content": cells[COL_PREV_QUESTION]},
                    {"role": "assistant", "content": cells[COL_PREV_ANSWER]},
                )
            else:
                logger.warning("%s행(%s): 이전질문/이전답변은 MANUAL 에서만 사용됩니다. 무시합니다 (검색대상=%s)",
                               row_number, item_id, doc_type)

        items.append(GoldenItem(
            id=item_id,
            doc_type=doc_type,
            question=cells[COL_QUESTION],
            row=row_number,
            reference=cells[COL_REFERENCE],
            reference_contexts=_split_contexts(cells[COL_REFERENCE_CONTEXTS]),
            expected_sources=sources,
            required_keywords=_split_keywords(cells[COL_REQUIRED_KEYWORDS]),
            forbidden_keywords=_split_keywords(cells[COL_FORBIDDEN_KEYWORDS]),
            expect_fallback=cells[COL_EXPECT_FALLBACK].upper() == "Y",
            prev_question=cells[COL_PREV_QUESTION],
            prev_answer=cells[COL_PREV_ANSWER],
            note=cells[COL_NOTE],
            history=history,
        ))

    if not errors and not items:
        errors.append("데이터 행이 없습니다 (2행부터 작성)")
    if errors:
        raise GoldenLoadError(path, errors)

    logger.info("골든셋 %d건 로드 (%s)", len(items), path.name)
    return items


def _validate_row(cells: dict[str, str]) -> list[str]:
    """한 행의 값 검증 (ID 중복·기대출처 개수는 호출부에서 따로 확인)."""
    errors = [f"필수값 누락: {name}" for name in REQUIRED_COLUMNS if not cells[name]]

    doc_type = cells[COL_DOC_TYPE]
    if doc_type and doc_type.upper() not in DOC_TYPES:
        errors.append(f"{COL_DOC_TYPE} 오류: '{doc_type}' (허용값: {' / '.join(DOC_TYPES)})")

    fallback = cells[COL_EXPECT_FALLBACK]
    if fallback and fallback.upper() not in YES_NO:
        errors.append(f"{COL_EXPECT_FALLBACK} 오류: '{fallback}' (허용값: Y / N / 빈칸)")

    if bool(cells[COL_PREV_QUESTION]) != bool(cells[COL_PREV_ANSWER]):
        errors.append(f"{COL_PREV_QUESTION}과 {COL_PREV_ANSWER}은 함께 작성하거나 함께 비워야 합니다")
    return errors


def _parse_sources(names_cell: str, sections_cell: str) -> tuple[tuple[ExpectedSource, ...], str | None]:
    """기대출처 문서명·목차를 짝지어 ExpectedSource 로 만든다. 문제가 있으면 (빈 튜플, 오류 문구)."""
    names = _split_lines(names_cell)
    sections = _split_lines(sections_cell)
    if not names:
        if sections:
            return (), f"{COL_SOURCE_SECTIONS}만 있고 {COL_SOURCE_NAMES}이 비어 있습니다"
        return (), None
    if not sections:
        # 목차를 통째로 비우면 문서 단위로만 비교한다
        return tuple(ExpectedSource(name) for name in names), None
    if len(names) != len(sections):
        return (), f"기대출처 개수 불일치: 문서명 {len(names)}건, 목차 {len(sections)}건"
    return tuple(ExpectedSource(name, section) for name, section in zip(names, sections)), None


def _text(value) -> str:
    """셀 값을 문자열로. 빈 셀은 빈 문자열, 정수로 떨어지는 숫자는 소수점 없이."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _split_lines(cell: str) -> list[str]:
    return [line.strip() for line in cell.split("\n") if line.strip()]


def _split_keywords(cell: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _KEYWORD_SPLIT.split(cell) if part.strip())


def _split_contexts(cell: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _CONTEXT_SPLIT.split(cell) if part.strip())
