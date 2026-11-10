"""골든셋 템플릿 생성 + 로더 테스트 (정상 / 오류 템플릿)."""
import pytest
from openpyxl import load_workbook

from eval.loader import ExpectedSource, GoldenLoadError, load_golden
from eval.template import COLUMNS, DOC_TYPES, SHEET_CODES, SHEET_GOLDEN, SHEET_GUIDE, create_template


def _write_golden(tmp_path, rows: list[dict], name: str = "golden.xlsx"):
    """빈 템플릿을 만들고 rows(열 이름 → 값)를 2행부터 채워 저장한다."""
    path = create_template(tmp_path / name)
    workbook = load_workbook(path)
    sheet = workbook[SHEET_GOLDEN]
    for row_number, row in enumerate(rows, start=2):
        for column_name, value in row.items():
            sheet.cell(row=row_number, column=COLUMNS.index(column_name) + 1, value=value)
    workbook.save(path)
    return path


def _row(**overrides) -> dict:
    row = {"ID": "LAW-001", "검색대상": "LAW", "질문": "음주측정을 거부하면?"}
    row.update(overrides)
    return row


# ── 템플릿 ──

def test_템플릿_시트와_헤더_드롭다운(tmp_path):
    path = create_template(tmp_path / "golden_template.xlsx")
    workbook = load_workbook(path)

    assert workbook.sheetnames == [SHEET_GOLDEN, SHEET_GUIDE, SHEET_CODES]
    golden = workbook[SHEET_GOLDEN]
    assert tuple(cell.value for cell in golden[1]) == COLUMNS

    # 드롭다운 2개(검색대상, 결과없음기대)가 코드값 시트를 가리킨다
    validations = golden.data_validations.dataValidation
    assert len(validations) == 2
    assert all(SHEET_CODES in v.formula1 for v in validations)

    codes = workbook[SHEET_CODES]
    assert tuple(codes.cell(row=r, column=1).value for r in range(2, 6)) == DOC_TYPES


def test_빈_템플릿은_데이터_없음_오류(tmp_path):
    path = create_template(tmp_path / "empty.xlsx")
    with pytest.raises(GoldenLoadError) as error:
        load_golden(path)
    assert "데이터 행이 없습니다" in str(error.value)


# ── 정상 ──

def test_정상_로드(tmp_path):
    path = _write_golden(tmp_path, [
        _row(
            모범답안="1년 이상 5년 이하의 징역",
            정답근거="제148조의2(벌칙) …\n---\n제44조 …",
            기대출처_문서명="도로교통법\n도로교통법",
            기대출처_목차="제148조의2\n제44조",
            필수키워드="제148조의2, 1년 이상 5년 이하",
            금지키워드="과태료",
            비고="메모",
        ),
        _row(ID="MAN-001", 검색대상="manual", 질문="그럼 미성년자는?",
             이전질문="피의자 조사 절차는?", 이전답변="다음과 같습니다.", 결과없음기대="y"),
    ])

    items = load_golden(path)

    assert [item.id for item in items] == ["LAW-001", "MAN-001"]
    law, manual = items
    assert law.row == 2
    assert law.reference_contexts == ("제148조의2(벌칙) …", "제44조 …")
    assert law.expected_sources == (
        ExpectedSource("도로교통법", "제148조의2"),
        ExpectedSource("도로교통법", "제44조"),
    )
    assert law.required_keywords == ("제148조의2", "1년 이상 5년 이하")
    assert law.forbidden_keywords == ("과태료",)
    assert law.expect_fallback is False
    assert law.history == ()

    # 소문자 입력은 대문자로 정규화, MANUAL 은 history 구성
    assert manual.doc_type == "MANUAL"
    assert manual.expect_fallback is True
    assert manual.history == (
        {"role": "user", "content": "피의자 조사 절차는?"},
        {"role": "assistant", "content": "다음과 같습니다."},
    )


def test_선택값이_비면_빈값(tmp_path):
    items = load_golden(_write_golden(tmp_path, [_row()]))
    item = items[0]
    assert item.reference == ""
    assert item.reference_contexts == ()
    assert item.expected_sources == ()
    assert item.required_keywords == ()
    assert item.expect_fallback is False


def test_목차를_비우면_문서명만으로_기대출처(tmp_path):
    items = load_golden(_write_golden(tmp_path, [_row(기대출처_문서명="도로교통법\n형법")]))
    assert items[0].expected_sources == (ExpectedSource("도로교통법"), ExpectedSource("형법"))


def test_숫자_ID와_빈_행_건너뛰기(tmp_path):
    path = _write_golden(tmp_path, [_row(ID=1), {}, _row(ID=2)])
    items = load_golden(path)
    assert [(item.id, item.row) for item in items] == [("1", 2), ("2", 4)]


def test_MANUAL_이_아니면_이전질문_무시(tmp_path):
    items = load_golden(_write_golden(tmp_path, [_row(이전질문="앞 질문", 이전답변="앞 답변")]))
    assert items[0].history == ()


def test_열_순서가_바뀌어도_로드(tmp_path):
    path = _write_golden(tmp_path, [_row()])
    workbook = load_workbook(path)
    sheet = workbook[SHEET_GOLDEN]
    # ID 열과 질문 열을 통째로 맞바꾼다
    for row in sheet.iter_rows(min_row=1, max_row=2):
        row[0].value, row[2].value = row[2].value, row[0].value
    workbook.save(path)

    items = load_golden(path)
    assert (items[0].id, items[0].question) == ("LAW-001", "음주측정을 거부하면?")


# ── 오류 ──

def test_오류를_행번호와_함께_모아서_보고(tmp_path):
    path = _write_golden(tmp_path, [
        _row(),                                                   # 2행 정상
        _row(ID="LAW-001"),                                       # 3행 ID 중복
        _row(ID="X-1", 검색대상="STATUTE"),                        # 4행 검색대상 오류
        _row(ID="", 질문=""),                                      # 5행 필수값 누락 2개
        _row(ID="X-2", 기대출처_문서명="가\n나", 기대출처_목차="1"),   # 6행 개수 불일치
        _row(ID="X-3", 결과없음기대="예"),                          # 7행 Y/N 오류
        _row(ID="X-4", 검색대상="MANUAL", 이전질문="앞 질문"),        # 8행 이전답변 누락
        _row(ID="X-5", 기대출처_목차="제1조"),                       # 9행 목차만 있음
    ])

    with pytest.raises(GoldenLoadError) as error:
        load_golden(path)

    errors = error.value.errors
    assert len(errors) == 8
    joined = "\n".join(errors)
    assert "3행: ID 중복: 'LAW-001' (2행과 같음)" in joined
    assert "4행: 검색대상 오류: 'STATUTE'" in joined
    assert "5행: 필수값 누락: ID" in joined
    assert "5행: 필수값 누락: 질문" in joined
    assert "6행: 기대출처 개수 불일치: 문서명 2건, 목차 1건" in joined
    assert "7행: 결과없음기대 오류" in joined
    assert "8행: 이전질문과 이전답변은 함께" in joined
    assert "9행: 기대출처_목차만 있고" in joined


def test_시트_없음(tmp_path):
    path = _write_golden(tmp_path, [_row()])
    workbook = load_workbook(path)
    workbook[SHEET_GOLDEN].title = "Sheet1"
    workbook.save(path)
    with pytest.raises(GoldenLoadError) as error:
        load_golden(path)
    assert "'골든셋' 시트가 없습니다" in str(error.value)


def test_헤더_열_누락(tmp_path):
    path = _write_golden(tmp_path, [_row()])
    workbook = load_workbook(path)
    workbook[SHEET_GOLDEN].cell(row=1, column=COLUMNS.index("정답근거") + 1, value="근거")
    workbook.save(path)
    with pytest.raises(GoldenLoadError) as error:
        load_golden(path)
    assert "1행 헤더에 없는 열: 정답근거" in str(error.value)


def test_파일_없음(tmp_path):
    with pytest.raises(GoldenLoadError) as error:
        load_golden(tmp_path / "없는파일.xlsx")
    assert "파일을 찾을 수 없습니다" in str(error.value)
