"""평가 설정 로드 테스트 (.env 값 해석, CLI 우선, Judge 설정 확인)."""
import pytest

from eval.config import ConfigError, ensure_judge_ready, load_config


def test_기본값():
    config = load_config(env={})
    assert config.api_url == "http://localhost:8000"
    assert config.api_concurrency == 2
    assert config.api_timeout_seconds == 120
    assert config.hit_k == (3, 5)
    assert config.use_llm is False
    assert config.mode_label == "NOLLM"
    assert config.judge_concurrency == 2
    assert config.judge_timeout_seconds == 180


def test_env_값_해석():
    config = load_config(env={
        "EVAL_API_URL": " http://10.0.0.5:8000/ ",
        "EVAL_API_CONCURRENCY": "4",
        "EVAL_HIT_K": "5, 1,3,3",
        "EVAL_USE_LLM": "TRUE",
        "EVAL_JUDGE_MODEL": "judge-model",
    })
    assert config.api_url == "http://10.0.0.5:8000"   # 공백·끝 슬래시 제거
    assert config.api_concurrency == 4
    assert config.hit_k == (1, 3, 5)                  # 정렬·중복 제거
    assert config.use_llm is True
    assert config.mode_label == "LLM"
    assert config.judge_model == "judge-model"


def test_CLI_옵션이_env_보다_우선():
    env = {"EVAL_USE_LLM": "true", "EVAL_API_URL": "http://a:1", "EVAL_API_CONCURRENCY": "8"}
    config = load_config(env=env, use_llm=False, api_url="http://b:2", api_concurrency=1)
    assert config.use_llm is False
    assert config.api_url == "http://b:2"
    assert config.api_concurrency == 1


def test_잘못된_값은_모아서_보고():
    with pytest.raises(ConfigError) as error:
        load_config(env={
            "EVAL_API_URL": "localhost:8000",
            "EVAL_API_CONCURRENCY": "0",
            "EVAL_API_TIMEOUT_SECONDS": "abc",
            "EVAL_HIT_K": "3,x",
            "EVAL_USE_LLM": "maybe",
        })
    message = str(error.value)
    for key in ("EVAL_API_URL", "EVAL_API_CONCURRENCY", "EVAL_API_TIMEOUT_SECONDS", "EVAL_HIT_K", "EVAL_USE_LLM"):
        assert key in message


def test_env_파일에서_읽기(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_API_URL", raising=False)
    monkeypatch.delenv("EVAL_HIT_K", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("EVAL_API_URL=http://file:9000\nEVAL_HIT_K=1,10   # 주석\n", encoding="utf-8")
    config = load_config(env_file=env_file)
    assert config.api_url == "http://file:9000"
    assert config.hit_k == (1, 10)


def test_OFF_모드는_Judge_설정이_비어도_통과():
    ensure_judge_ready(load_config(env={}))


def test_ON_모드인데_Judge_설정이_없으면_중단():
    with pytest.raises(ConfigError) as error:
        ensure_judge_ready(load_config(env={}, use_llm=True))
    assert "EVAL_JUDGE_BASE_URL" in str(error.value)
    assert "EVAL_JUDGE_MODEL" in str(error.value)


def test_ON_모드에서_Judge_설정이_있으면_통과():
    config = load_config(env={
        "EVAL_USE_LLM": "true",
        "EVAL_JUDGE_BASE_URL": "http://llm:8080/v1",
        "EVAL_JUDGE_MODEL": "judge-model",
    })
    ensure_judge_ready(config)
