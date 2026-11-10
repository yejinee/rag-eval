"""
RAGAS 가 제공하지만 이 도구가 계산하지 않는 지표 목록 (ragas 0.4.3 기준).

결과 엑셀 '실행정보' 시트의 지표 정의 표에 참고용으로 싣는다. 계산 로직과는 무관한 설명 자료다.
이 도구가 계산하는 지표의 정의는 scoring.all_metric_defs() 에 있다.

※ ragas 버전을 올리면 이 목록도 함께 확인할 것.
"""
from dataclasses import dataclass

from eval.scoring import CATEGORY_ANSWER, CATEGORY_OTHER, CATEGORY_RETRIEVAL


@dataclass(frozen=True)
class RagasMetricInfo:
    label: str              # 한글 이름
    ragas_name: str         # RAGAS 지표 클래스명
    description: str
    category: str
    needs_llm: bool         # 채점용 LLM(Judge) 필요 → LLM 미사용 시 계산 불가
    needs_embedding: bool   # 임베딩 모델 필요
    reason: str             # 이 도구에서 계산하지 않는 이유


_NEEDS_EMBEDDING = "임베딩 모델이 필요해 제외"
_NOT_RAG_QA = "RAG 질의응답 평가 대상이 아님"

UNSUPPORTED_RAGAS_METRICS = (
    # ── 답변 품질 ──
    RagasMetricInfo("답변 관련성", "ResponseRelevancy (= Answer Relevancy)",
                    "답변이 질문에 맞는 내용인지. 답변에서 거꾸로 질문을 만들어 원래 질문과의 임베딩 유사도를 잰다.",
                    CATEGORY_ANSWER, True, True, _NEEDS_EMBEDDING),
    RagasMetricInfo("답변 정확도(종합)", "AnswerCorrectness",
                    "사실 일치도(LLM)와 의미 유사도(임베딩)를 가중 평균한 점수.",
                    CATEGORY_ANSWER, True, True, _NEEDS_EMBEDDING + " (사실 일치도는 FactualCorrectness 로 계산)"),
    RagasMetricInfo("의미 유사도", "SemanticSimilarity (= Answer Similarity)",
                    "답변과 모범답안의 임베딩 코사인 유사도.",
                    CATEGORY_ANSWER, False, True, _NEEDS_EMBEDDING),
    RagasMetricInfo("답변 정확도(NVIDIA)", "AnswerAccuracy",
                    "답변이 모범답안과 일치하는지를 Judge 가 등급으로 매긴다.",
                    CATEGORY_ANSWER, True, False, "FactualCorrectness 와 용도가 겹쳐 제외"),
    RagasMetricInfo("답변 근거성(NVIDIA)", "ResponseGroundedness",
                    "답변이 근거에 기반했는지를 Judge 가 등급으로 매긴다.",
                    CATEGORY_ANSWER, True, False, "Faithfulness 와 용도가 겹쳐 제외"),
    RagasMetricInfo("충실도(HHEM)", "FaithfulnesswithHHEM",
                    "Faithfulness 의 근거 대조 단계를 별도 분류 모델(HHEM)로 수행한다.",
                    CATEGORY_ANSWER, True, False, "별도 분류 모델 반입이 필요해 제외"),
    RagasMetricInfo("노이즈 민감도", "NoiseSensitivity",
                    "근거에 무관한 문서가 섞였을 때 답변이 틀린 주장을 하는 비율.",
                    CATEGORY_ANSWER, True, False, "Judge 호출이 많아 이번 범위에서 제외"),
    RagasMetricInfo("기준 채점", "AspectCritic / SimpleCriteriaScore",
                    "사용자가 글로 적은 기준(예: '반말을 쓰지 않았는가')을 Judge 가 판정한다.",
                    CATEGORY_ANSWER, True, False, "평가 기준 문구를 따로 정의해야 해 제외"),
    RagasMetricInfo("루브릭 채점", "RubricsScore / InstanceRubrics",
                    "점수별 설명(루브릭)에 따라 Judge 가 1~5점 등으로 채점한다.",
                    CATEGORY_ANSWER, True, False, "루브릭을 따로 정의해야 해 제외"),
    RagasMetricInfo("완전 일치", "ExactMatch",
                    "답변이 모범답안과 글자까지 완전히 같으면 1.",
                    CATEGORY_ANSWER, False, False, "생성형 답변은 완전 일치할 수 없어 항상 0"),
    RagasMetricInfo("문자열 포함", "StringPresence",
                    "모범답안 문자열이 답변에 그대로 들어 있으면 1.",
                    CATEGORY_ANSWER, False, False, "필수키워드 포함률로 대체"),
    RagasMetricInfo("BLEU", "BleuScore",
                    "답변과 모범답안의 단어 n-gram 일치도.",
                    CATEGORY_ANSWER, False, False, "띄어쓰기 단위 비교라 한국어에 부적합"),
    RagasMetricInfo("ROUGE", "RougeScore",
                    "답변과 모범답안의 단어 중복도.",
                    CATEGORY_ANSWER, False, False, "RAGAS 구현이 한글을 지워 항상 0"),
    RagasMetricInfo("chrF", "ChrfScore",
                    "답변과 모범답안의 글자 n-gram 일치도(F-score).",
                    CATEGORY_ANSWER, False, False, "답변 유사도(참고)와 용도가 겹쳐 제외"),
    # ── 검색 품질 ──
    RagasMetricInfo("컨텍스트 정밀도(모범답안 없음)", "LLMContextPrecisionWithoutReference (= Context Utilization)",
                    "모범답안 대신 답변을 기준으로 근거 청크가 유용했는지 판단한다.",
                    CATEGORY_RETRIEVAL, True, False, "모범답안 기준 지표(LLM 근거 Precision)를 사용"),
    RagasMetricInfo("컨텍스트 개체 재현율", "ContextEntityRecall",
                    "모범답안에 나온 개체(이름·조문·수치 등) 중 근거 청크에 있는 비율.",
                    CATEGORY_RETRIEVAL, True, False, "LLM 근거 Recall 과 용도가 겹쳐 제외"),
    RagasMetricInfo("컨텍스트 관련성(NVIDIA)", "ContextRelevance",
                    "근거 청크가 질문과 관련 있는지를 Judge 가 등급으로 매긴다.",
                    CATEGORY_RETRIEVAL, True, False, "LLM 근거 Precision 과 용도가 겹쳐 제외"),
    # ── RAG 질의응답 외 ──
    RagasMetricInfo("요약 점수", "SummarizationScore",
                    "요약문이 원문의 핵심을 담았는지.", CATEGORY_OTHER, True, False, _NOT_RAG_QA),
    RagasMetricInfo("주제 준수", "TopicAdherenceScore",
                    "멀티턴 대화가 허용된 주제 안에 머무는지.", CATEGORY_OTHER, True, False, _NOT_RAG_QA),
    RagasMetricInfo("에이전트 목표 달성", "AgentGoalAccuracyWithReference / WithoutReference",
                    "에이전트가 사용자의 목표를 달성했는지.", CATEGORY_OTHER, True, False, _NOT_RAG_QA),
    RagasMetricInfo("도구 호출 정확도", "ToolCallAccuracy / ToolCallF1",
                    "에이전트가 기대한 도구를 올바른 인자로 호출했는지.", CATEGORY_OTHER, False, False,
                    _NOT_RAG_QA + " (이 API 는 도구 호출이 없음)"),
    RagasMetricInfo("SQL 동등성", "LLMSQLEquivalence",
                    "생성한 SQL 이 정답 SQL 과 같은 뜻인지.", CATEGORY_OTHER, True, False, _NOT_RAG_QA),
    RagasMetricInfo("SQL 결과 비교", "DataCompyScore",
                    "SQL 실행 결과 표가 정답 표와 같은지.", CATEGORY_OTHER, False, False, _NOT_RAG_QA),
    RagasMetricInfo("멀티모달 충실도·관련성", "MultiModalFaithfulness / MultiModalRelevance",
                    "이미지가 포함된 근거에 대한 충실도·관련성.", CATEGORY_OTHER, True, False, _NOT_RAG_QA),
)
