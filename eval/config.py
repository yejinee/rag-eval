"""
평가 설정 로드.

.env 의 EVAL_* 키를 읽어 EvalConfig 로 만든다.
우선순위: CLI 옵션 > OS 환경변수 > .env 파일 > 기본값

※ 이 모듈은 LLM 관련 패키지를 import 하지 않는다. Judge 설정은 문자열로만 보관하고,
  실제 Judge 생성은 eval/judge.py 에서 LLM ON 일 때만 한다.
"""
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

# 저장소 루트의 .env (eval/ 의 상위 폴더)
DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

_TRUE_VALUES = {"true", "1", "y", "yes", "on"}
_FALSE_VALUES = {"false", "0", "n", "no", "off", ""}


class ConfigError(Exception):
    """설정값 오류. 메시지에 문제 항목을 모두 담는다."""


@dataclass(frozen=True)
class EvalConfig:
    # 평가 대상 API
    api_url: str
    api_concurrency: int
    api_timeout_seconds: float
    hit_k: tuple[int, ...]
    # 평가 모드
    use_llm: bool
    # Judge (LLM ON 일 때만 사용)
    judge_base_url: str
    judge_api_key: str
    judge_model: str
    judge_temperature: float
    judge_concurrency: int
    judge_timeout_seconds: float

    @property
    def mode_label(self) -> str:
        """결과 파일명·실행정보에 쓰는 모드 표기."""
        return "LLM" if self.use_llm else "NOLLM"


def load_config(
    *,
    use_llm: bool | None = None,
    api_url: str | None = None,
    api_concurrency: int | None = None,
    env: Mapping[str, str] | None = None,
    env_file: Path | str | None = None,
) -> EvalConfig:
    """
    설정을 읽어 EvalConfig 를 만든다.

    use_llm / api_url / api_concurrency: CLI 옵션 값. None 이 아니면 .env 보다 우선한다.
    env: 테스트용. 주면 OS 환경변수와 .env 파일을 읽지 않고 이 값만 쓴다.
    """
    if env is None:
        file_values = dotenv_values(env_file or DEFAULT_ENV_FILE)
        env = {**{k: v or "" for k, v in file_values.items()}, **os.environ}

    errors: list[str] = []

    def text(key: str, default: str = "") -> str:
        return (env.get(key) or default).strip()

    def integer(key: str, default: int, override: int | None = None) -> int:
        raw = str(override) if override is not None else text(key, str(default))
        try:
            value = int(raw)
        except ValueError:
            errors.append(f"{key}: 정수가 아닙니다 ({raw!r})")
            return default
        if value < 1:
            errors.append(f"{key}: 1 이상이어야 합니다 ({value})")
        return value

    def number(key: str, default: float, *, minimum: float, inclusive: bool) -> float:
        raw = text(key, str(default))
        try:
            value = float(raw)
        except ValueError:
            errors.append(f"{key}: 숫자가 아닙니다 ({raw!r})")
            return default
        if value < minimum or (not inclusive and value == minimum):
            errors.append(f"{key}: {minimum} {'이상' if inclusive else '초과'}이어야 합니다 ({value})")
        return value

    def boolean(key: str) -> bool:
        raw = text(key).lower()
        if raw in _TRUE_VALUES:
            return True
        if raw not in _FALSE_VALUES:
            errors.append(f"{key}: true/false 가 아닙니다 ({raw!r})")
        return False

    def hit_k() -> tuple[int, ...]:
        raw = text("EVAL_HIT_K", "3,5")
        try:
            values = sorted({int(part) for part in raw.split(",") if part.strip()})
        except ValueError:
            errors.append(f"EVAL_HIT_K: 쉼표로 구분한 정수여야 합니다 ({raw!r})")
            return (3, 5)
        if not values or values[0] < 1:
            errors.append(f"EVAL_HIT_K: 1 이상의 정수가 하나 이상 필요합니다 ({raw!r})")
        return tuple(values)

    url = (api_url if api_url is not None else text("EVAL_API_URL", "http://localhost:8000")).strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        errors.append(f"EVAL_API_URL: http:// 또는 https:// 로 시작해야 합니다 ({url!r})")

    config = EvalConfig(
        api_url=url,
        api_concurrency=integer("EVAL_API_CONCURRENCY", 2, api_concurrency),
        api_timeout_seconds=number("EVAL_API_TIMEOUT_SECONDS", 120, minimum=0, inclusive=False),
        hit_k=hit_k(),
        use_llm=use_llm if use_llm is not None else boolean("EVAL_USE_LLM"),
        judge_base_url=text("EVAL_JUDGE_BASE_URL"),
        judge_api_key=text("EVAL_JUDGE_API_KEY"),
        judge_model=text("EVAL_JUDGE_MODEL"),
        judge_temperature=number("EVAL_JUDGE_TEMPERATURE", 0, minimum=0, inclusive=True),
        judge_concurrency=integer("EVAL_JUDGE_CONCURRENCY", 2),
        judge_timeout_seconds=number("EVAL_JUDGE_TIMEOUT_SECONDS", 180, minimum=0, inclusive=False),
    )
    if errors:
        raise ConfigError("평가 설정 오류:\n" + "\n".join(f" - {e}" for e in errors))
    return config


def ensure_judge_ready(config: EvalConfig) -> None:
    """
    LLM ON 인데 Judge 접속 정보가 비어 있으면 ConfigError 를 던진다.
    API 호출(수집)을 시작하기 전에 불러, 수집을 다 하고 나서 채점에서 실패하는 일을 막는다.
    LLM OFF 면 아무것도 확인하지 않는다 (Judge 설정이 비어 있어도 정상).

    ※ API 키는 필수로 보지 않는다 (키 없이 열려 있는 사내 서버 대비).
    """
    if not config.use_llm:
        return
    missing = [
        key
        for key, value in (
            ("EVAL_JUDGE_BASE_URL", config.judge_base_url),
            ("EVAL_JUDGE_MODEL", config.judge_model),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "LLM 채점 모드(EVAL_USE_LLM=true 또는 --llm)인데 Judge 설정이 비어 있습니다: "
            + ", ".join(missing)
            + "\n.env 에 값을 채우거나 --no-llm 으로 실행하세요."
        )
