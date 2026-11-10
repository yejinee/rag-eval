"""
채점: 골든셋 + 수집 결과(raw) → 항목별 지표.

자체 규칙 지표(metrics.py)와 RAGAS 지표(ragas_eval.py)를 한 곳에서 묶는다.
LLM OFF / ON 의 차이는 맨 끝에서 score_llm() 을 추가로 부르느냐 하나뿐이다.
"""
import logging
from dataclasses import dataclass, field

from eval import metrics, ragas_eval
from eval.collect import ERR_NO_RAW
from eval.config import EvalConfig
from eval.loader import GoldenItem

logger = logging.getLogger(__name__)

GROUP_COMMON = "공통"
GROUP_LLM = "LLM"

# 지표 분류 (무엇을 평가하는가)
CATEGORY_RETRIEVAL = "검색 품질"
CATEGORY_ANSWER = "답변 품질"
CATEGORY_FORMAT = "응답 형식"
CATEGORY_OTHER = "기타 (RAG 질의응답 외)"

# 지표 종류
KIND_SCORE = "score"   # 0~1 점수 (높을수록 좋음)
KIND_FLAG = "flag"     # 1=예 / 0=아니오
KIND_COUNT = "count"   # 개수

# 자체 규칙 지표 키
KEYWORD_RECALL = "keyword_recall"
FORBIDDEN_HIT = "forbidden_hit"
CITATION_HALLUCINATION = "citation_hallucination"
MRR = "mrr"
FALLBACK_CORRECT = "fallback_correct"
REASON_FILLED = "reason_filled"


def hit_key(k: int) -> str:
    return f"hit_at_{k}"


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str                      # 엑셀 열 이름
    group: str                      # 공통 / LLM
    kind: str
    description: str                # 실행정보 시트의 지표 정의
    higher_is_better: bool = True
    ragas_name: str = ""            # RAGAS 지표 클래스명 (빈 값 = RAGAS 가 아닌 자체 규칙 지표)
    category: str = ""              # 검색 품질 / 답변 품질 / 응답 형식


def metric_defs(config: EvalConfig) -> list[MetricDef]:
    """이번 실행에서 계산하는 지표 목록 (엑셀 열 순서). LLM 지표는 LLM ON 일 때만 포함된다."""
    return [definition for definition in all_metric_defs(config.hit_k)
            if config.use_llm or definition.group != GROUP_LLM]


def all_metric_defs(hit_k: tuple[int, ...]) -> list[MetricDef]:
    """이 도구가 계산할 수 있는 모든 지표 (LLM 지표 포함). 실행정보 시트의 지표 정의 표에 쓴다."""
    defs = [
        MetricDef(KEYWORD_RECALL, "필수키워드 포함률", GROUP_COMMON, KIND_SCORE,
                  "필수키워드 중 답변에 들어 있는 비율 (공백·대소문자 무시).",
                  category=CATEGORY_ANSWER),
        MetricDef(FORBIDDEN_HIT, "금지키워드 검출", GROUP_COMMON, KIND_FLAG,
                  "금지키워드가 답변에 하나라도 나오면 1. 평균은 검출된 건의 비율 (낮을수록 좋음).",
                  higher_is_better=False, category=CATEGORY_ANSWER),
        MetricDef(CITATION_HALLUCINATION, "환각 인용 수", GROUP_COMMON, KIND_COUNT,
                  "답변이 인용한 조문·사건번호 중 근거 청크(본문·문서명·목차)에 없는 것의 개수 (낮을수록 좋음).",
                  higher_is_better=False, category=CATEGORY_ANSWER),
    ]
    defs += [
        MetricDef(hit_key(k), f"Hit@{k}", GROUP_COMMON, KIND_FLAG,
                  f"기대 출처가 검색 결과(documents) 상위 {k}개 안에 하나라도 있으면 1.",
                  category=CATEGORY_RETRIEVAL)
        for k in hit_k
    ]
    defs += [
        MetricDef(MRR, "MRR", GROUP_COMMON, KIND_SCORE,
                  "1 / (기대 출처가 검색 결과에 처음 나온 순위). 없으면 0.",
                  category=CATEGORY_RETRIEVAL),
        MetricDef(FALLBACK_CORRECT, "Fallback 일치", GROUP_COMMON, KIND_FLAG,
                  "결과없음기대(Y/N)와 실제 fallback_yn 이 같으면 1.",
                  category=CATEGORY_FORMAT),
        MetricDef(REASON_FILLED, "이유 작성", GROUP_COMMON, KIND_FLAG,
                  "응답의 이유(reason)가 비어 있지 않으면 1.",
                  category=CATEGORY_FORMAT),
        MetricDef(ragas_eval.NON_LLM_CONTEXT_RECALL, "근거 Recall(문자열)", GROUP_COMMON, KIND_SCORE,
                  "정답근거 중 답변 근거 청크와 문자열 유사도 0.5 이상으로 일치하는 비율. "
                  "Context Recall 을 LLM 판단 대신 문자열 비교로 계산한 것.",
                  ragas_name="NonLLMContextRecall", category=CATEGORY_RETRIEVAL),
        MetricDef(ragas_eval.NON_LLM_CONTEXT_PRECISION, "근거 Precision(문자열)", GROUP_COMMON, KIND_SCORE,
                  "답변 근거 청크 중 정답근거와 일치하는 청크가 상위에 있는 정도. "
                  "Context Precision 을 LLM 판단 대신 문자열 비교로 계산한 것.",
                  ragas_name="NonLLMContextPrecisionWithReference", category=CATEGORY_RETRIEVAL),
        MetricDef(ragas_eval.ID_CONTEXT_RECALL, "출처 Recall(ID)", GROUP_COMMON, KIND_SCORE,
                  "기대 출처 중 답변 근거 청크의 출처에 포함된 비율.",
                  ragas_name="IDBasedContextRecall", category=CATEGORY_RETRIEVAL),
        MetricDef(ragas_eval.ID_CONTEXT_PRECISION, "출처 Precision(ID)", GROUP_COMMON, KIND_SCORE,
                  "답변 근거 청크의 출처 중 기대 출처인 비율.",
                  ragas_name="IDBasedContextPrecision", category=CATEGORY_RETRIEVAL),
        MetricDef(ragas_eval.ANSWER_SIMILARITY, "답변 유사도(참고)", GROUP_COMMON, KIND_SCORE,
                  "답변과 모범답안의 글자 단위 유사도(편집 거리). "
                  "표현이 다르면 정답이어도 낮게 나오므로 참고용.",
                  ragas_name="NonLLMStringSimilarity", category=CATEGORY_ANSWER),
        MetricDef(ragas_eval.FAITHFULNESS, "Faithfulness", GROUP_LLM, KIND_SCORE,
                  "충실도. 답변의 주장 중 근거 청크로 뒷받침되는 비율. 낮으면 근거에 없는 내용을 지어낸 것.",
                  ragas_name="Faithfulness", category=CATEGORY_ANSWER),
        MetricDef(ragas_eval.LLM_CONTEXT_RECALL, "LLM 근거 Recall", GROUP_LLM, KIND_SCORE,
                  "컨텍스트 재현율. 모범답안의 내용 중 근거 청크에서 찾을 수 있는 비율. 낮으면 검색이 부족한 것.",
                  ragas_name="LLMContextRecall (= Context Recall)", category=CATEGORY_RETRIEVAL),
        MetricDef(ragas_eval.LLM_CONTEXT_PRECISION, "LLM 근거 Precision", GROUP_LLM, KIND_SCORE,
                  "컨텍스트 정밀도. 근거 청크 중 모범답안에 유용한 청크가 상위에 있는 정도. "
                  "낮으면 불필요한 근거가 섞인 것.",
                  ragas_name="LLMContextPrecisionWithReference (= Context Precision)", category=CATEGORY_RETRIEVAL),
        MetricDef(ragas_eval.FACTUAL_CORRECTNESS, "FactualCorrectness", GROUP_LLM, KIND_SCORE,
                  "사실 정확도(F1). 답변과 모범답안을 주장 단위로 쪼개 서로 일치하는 정도.",
                  ragas_name="FactualCorrectness", category=CATEGORY_ANSWER),
    ]
    return defs


@dataclass
class ItemResult:
    """골든셋 1건의 채점 결과."""
    item: GoldenItem
    raw: dict | None                                   # 수집 레코드 (없으면 None)
    error_code: str = ""
    metrics: dict[str, float | None] = field(default_factory=dict)
    missing_keywords: list[str] = field(default_factory=list)
    forbidden_hits: list[str] = field(default_factory=list)
    hallucinated: list[str] = field(default_factory=list)
    matched_ranks: set[int] = field(default_factory=set)   # 기대 출처와 일치한 documents 순위(1부터)
    judge_errors: list[str] = field(default_factory=list)

    @property
    def response(self) -> dict:
        return (self.raw or {}).get("response") or {}

    @property
    def documents(self) -> list[dict]:
        return self.response.get("documents") or []

    @property
    def fallback(self) -> bool:
        return str(self.response.get("fallback_yn", "")).strip().upper() == "Y"

    @property
    def elapsed_seconds(self) -> float | None:
        return (self.raw or {}).get("elapsed_seconds")


@dataclass
class ScoreOutput:
    results: list[ItemResult]
    llm_attempts: dict[str, int] = field(default_factory=dict)   # LLM 지표별 채점 시도 건수
    llm_failures: dict[str, int] = field(default_factory=dict)   # LLM 지표별 실패(NaN·오류) 건수


def score_all(items: list[GoldenItem], raws: dict[str, dict], config: EvalConfig) -> ScoreOutput:
    """골든셋 전체를 채점한다. raws = {골든셋 ID: 수집 레코드}."""
    keys = [definition.key for definition in metric_defs(config)]
    results: list[ItemResult] = []
    samples: list[ragas_eval.RagasSample] = []

    for item in items:
        raw = raws.get(item.id)
        result = ItemResult(item=item, raw=raw, metrics={key: None for key in keys})
        results.append(result)
        if raw is None:
            result.error_code = ERR_NO_RAW
            logger.warning("수집 결과에 없는 ID → 전 지표 N/A: %s", item.id)
            continue
        if raw.get("question") != item.question:
            logger.warning("수집 당시 질문과 골든셋 질문이 다릅니다 (다시 수집 필요): %s", item.id)
        if raw.get("error_code") or not raw.get("response"):
            result.error_code = raw.get("error_code") or "EVAL_BAD_RESPONSE"
            continue
        samples.append(_score_rules(result, config))

    by_id = {result.item.id: result for result in results}
    for sample_id, row in ragas_eval.score_non_llm(samples).items():
        by_id[sample_id].metrics.update(row)

    output = ScoreOutput(results=results)
    if config.use_llm:
        llm = ragas_eval.score_llm(samples, config)
        for sample_id, row in llm.scores.items():
            by_id[sample_id].metrics.update(row)
        for sample_id, errors in llm.errors.items():
            by_id[sample_id].judge_errors = errors
        output.llm_attempts = llm.attempts
        output.llm_failures = llm.failures
    return output


def _score_rules(result: ItemResult, config: EvalConfig) -> ragas_eval.RagasSample:
    """자체 규칙 지표를 계산해 result 에 채우고, RAGAS 채점 입력을 만들어 돌려준다."""
    item = result.item
    response = result.response
    answer = str(response.get("answer") or "")
    documents = result.documents
    used = [document for document in documents if str(document.get("used_yn", "")).upper() == "Y"]
    fallback = result.fallback
    scores = result.metrics

    def flag(value: bool | None) -> float | None:
        return None if value is None else float(value)

    scores[KEYWORD_RECALL], result.missing_keywords = metrics.keyword_recall(answer, item.required_keywords)
    hit, result.forbidden_hits = metrics.forbidden_hit(answer, item.forbidden_keywords)
    scores[FORBIDDEN_HIT] = flag(hit)
    scores[FALLBACK_CORRECT] = flag(metrics.fallback_correct(item.expect_fallback, response.get("fallback_yn", "")))

    # 검색 지표: 관련 문서 목록 전체(documents, 재정렬 순) 기준
    expected = [(source.doc_name, source.section_path) for source in item.expected_sources]
    retrieved = [(_name(document), _section(document)) for document in documents]
    for k in config.hit_k:
        scores[hit_key(k)] = flag(metrics.hit_at_k(expected, retrieved, k))
    scores[MRR] = metrics.mrr(expected, retrieved)
    result.matched_ranks = {
        rank for rank, document in enumerate(retrieved, start=1)
        if any(metrics.source_matches(source, document) for source in expected)
    }

    # 근거·환각 지표: fallback(관련 자료 없음) 건은 근거가 없으므로 N/A
    if not fallback:
        grounding = [text for document in used
                     for text in (str(document.get("content") or ""), _name(document), _section(document))]
        count, result.hallucinated = metrics.citation_hallucination(answer, grounding)
        scores[CITATION_HALLUCINATION] = float(count)
        scores[REASON_FILLED] = flag(metrics.reason_filled(response.get("reason", "")))

    # 출처 ID: 근거 청크가 기대 출처와 일치하면(source_matches) 그 기대 출처의 ID 를 쓴다
    # → ID 기반 지표가 Hit@k 와 같은 일치 규칙(목차 일부만 적어도 일치)을 따른다
    def retrieved_id(document: dict) -> str:
        pair = (_name(document), _section(document))
        for source in expected:
            if metrics.source_matches(source, pair):
                return metrics.source_id(*source)
        return metrics.source_id(*pair)

    return ragas_eval.RagasSample(
        id=item.id,
        question=item.question,
        answer=answer,
        reference=item.reference,
        retrieved_contexts=tuple(str(document.get("content") or "") for document in used),
        reference_contexts=item.reference_contexts,
        retrieved_ids=tuple(dict.fromkeys(retrieved_id(document) for document in used)),
        reference_ids=tuple(dict.fromkeys(metrics.source_id(*source) for source in expected)),
        fallback=fallback,
    )


def _name(document: dict) -> str:
    return str(document.get("doc_name") or "")


def _section(document: dict) -> str:
    return str(document.get("section_path") or "")


# ── 요약 ──

GROUP_ALL = "전체"


@dataclass
class GroupSummary:
    name: str                                  # 전체 / MANUAL / LAW / …
    count: int = 0
    errors: int = 0
    fallbacks: int = 0
    mean_elapsed: float | None = None
    means: dict[str, float | None] = field(default_factory=dict)   # 지표별 평균 (N/A 제외)
    scored: dict[str, int] = field(default_factory=dict)           # 지표별 평균에 들어간 건수


def summarize(results: list[ItemResult], defs: list[MetricDef]) -> list[GroupSummary]:
    """전체 + 검색대상별 요약. 평균은 N/A 를 제외하고 계산한다."""
    doc_types = list(dict.fromkeys(result.item.doc_type for result in results))
    groups = [(GROUP_ALL, results)] + [
        (doc_type, [result for result in results if result.item.doc_type == doc_type]) for doc_type in doc_types
    ]
    summaries = []
    for name, members in groups:
        summary = GroupSummary(name=name, count=len(members))
        summary.errors = sum(1 for result in members if result.error_code)
        summary.fallbacks = sum(1 for result in members if not result.error_code and result.fallback)
        elapsed = [result.elapsed_seconds for result in members if result.elapsed_seconds is not None]
        summary.mean_elapsed = sum(elapsed) / len(elapsed) if elapsed else None
        for definition in defs:
            values = [result.metrics.get(definition.key) for result in members]
            values = [value for value in values if value is not None]
            summary.scored[definition.key] = len(values)
            summary.means[definition.key] = sum(values) / len(values) if values else None
        summaries.append(summary)
    return summaries
