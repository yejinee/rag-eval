"""
RAGAS 지표 실행.

- Non-LLM 지표(문자열 유사도·ID 비교)는 항상 실행한다. LLM·임베딩·네트워크 호출이 없다
- LLM 지표는 score_llm() 을 부를 때만 실행한다. Judge(eval/judge.py)는 그 안에서 지연 import 한다
  → LLM OFF 모드에서는 judge 모듈이 import 되지도, Judge 가 만들어지지도 않는다

기대값이 없거나 계산에 실패한 지표는 0 이 아니라 None(N/A)이다.
"""
import asyncio
import logging
import math
import os
import warnings
from collections.abc import Callable
from dataclasses import dataclass

from eval.config import EvalConfig

# ragas 사용 통계 전송 끄기 — ragas 를 import 하기 전에 설정해야 한다
os.environ["RAGAS_DO_NOT_TRACK"] = "true"

logger = logging.getLogger(__name__)

# ── 지표 키 ──
NON_LLM_CONTEXT_RECALL = "non_llm_context_recall"
NON_LLM_CONTEXT_PRECISION = "non_llm_context_precision"
ID_CONTEXT_RECALL = "id_context_recall"
ID_CONTEXT_PRECISION = "id_context_precision"
ANSWER_SIMILARITY = "answer_similarity"

FAITHFULNESS = "faithfulness"
LLM_CONTEXT_RECALL = "llm_context_recall"
LLM_CONTEXT_PRECISION = "llm_context_precision"
FACTUAL_CORRECTNESS = "factual_correctness"

NON_LLM_KEYS = (NON_LLM_CONTEXT_RECALL, NON_LLM_CONTEXT_PRECISION, ID_CONTEXT_RECALL, ID_CONTEXT_PRECISION,
                ANSWER_SIMILARITY)
LLM_KEYS = (FAITHFULNESS, LLM_CONTEXT_RECALL, LLM_CONTEXT_PRECISION, FACTUAL_CORRECTNESS)


@dataclass(frozen=True)
class RagasSample:
    """RAGAS 채점 입력 1건 (API 응답이 정상인 건만 만든다)."""
    id: str
    question: str
    answer: str
    reference: str                        # 모범답안
    retrieved_contexts: tuple[str, ...]   # 답변 근거로 쓰인 청크 본문 (used_yn == "Y")
    reference_contexts: tuple[str, ...]   # 정답근거
    retrieved_ids: tuple[str, ...]        # 근거 청크의 출처 ID
    reference_ids: tuple[str, ...]        # 기대 출처 ID
    fallback: bool                        # fallback_yn == "Y"


# ── 지표별 계산 조건: False 면 채점하지 않고 N/A ──
# fallback(관련 자료 없음) 건은 근거가 없으므로 근거 기반 지표를 계산하지 않는다
def _has_contexts(sample: RagasSample) -> bool:
    return not sample.fallback and bool(sample.retrieved_contexts)


_REQUIREMENTS: dict[str, Callable[[RagasSample], bool]] = {
    NON_LLM_CONTEXT_RECALL: lambda s: _has_contexts(s) and bool(s.reference_contexts),
    NON_LLM_CONTEXT_PRECISION: lambda s: _has_contexts(s) and bool(s.reference_contexts),
    ID_CONTEXT_RECALL: lambda s: not s.fallback and bool(s.retrieved_ids) and bool(s.reference_ids),
    ID_CONTEXT_PRECISION: lambda s: not s.fallback and bool(s.retrieved_ids) and bool(s.reference_ids),
    ANSWER_SIMILARITY: lambda s: bool(s.answer.strip()) and bool(s.reference.strip()),
    FAITHFULNESS: lambda s: _has_contexts(s) and bool(s.answer.strip()),
    LLM_CONTEXT_RECALL: lambda s: _has_contexts(s) and bool(s.reference.strip()),
    LLM_CONTEXT_PRECISION: lambda s: _has_contexts(s) and bool(s.reference.strip()),
    FACTUAL_CORRECTNESS: lambda s: bool(s.answer.strip()) and bool(s.reference.strip()),
}


@dataclass
class LLMScoreResult:
    scores: dict[str, dict[str, float | None]]   # {샘플 ID: {지표 키: 점수}}
    attempts: dict[str, int]                     # 지표별 채점 시도 건수
    failures: dict[str, int]                     # 지표별 실패(NaN·오류) 건수
    errors: dict[str, list[str]]                 # {샘플 ID: ["지표: 사유", ...]}


def score_non_llm(samples: list[RagasSample]) -> dict[str, dict[str, float | None]]:
    """Non-LLM RAGAS 지표를 계산한다. {샘플 ID: {지표 키: 점수 | None}}"""
    if not samples:
        return {}
    return asyncio.run(_score_non_llm(samples))


def score_llm(samples: list[RagasSample], config: EvalConfig) -> LLMScoreResult:
    """LLM RAGAS 지표를 Judge 로 채점한다. (LLM ON 일 때만 호출할 것)"""
    return asyncio.run(_score_llm(samples, config))


# ── 내부 구현 ──

def _build_non_llm_metrics() -> dict:
    from ragas.metrics import (
        IDBasedContextPrecision,
        IDBasedContextRecall,
        NonLLMContextPrecisionWithReference,
        NonLLMContextRecall,
        NonLLMStringSimilarity,
    )
    return {
        NON_LLM_CONTEXT_RECALL: NonLLMContextRecall(),
        NON_LLM_CONTEXT_PRECISION: NonLLMContextPrecisionWithReference(),
        ID_CONTEXT_RECALL: IDBasedContextRecall(),
        ID_CONTEXT_PRECISION: IDBasedContextPrecision(),
        ANSWER_SIMILARITY: NonLLMStringSimilarity(),
    }


def _build_llm_metrics(config: EvalConfig) -> dict:
    """Judge 를 만들어 LLM 지표에 연결한다. judge 모듈은 여기서 처음 import 된다."""
    from ragas.metrics import (
        FactualCorrectness,
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
    )

    from eval.judge import build_judge

    judge = build_judge(config)
    return {
        FAITHFULNESS: Faithfulness(llm=judge),
        LLM_CONTEXT_RECALL: LLMContextRecall(llm=judge),
        LLM_CONTEXT_PRECISION: LLMContextPrecisionWithReference(llm=judge),
        FACTUAL_CORRECTNESS: FactualCorrectness(llm=judge),
    }


def _to_ragas_sample(sample: RagasSample):
    from ragas.dataset_schema import SingleTurnSample

    return SingleTurnSample(
        user_input=sample.question,
        response=sample.answer,
        reference=sample.reference or None,
        retrieved_contexts=list(sample.retrieved_contexts),
        reference_contexts=list(sample.reference_contexts) or None,
        retrieved_context_ids=list(sample.retrieved_ids) or None,
        reference_context_ids=list(sample.reference_ids) or None,
    )


async def _score_one(metric, ragas_sample) -> tuple[float | None, str]:
    """지표 1개 × 샘플 1건. (점수, 실패 사유) — 성공이면 사유는 빈 문자열."""
    try:
        value = await metric.single_turn_ascore(ragas_sample)
    except Exception as error:  # noqa: BLE001 - 한 건의 실패가 전체 채점을 멈추면 안 된다
        return None, f"{type(error).__name__}: {error}"[:300]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None, "NaN (채점 결과 해석 실패)"
    return float(value), ""


async def _score_non_llm(samples: list[RagasSample]) -> dict[str, dict[str, float | None]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        metrics = _build_non_llm_metrics()
        scores: dict[str, dict[str, float | None]] = {}
        for sample in samples:
            ragas_sample = _to_ragas_sample(sample)
            row: dict[str, float | None] = {}
            for key, metric in metrics.items():
                if not _REQUIREMENTS[key](sample):
                    row[key] = None
                    continue
                row[key], reason = await _score_one(metric, ragas_sample)
                if reason:
                    logger.warning("지표 계산 실패 → N/A: %s / %s / %s", sample.id, key, reason)
            scores[sample.id] = row
    return scores


async def _score_llm(samples: list[RagasSample], config: EvalConfig) -> LLMScoreResult:
    result = LLMScoreResult(
        scores={},
        attempts={key: 0 for key in LLM_KEYS},
        failures={key: 0 for key in LLM_KEYS},
        errors={},
    )
    if not samples:
        return result

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        metrics = _build_llm_metrics(config)

    semaphore = asyncio.Semaphore(config.judge_concurrency)
    total = len(samples)
    done = 0
    logger.info("LLM 채점 시작: %d건, Judge 동시성 %d", total, config.judge_concurrency)

    async def run_one(sample: RagasSample) -> None:
        nonlocal done
        row: dict[str, float | None] = {}
        errors: list[str] = []
        async with semaphore:
            ragas_sample = _to_ragas_sample(sample)
            for key, metric in metrics.items():
                if not _REQUIREMENTS[key](sample):
                    row[key] = None
                    continue
                result.attempts[key] += 1
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    row[key], reason = await _score_one(metric, ragas_sample)
                if reason:
                    result.failures[key] += 1
                    errors.append(f"{key}: {reason}")
        result.scores[sample.id] = row
        if errors:
            result.errors[sample.id] = errors
        done += 1
        if errors:
            logger.warning("LLM 채점 %d/%d  %s  실패 지표 %d개", done, total, sample.id, len(errors))
        else:
            logger.info("LLM 채점 %d/%d  %s", done, total, sample.id)

    await asyncio.gather(*(run_one(sample) for sample in samples))
    logger.info("LLM 채점 완료: 지표별 실패(NaN·오류) 건수 %s", result.failures)
    return result
