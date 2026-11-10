"""
골든셋 평가 CLI.

    python -m eval.run template --out golden_template.xlsx
    python -m eval.run evaluate --input 골든셋.xlsx [--llm | --no-llm] [--api URL] [--concurrency N]
    python -m eval.run rescore  --raw eval/results/<run_id>/raw.jsonl --input 골든셋.xlsx [--llm | --no-llm]

evaluate = 수집(API 호출 → raw.jsonl) + 채점 + 결과 엑셀
rescore  = 저장된 raw.jsonl 을 API 재호출 없이 다시 채점 (예: NOLLM 으로 돌린 결과를 LLM 으로 재채점)
"""
import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from eval.config import ConfigError, EvalConfig, ensure_judge_ready, load_config
from eval.loader import GoldenLoadError, load_golden

logger = logging.getLogger("eval")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"
RAW_FILE_NAME = "raw.jsonl"
META_FILE_NAME = "meta.json"

EXIT_OK = 0
EXIT_INPUT_ERROR = 2   # 설정·골든셋·파일 오류


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    # 외부 라이브러리의 요청 단위 로그는 진행률 로그를 가리므로 줄인다
    for noisy in ("httpx", "httpcore", "openai", "ragas"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return args.handler(args)
    except (ConfigError, GoldenLoadError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_INPUT_ERROR
    except PermissionError as error:
        print(f"파일에 쓸 수 없습니다 (엑셀에서 열려 있으면 닫고 다시 실행하세요): {error.filename}", file=sys.stderr)
        return EXIT_INPUT_ERROR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eval.run", description="골든셋 기반 RAG 품질 평가")
    commands = parser.add_subparsers(dest="command", required=True)

    template = commands.add_parser("template", help="빈 골든셋 템플릿 생성")
    template.add_argument("--out", default="golden_template.xlsx", help="저장할 파일 경로")
    template.set_defaults(handler=_run_template)

    evaluate = commands.add_parser("evaluate", help="API 호출 → 채점 → 결과 엑셀")
    evaluate.add_argument("--input", required=True, help="골든셋 엑셀 경로")
    evaluate.add_argument("--api", help="평가 대상 API 주소 (.env 의 EVAL_API_URL 보다 우선)")
    evaluate.add_argument("--concurrency", type=int, help="API 동시 호출 수 (.env 의 EVAL_API_CONCURRENCY 보다 우선)")
    evaluate.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR), help="결과 폴더 (기본 eval/results)")
    _add_llm_switch(evaluate)
    evaluate.set_defaults(handler=_run_evaluate)

    rescore = commands.add_parser("rescore", help="저장된 raw.jsonl 을 API 재호출 없이 다시 채점")
    rescore.add_argument("--raw", required=True, help="수집 결과 경로 (eval/results/<run_id>/raw.jsonl)")
    rescore.add_argument("--input", required=True, help="골든셋 엑셀 경로")
    _add_llm_switch(rescore)
    rescore.set_defaults(handler=_run_rescore)
    return parser


def _add_llm_switch(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--llm", dest="use_llm", action="store_const", const=True, default=None,
                       help="LLM 채점 지표 추가 (.env 의 EVAL_USE_LLM 보다 우선)")
    group.add_argument("--no-llm", dest="use_llm", action="store_const", const=False,
                       help="LLM 채점 지표 없이 실행 (.env 의 EVAL_USE_LLM 보다 우선)")


# ── 명령 ──

def _run_template(args: argparse.Namespace) -> int:
    from eval.template import create_template

    path = create_template(args.out)
    print(f"템플릿 생성: {path}")
    return EXIT_OK


def _run_evaluate(args: argparse.Namespace) -> int:
    from eval.collect import collect

    config = load_config(use_llm=args.use_llm, api_url=args.api, api_concurrency=args.concurrency)
    ensure_judge_ready(config)   # LLM ON 인데 Judge 설정이 없으면 API 호출 전에 중단
    items = load_golden(args.input)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.results_dir) / run_id
    raw_path = run_dir / RAW_FILE_NAME

    started = time.perf_counter()
    records = collect(items, config, raw_path)
    meta = {
        "run_id": run_id,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "api_url": config.api_url,
        "api_concurrency": config.api_concurrency,
        "api_timeout_seconds": config.api_timeout_seconds,
        "input_file": Path(args.input).name,
        "collect_seconds": round(time.perf_counter() - started, 1),
    }
    (run_dir / META_FILE_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    raws = {record["id"]: record for record in records}
    return _score_and_report(items, raws, config, run_dir, meta, input_file=Path(args.input).name)


def _run_rescore(args: argparse.Namespace) -> int:
    from eval.collect import load_raw

    raw_path = Path(args.raw)
    if not raw_path.is_file():
        print(f"수집 결과 파일을 찾을 수 없습니다: {raw_path}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    config = load_config(use_llm=args.use_llm)
    ensure_judge_ready(config)
    items = load_golden(args.input)

    run_dir = raw_path.parent
    meta_path = run_dir / META_FILE_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {"run_id": run_dir.name}
    logger.info("재채점: %s (API 재호출 없음)", raw_path)
    return _score_and_report(items, load_raw(raw_path), config, run_dir, meta, input_file=Path(args.input).name)


def _score_and_report(items, raws: dict, config: EvalConfig, run_dir: Path, meta: dict, *, input_file: str) -> int:
    # ragas 를 불러오는 모듈이라 template 명령에서는 import 하지 않도록 여기서 가져온다
    from eval.report import write_report
    from eval.scoring import all_metric_defs, metric_defs, score_all

    started = time.perf_counter()
    logger.info("채점 시작: %d건, 모드 %s", len(items), config.mode_label)
    output = score_all(items, raws, config)
    score_seconds = round(time.perf_counter() - started, 1)

    run_id = meta.get("run_id") or run_dir.name
    models = sorted({str(result.response.get("model")) for result in output.results if result.response.get("model")})
    run_info = {
        "run_id": run_id,
        "채점 일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "수집 일시": meta.get("collected_at", ""),
        "평가 모드": f"{config.mode_label} ({'LLM 채점 지표 포함' if config.use_llm else 'LLM 호출 없음'})",
        "Judge 모델": config.judge_model if config.use_llm else "(사용 안 함)",
        "Judge 서버": config.judge_base_url if config.use_llm else "(사용 안 함)",
        "답변 모델 (API 응답 model)": ", ".join(models),
        "API URL": meta.get("api_url", ""),
        "API 동시성": meta.get("api_concurrency", ""),
        "Judge 동시성": config.judge_concurrency if config.use_llm else "(사용 안 함)",
        "수집 소요시간(초)": meta.get("collect_seconds", ""),
        "채점 소요시간(초)": score_seconds,
        "Hit@k": ", ".join(str(k) for k in config.hit_k),
        "입력 파일": input_file,
        "수집 당시 입력 파일": meta.get("input_file", ""),
        "git commit (rag-eval)": _git_commit(),
    }
    report_path = run_dir / f"평가결과_{run_id}_{config.mode_label}.xlsx"
    write_report(report_path, output, metric_defs(config), all_metric_defs(config.hit_k), run_info)

    errors = sum(1 for result in output.results if result.error_code)
    logger.info("완료: %d건 (오류 %d건), 채점 %.1f초", len(items), errors, score_seconds)
    print(f"결과 엑셀: {report_path}")
    return EXIT_OK


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "(확인 불가)"
    return completed.stdout.strip() or "(확인 불가)"


if __name__ == "__main__":
    sys.exit(main())
