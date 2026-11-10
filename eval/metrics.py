"""
자체 규칙 지표 (순수 함수 — 네트워크·LLM·파일 접근 없음).

공통 규칙: 기대값이 비어 계산할 수 없으면 0 이 아니라 None(N/A)을 돌려준다.
"""
import re
import unicodedata
from collections.abc import Iterable, Sequence

# ── 인용 추출 정규식 (수정은 여기서만) ──
# 조문: 제148조, 제148조의2, 제 148 조의 2
ARTICLE_PATTERN = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?")
# 판례·사건번호: 2005도1247, 2019다12345, 2018헌마123, 99도1234
#  - 숫자 바로 뒤가 단위(년·월·만원 등)면 사건번호가 아니다 → "2020년8월", "500만원2" 제외
CASE_NUMBER_PATTERN = re.compile(
    r"(?<!\d)\d{2,4}(?![년월일시분초만천억원조항호명건개회차세번])[가-힣]{1,3}\d+(?!\d)"
)
CITATION_PATTERNS = (ARTICLE_PATTERN, CASE_NUMBER_PATTERN)

_WHITESPACE = re.compile(r"\s+")

# 출처 ID 의 문서명·목차 구분자
SOURCE_ID_SEPARATOR = "|"

# ── 목차(section_path) 해석 (수정은 여기서만) ──
SECTION_PATH_SEPARATOR = ">"                         # "제2편 각칙 > 제38장 … > 제329조(절도)"
_REVISION_TAG = re.compile(r"<[^<>]*>")              # "<개정 2009.12.29>" 같은 개정 표시
_TRAILING_TITLE = re.compile(r"\([^()]*\)\s*$")      # 끝의 괄호 제목 "(절도)"


def normalize(text: str) -> str:
    """비교용 정규화: 유니코드 호환 정규화(NFKC) + 소문자 + 모든 공백 제거."""
    return _WHITESPACE.sub("", unicodedata.normalize("NFKC", text or "")).lower()


# ── 키워드 ──

def keyword_recall(answer: str, keywords: Sequence[str]) -> tuple[float | None, list[str]]:
    """필수키워드 포함 비율과 빠진 키워드 목록. 키워드가 없으면 (None, [])."""
    if not keywords:
        return None, []
    normalized_answer = normalize(answer)
    missing = [keyword for keyword in keywords if normalize(keyword) not in normalized_answer]
    return (len(keywords) - len(missing)) / len(keywords), missing


def forbidden_hit(answer: str, keywords: Sequence[str]) -> tuple[bool | None, list[str]]:
    """금지키워드가 답변에 나왔는지와 걸린 키워드 목록. 키워드가 없으면 (None, [])."""
    if not keywords:
        return None, []
    normalized_answer = normalize(answer)
    hits = [keyword for keyword in keywords if normalize(keyword) in normalized_answer]
    return bool(hits), hits


# ── 인용 환각 ──

def extract_citations(text: str) -> list[str]:
    """텍스트의 조문·사건번호 인용을 공백 제거한 형태로, 처음 나온 순서대로 중복 없이 돌려준다."""
    found: dict[str, None] = {}
    for pattern in CITATION_PATTERNS:
        for match in pattern.finditer(text or ""):
            found.setdefault(_WHITESPACE.sub("", match.group()))
    return list(found)


def citation_hallucination(answer: str, grounding_texts: Iterable[str]) -> tuple[int, list[str]]:
    """
    답변의 인용 중 근거에 없는 것의 개수와 목록.

    grounding_texts: 근거 청크의 본문 + 문서명 + 목차
      (조문 번호가 본문이 아니라 목차에만 있는 청크가 많아 메타데이터도 근거로 본다)
    '제148조' 와 '제148조의2' 는 서로 다른 인용으로 본다 (부분 문자열 일치 아님).
    """
    grounded: set[str] = set()
    for text in grounding_texts:
        grounded.update(extract_citations(text))
    hallucinated = [citation for citation in extract_citations(answer) if citation not in grounded]
    return len(hallucinated), hallucinated


# ── 출처 일치 ──

def source_id(doc_name: str, section_path: str = "") -> str:
    """출처 ID = 정규화(문서명) + "|" + 정규화(목차). (응답에 chunk id 가 없어 이 값으로 대신한다)"""
    return normalize(doc_name) + SOURCE_ID_SEPARATOR + normalize(section_path)


def section_candidates(section_path: str) -> set[str]:
    """
    검색 결과의 목차(section_path)에서 '일치로 인정할 표기'를 모두 만든다.

    예) "제2편 각칙 > 제38장 절도와 강도의 죄 <개정 2020.1.1> > 제329조(절도)" →
        전체 경로, 각 단계("제2편 각칙", "제38장 절도와 강도의 죄", "제329조(절도)"),
        각 단계에서 괄호 제목을 뺀 것("제329조") — 모두 정규화한 값
    """
    # 개정 표시의 '>' 가 경로 구분자로 잘리지 않게 먼저 지운다
    path = _REVISION_TAG.sub("", section_path or "")
    candidates = {normalize(path)}
    for segment in path.split(SECTION_PATH_SEPARATOR):
        candidates.add(normalize(segment))
        candidates.add(normalize(_TRAILING_TITLE.sub("", segment)))
    candidates.discard("")
    return candidates


def source_matches(expected: tuple[str, str], retrieved: tuple[str, str]) -> bool:
    """
    기대 출처(문서명, 목차)와 검색 출처(문서명, 목차)가 같은지.

    - 문서명은 정규화 후 완전 일치
    - 기대 목차가 비어 있으면 문서명만 비교
    - 기대 목차는 전체 경로를 적어도 되고, 경로의 한 단계(예: "제329조(절도)" 또는 "제329조")만 적어도 된다
      ("제148조" 는 "제148조의2" 와 일치하지 않는다)
    """
    expected_name, expected_section = expected
    retrieved_name, retrieved_section = retrieved
    if normalize(expected_name) != normalize(retrieved_name):
        return False
    # 결과 엑셀에서 복사해 온 목차에 개정 표시가 섞여 있어도 일치하도록 기대값에서도 지운다
    wanted = normalize(_REVISION_TAG.sub("", expected_section or ""))
    return not wanted or wanted in section_candidates(retrieved_section)


def first_hit_rank(expected: Sequence[tuple[str, str]], retrieved: Sequence[tuple[str, str]]) -> int | None:
    """기대 출처 중 하나와 일치하는 첫 검색 결과의 순위(1부터). 없으면 None."""
    for rank, document in enumerate(retrieved, start=1):
        if any(source_matches(source, document) for source in expected):
            return rank
    return None


def hit_at_k(expected: Sequence[tuple[str, str]], retrieved: Sequence[tuple[str, str]], k: int) -> bool | None:
    """기대 출처가 검색 결과 상위 k개 안에 하나라도 있는지. 기대 출처가 없으면 None."""
    if not expected:
        return None
    rank = first_hit_rank(expected, retrieved)
    return rank is not None and rank <= k


def mrr(expected: Sequence[tuple[str, str]], retrieved: Sequence[tuple[str, str]]) -> float | None:
    """역순위(1 / 기대 출처가 처음 나온 순위). 검색 결과에 없으면 0, 기대 출처가 없으면 None."""
    if not expected:
        return None
    rank = first_hit_rank(expected, retrieved)
    return 1 / rank if rank else 0.0


# ── 응답 형식 ──

def fallback_correct(expect_fallback: bool, fallback_yn: str) -> bool:
    """기대한 '결과 없음' 여부와 실제 fallback_yn 이 일치하는지."""
    return expect_fallback == ((fallback_yn or "").strip().upper() == "Y")


def reason_filled(reason: str) -> bool:
    """이유(reason)가 비어 있지 않은지."""
    return bool((reason or "").strip())
