"""테스트 공통 도구: 골든셋 파일 만들기, 가짜 API, 환경변수 격리."""
import os

import httpx
import pytest
from openpyxl import load_workbook

from eval.template import COLUMNS, SHEET_GOLDEN, create_template


def write_golden(directory, rows: list[dict], name: str = "golden.xlsx"):
    """빈 템플릿을 만들고 rows(열 이름 → 값)를 2행부터 채워 저장한다."""
    path = create_template(directory / name)
    workbook = load_workbook(path)
    sheet = workbook[SHEET_GOLDEN]
    for row_number, row in enumerate(rows, start=2):
        for column_name, value in row.items():
            sheet.cell(row=row_number, column=COLUMNS.index(column_name) + 1, value=value)
    workbook.save(path)
    return path


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """실제 .env 와 OS 환경변수의 EVAL_* 가 테스트에 섞이지 않게 한다."""
    monkeypatch.setattr("eval.config.DEFAULT_ENV_FILE", tmp_path / "없는.env")
    for key in list(os.environ):
        if key.startswith("EVAL_"):
            monkeypatch.delenv(key)


@pytest.fixture
def fake_api(monkeypatch):
    """
    실제 API 대신 handler(question, payload) 로 응답하게 한다.
    handler 는 (status_code, json_body) 를 돌려주거나 예외를 던진다. 호출 기록은 calls 로 확인한다.
    """
    calls: list[dict] = []

    def install(handler):
        def respond(request: httpx.Request) -> httpx.Response:
            import json

            payload = json.loads(request.content)
            calls.append(payload)
            status, body = handler(payload["question"], payload)
            return httpx.Response(status, json=body)

        def build_client(config):
            return httpx.AsyncClient(base_url=config.api_url, transport=httpx.MockTransport(respond))

        monkeypatch.setattr("eval.collect._build_client", build_client)
        return calls

    return install
