"""자체 규칙 지표 경계 케이스 (한글 공백 변형, 빈 기대값 → N/A, 조문·사건번호 정규식)."""
import pytest

from eval import metrics


# ── 정규화 / 키워드 ──

def test_normalize_공백과_대소문자와_전각문자():
    assert metrics.normalize(" 1년 이상\n5년　이하 ") == "1년이상5년이하"
    assert metrics.normalize("ABC Ｄ１") == "abcd1"
    assert metrics.normalize(None) == ""


def test_keyword_recall_공백_변형은_일치로_본다():
    score, missing = metrics.keyword_recall(
        "측정에 불응하면 1년이상 5년 이하의 징역(도로교통법 제148조의 2)",
        ["1년 이상 5년 이하", "제148조의2", "벌금"],
    )
    assert score == pytest.approx(2 / 3)
    assert missing == ["벌금"]


def test_keyword_recall_영문_대소문자_무시():
    assert metrics.keyword_recall("cctv 영상을 확보한다", ["CCTV"]) == (1.0, [])


def test_keyword_recall_키워드가_없으면_NA():
    assert metrics.keyword_recall("아무 답변", []) == (None, [])


def test_keyword_recall_답변이_비면_0점():
    assert metrics.keyword_recall("", ["징역"]) == (0.0, ["징역"])


def test_forbidden_hit():
    assert metrics.forbidden_hit("과 태 료를 부과한다", ["과태료", "범칙금"]) == (True, ["과태료"])
    assert metrics.forbidden_hit("징역에 처한다", ["과태료"]) == (False, [])
    assert metrics.forbidden_hit("징역에 처한다", []) == (None, [])


# ── 인용 추출 정규식 ──

@pytest.mark.parametrize("text, expected", [
    ("제148조에 따라", ["제148조"]),
    ("제148조의2 제2항", ["제148조의2"]),
    ("제 148 조의 2 에 따라", ["제148조의2"]),
    ("제44조제2항 및 제44조", ["제44조"]),                       # 중복 제거
    ("제3조와 제10조의3", ["제3조", "제10조의3"]),
    ("대법원 2005도1247 판결", ["2005도1247"]),
    ("대법원 2005. 10. 28. 선고 2005도1247 판결", ["2005도1247"]),
    ("헌재 2018헌마123 결정", ["2018헌마123"]),
    ("서울고법 2019구합12345", ["2019구합12345"]),
    ("99도1234 판결", ["99도1234"]),
])
def test_extract_citations_인용을_찾는다(text, expected):
    assert metrics.extract_citations(text) == expected


@pytest.mark.parametrize("text", [
    "2020년8월11일 개정",          # 날짜
    "12월31일까지",
    "500만원2천만원",              # 금액
    "1년 이상 5년 이하",
    "제2항 제3호",                 # 항·호는 조문이 아님
    "30일10시",
    "",
])
def test_extract_citations_인용이_아닌_것(text):
    assert metrics.extract_citations(text) == []


def test_citation_hallucination_근거에_없는_인용만_센다():
    count, hallucinated = metrics.citation_hallucination(
        "제148조의2와 제999조, 그리고 2005도1247 판결에 따르면",
        ["제148조의2(벌칙) 술에 취한…", "대법원 2005도1247"],
    )
    assert (count, hallucinated) == (1, ["제999조"])


def test_citation_hallucination_조문과_조의N은_다른_인용():
    # 근거에 '제148조의2' 만 있으면 '제148조' 인용은 근거 없음
    assert metrics.citation_hallucination("제148조에 따라", ["제148조의2(벌칙)"]) == (1, ["제148조"])
    assert metrics.citation_hallucination("제148조의2에 따라", ["제148조(벌칙)"]) == (1, ["제148조의2"])


def test_citation_hallucination_목차에만_있는_조문도_근거로_인정():
    # 조문 번호가 청크 본문이 아니라 목차(section_path)에만 있는 경우
    grounding = ["① 질병관리청장은 연구사업을 수행한다.", "희귀질환관리법", "제2장 > 제9조(연구개발사업)"]
    assert metrics.citation_hallucination("희귀질환관리법 제9조에 따라", grounding) == (0, [])


def test_citation_hallucination_인용이_없으면_0():
    assert metrics.citation_hallucination("관련 규정에 따라 처리한다", ["제1조"]) == (0, [])


def test_citation_hallucination_공백_변형():
    assert metrics.citation_hallucination("제 44 조에 따라", ["제44조(금지)"]) == (0, [])


# ── 출처 일치 / Hit@k / MRR ──

RETRIEVED = [("형법", "제1조"), ("도로교통법", "제44조"), ("도로 교통법", "제148조의2"), ("형법", "제2조")]


def test_source_id_정규화():
    assert metrics.source_id("도로 교통법", "제148조의 2") == "도로교통법|제148조의2"
    assert metrics.source_id("형법") == "형법|"


def test_hit_at_k_와_mrr():
    expected = [("도로교통법", "제148조의2")]
    assert metrics.hit_at_k(expected, RETRIEVED, 1) is False
    assert metrics.hit_at_k(expected, RETRIEVED, 2) is False
    assert metrics.hit_at_k(expected, RETRIEVED, 3) is True    # 문서명 공백 변형도 일치
    assert metrics.hit_at_k(expected, RETRIEVED, 5) is True    # k 가 결과 수보다 커도 됨
    assert metrics.mrr(expected, RETRIEVED) == pytest.approx(1 / 3)


def test_기대출처가_여러_건이면_하나만_맞아도_hit():
    expected = [("민법", "제1조"), ("도로교통법", "제44조")]
    assert metrics.hit_at_k(expected, RETRIEVED, 2) is True
    assert metrics.mrr(expected, RETRIEVED) == 0.5


def test_기대_목차가_비면_문서명만_비교():
    expected = [("도로교통법", "")]
    assert metrics.hit_at_k(expected, RETRIEVED, 2) is True
    assert metrics.mrr(expected, RETRIEVED) == 0.5


@pytest.mark.parametrize("expected_section, matched", [
    ("제2편 각칙 > 제38장 절도와 강도의 죄 > 제329조(절도)", True),    # 전체 경로
    ("제2편 각칙>제38장 절도와 강도의 죄>제329조(절도)", True),        # 공백 변형
    ("제2편 각칙 > 제38장 절도와 강도의 죄 <개정 2020.1.1> > 제329조(절도)", True),   # 개정 표시째 복사
    ("제329조(절도)", True),                                         # 마지막 단계
    ("제329조", True),                                               # 괄호 제목 생략
    ("제38장 절도와 강도의 죄", True),                                # 중간 단계 (개정 표시는 무시)
    ("제32조", False),                                               # 부분 문자열은 불일치
    ("제329조의2", False),
    ("절도", False),
])
def test_목차는_전체_경로나_한_단계만_적어도_일치(expected_section, matched):
    retrieved = ("형법", "제2편 각칙 > 제38장 절도와 강도의 죄 <개정 2020.1.1> > 제329조(절도)")
    assert metrics.source_matches(("형법", expected_section), retrieved) is matched


def test_조문과_조의N은_목차에서도_구분():
    retrieved = ("도로교통법", "제13장 벌칙 > 제148조의2(벌칙)")
    assert metrics.source_matches(("도로교통법", "제148조의2"), retrieved) is True
    assert metrics.source_matches(("도로교통법", "제148조"), retrieved) is False


def test_목차가_다르면_불일치():
    expected = [("도로교통법", "제45조")]
    assert metrics.hit_at_k(expected, RETRIEVED, 5) is False
    assert metrics.mrr(expected, RETRIEVED) == 0.0


def test_기대출처가_없으면_NA():
    assert metrics.hit_at_k([], RETRIEVED, 3) is None
    assert metrics.mrr([], RETRIEVED) is None


def test_검색결과가_없으면_0점():
    expected = [("도로교통법", "제44조")]
    assert metrics.hit_at_k(expected, [], 3) is False
    assert metrics.mrr(expected, []) == 0.0


# ── 응답 형식 ──

@pytest.mark.parametrize("expect, actual, correct", [
    (False, "N", True), (True, "Y", True), (True, "N", False), (False, "Y", False),
    (True, " y ", True), (False, "", True), (False, None, True),
])
def test_fallback_correct(expect, actual, correct):
    assert metrics.fallback_correct(expect, actual) is correct


def test_reason_filled():
    assert metrics.reason_filled("근거 조문에 따름") is True
    assert metrics.reason_filled("  \n ") is False
    assert metrics.reason_filled(None) is False
