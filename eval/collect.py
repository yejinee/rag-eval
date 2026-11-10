"""
수집: 골든셋 질문으로 RAG API(POST /query/sync)를 호출해 raw JSONL 로 저장한다.

- 채점과 분리돼 있다. 여기서는 응답을 있는 그대로 저장만 한다 (rescore 로 재채점 가능)
- 한 건이 오류·타임아웃이어도 전체를 중단하지 않는다. 해당 건은 error_code 를 기록한다
- 동시 호출 수는 EVAL_API_CONCURRENCY 로 제한한다 (사내 LLM 서버 보호)
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx

from eval.config import EvalConfig
from eval.loader import GoldenItem

logger = logging.getLogger(__name__)

QUERY_PATH = "/query/sync"

# 평가 도구가 붙이는 오류 코드 (API 가 준 error_code 가 있으면 그 값을 쓴다)
ERR_TIMEOUT = "EVAL_TIMEOUT"                 # 응답 대기 시간 초과
ERR_CONNECT = "EVAL_CONNECT_ERROR"           # 서버 연결 실패
ERR_BAD_RESPONSE = "EVAL_BAD_RESPONSE"       # 200 인데 JSON 이 아니거나 형식이 다름
ERR_NO_RAW = "EVAL_NO_RAW"                   # rescore 시 수집 결과에 없는 ID


def collect(items: list[GoldenItem], config: EvalConfig, raw_path: Path) -> list[dict]:
    """모든 골든셋 항목을 호출해 raw_path(JSONL)에 저장하고, 골든셋 순서대로 레코드를 돌려준다."""
    return asyncio.run(_collect(items, config, Path(raw_path)))


def load_raw(raw_path: Path | str) -> dict[str, dict]:
    """raw JSONL 을 읽어 {골든셋 ID: 레코드} 로 돌려준다. 같은 ID 가 여러 번이면 마지막 것을 쓴다."""
    records: dict[str, dict] = {}
    with open(raw_path, encoding="utf-8") as file:
        for line in file:
            if line.strip():
                record = json.loads(line)
                records[record["id"]] = record
    return records


def _build_client(config: EvalConfig) -> httpx.AsyncClient:
    """API 호출용 클라이언트 (테스트에서 가짜 응답으로 바꿔 끼우는 지점)."""
    return httpx.AsyncClient(base_url=config.api_url, timeout=config.api_timeout_seconds)


async def _collect(items: list[GoldenItem], config: EvalConfig, raw_path: Path) -> list[dict]:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(config.api_concurrency)
    total = len(items)
    done = 0
    records: dict[str, dict] = {}

    logger.info("수집 시작: %d건, 동시성 %d, API %s", total, config.api_concurrency, config.api_url)
    async with _build_client(config) as client:
        with open(raw_path, "w", encoding="utf-8") as file:

            async def run_one(item: GoldenItem) -> None:
                nonlocal done
                async with semaphore:
                    record = await _call_api(client, item)
                # 끝나는 대로 한 줄씩 기록한다 (중간에 끊겨도 받은 만큼은 남는다)
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
                file.flush()
                records[item.id] = record
                done += 1
                if record["error_code"]:
                    logger.warning("수집 %d/%d  %s  오류 %s (%.1f초)",
                                   done, total, item.id, record["error_code"], record["elapsed_seconds"])
                else:
                    logger.info("수집 %d/%d  %s  (%.1f초)", done, total, item.id, record["elapsed_seconds"])

            await asyncio.gather(*(run_one(item) for item in items))

    errors = sum(1 for record in records.values() if record["error_code"])
    logger.info("수집 완료: %d건 중 오류 %d건 → %s", total, errors, raw_path)
    return [records[item.id] for item in items]


async def _call_api(client: httpx.AsyncClient, item: GoldenItem) -> dict:
    """API 1회 호출. 어떤 실패도 예외로 올리지 않고 레코드의 error_code 로 남긴다."""
    payload = {"question": item.question, "doc_type": item.doc_type, "history": list(item.history)}
    record = {
        "id": item.id,
        "doc_type": item.doc_type,
        "question": item.question,
        "requested_at": datetime.now().isoformat(timespec="seconds"),
        "http_status": None,
        "error_code": "",
        "error_message": "",
        "elapsed_seconds": 0.0,
        "response": None,
    }
    started = time.perf_counter()
    try:
        response = await client.post(QUERY_PATH, json=payload)
        record["http_status"] = response.status_code
        body = _json_or_none(response)
        if response.status_code == 200:
            if isinstance(body, dict) and "answer" in body:
                record["response"] = body
            else:
                record["error_code"] = ERR_BAD_RESPONSE
                record["error_message"] = response.text[:500]
        else:
            # API 오류 규격: {request_id, error_code, message}
            body = body if isinstance(body, dict) else {}
            record["error_code"] = str(body.get("error_code") or f"HTTP_{response.status_code}")
            record["error_message"] = str(body.get("message") or response.text[:500])
            record["request_id"] = body.get("request_id", "")
    except httpx.TimeoutException:
        record["error_code"] = ERR_TIMEOUT
        record["error_message"] = "응답 대기 시간 초과"
    except httpx.HTTPError as error:
        record["error_code"] = ERR_CONNECT
        record["error_message"] = f"{type(error).__name__}: {error}"
    record["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return record


def _json_or_none(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return None
