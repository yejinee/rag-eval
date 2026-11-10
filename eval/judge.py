"""
채점용 LLM(Judge) 생성.

※ 이 모듈은 LLM ON 일 때만 import 된다 (ragas_eval.score_llm 안에서 지연 import).
  Judge 를 만드는 코드는 이 파일 한 곳에만 둔다.
"""
import asyncio
import logging
import warnings

from langchain_openai import ChatOpenAI
from ragas.llms.base import LangchainLLMWrapper   # ragas.llms 의 같은 이름은 상속할 수 없는 경고용 대리 객체
from ragas.run_config import RunConfig

from eval.config import EvalConfig

logger = logging.getLogger(__name__)

# Judge 호출 실패(타임아웃·일시 오류) 시 시도 횟수. ragas 기본 10회는 사내 서버에 과하다
JUDGE_MAX_ATTEMPTS = 2
JUDGE_RETRY_MAX_WAIT_SECONDS = 20

# 키 없이 열려 있는 서버용 자리표시자 (openai 클라이언트가 빈 키를 거부한다)
_EMPTY_API_KEY = "EMPTY"


class _ConcurrencyLimitedLLM(LangchainLLMWrapper):
    """Judge 서버로 동시에 나가는 요청 수를 제한하는 래퍼 (사내 LLM 서버 보호)."""

    def __init__(self, *args, concurrency: int, **kwargs):
        super().__init__(*args, **kwargs)
        self._semaphore = asyncio.Semaphore(concurrency)

    async def agenerate_text(self, *args, **kwargs):
        async with self._semaphore:
            return await super().agenerate_text(*args, **kwargs)


def build_judge(config: EvalConfig) -> LangchainLLMWrapper:
    """EVAL_JUDGE_* 설정으로 ragas 용 Judge LLM 을 만든다."""
    chat = ChatOpenAI(
        base_url=config.judge_base_url,
        api_key=config.judge_api_key or _EMPTY_API_KEY,
        model=config.judge_model,
        temperature=config.judge_temperature,
        timeout=config.judge_timeout_seconds,
        max_retries=0,   # 재시도는 RunConfig 한 곳에서만 한다
    )
    run_config = RunConfig(
        timeout=int(config.judge_timeout_seconds),
        max_retries=JUDGE_MAX_ATTEMPTS,
        max_wait=JUDGE_RETRY_MAX_WAIT_SECONDS,
        max_workers=config.judge_concurrency,
    )
    logger.info("Judge 생성: 모델 %s, 동시성 %d, 타임아웃 %s초",
                config.judge_model, config.judge_concurrency, config.judge_timeout_seconds)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)   # ragas 의 래퍼 교체 예고 경고는 숨긴다
        return _ConcurrencyLimitedLLM(
            chat,
            run_config=run_config,
            bypass_temperature=True,   # ragas 가 온도를 0.01 로 덮어쓰지 않게 한다 (EVAL_JUDGE_TEMPERATURE 유지)
            concurrency=config.judge_concurrency,
        )
