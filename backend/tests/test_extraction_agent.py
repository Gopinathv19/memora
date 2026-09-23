"""Extraction Agent: routing to model roles, fallbacks, and result validation.

The agent is exercised through its public `extract(...)` interface with a fake
LLM client, so no provider is ever called.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.agents import ExtractionFailed, NemotronExtractionAgent
from app.core.config import Settings
from app.processing import DocumentUnit, ImageBlob, ProcessedDocument
from app.schemas.enums import ExtractionRoute, ExtractionStatus, ModelRole, PageDifficulty
from tests.extraction_fakes import FakeLLMClient

SETTINGS = Settings(database_url="postgresql+psycopg://unused/unused")
LAYOUT = SETTINGS.llm_layout_model
VISION = SETTINGS.llm_vision_model
EXTRACT = SETTINGS.llm_extract_model
IMG = ImageBlob(b"png-bytes", "image/png", "picture")


def _doc():
    return ProcessedDocument(
        "pdf",
        [
            DocumentUnit(1, "page", "Easy page text", PageDifficulty.EASY, ExtractionRoute.TEXT),
            DocumentUnit(
                2, "page", "Medium page text", PageDifficulty.MEDIUM,
                ExtractionRoute.VISION, images=[IMG, IMG],
            ),
            DocumentUnit(
                3, "page", "hard local text", PageDifficulty.HARD,
                ExtractionRoute.LAYOUT, page_image=IMG,
            ),
        ],
    )


def _agent(**client_kwargs):
    client = FakeLLMClient(**client_kwargs)
    return NemotronExtractionAgent(client, SETTINGS), client


def test_each_difficulty_goes_to_its_model():
    agent, client = _agent()
    source_id = uuid.uuid4()
    output = agent.extract(_doc(), source_id=source_id)

    image_calls = [c["model"] for c in client.calls if c["kind"] == "read_image"]
    # EASY: no call. MEDIUM: one vision call per picture. HARD: one layout call.
    assert sorted(image_calls) == sorted([VISION, VISION, LAYOUT])
    [extract_call] = [c for c in client.calls if c["kind"] == "chat_json"]
    assert extract_call["model"] == EXTRACT

    # The single extract call sees every unit, in order, with provenance markers.
    user = extract_call["user"]
    assert user.index("page 1 · easy · text") < user.index("page 2 · medium · vision")
    assert user.index("page 2") < user.index("page 3 · hard · layout")
    assert f"transcribed text by {LAYOUT}" in user

    result = output.result
    assert result.source_id == source_id
    assert result.status == ExtractionStatus.COMPLETED
    assert [(p.page, p.route, p.model) for p in result.pages] == [
        (1, ExtractionRoute.TEXT, None),
        (2, ExtractionRoute.VISION, VISION),
        (3, ExtractionRoute.LAYOUT, LAYOUT),
    ]
    roles = sorted(u.role for u in output.usage)
    assert roles == sorted([ModelRole.VISION, ModelRole.VISION, ModelRole.LAYOUT, ModelRole.EXTRACT])


def test_model_output_is_validated_leniently():
    agent, _ = _agent()
    result = agent.extract(_doc(), source_id=uuid.uuid4()).result
    assert result.document_type == "invoice"
    # Malformed fields are dropped, numbers become strings, 92 becomes 0.92,
    # keys are normalized to snake_case.
    assert [(f.key, f.value, f.confidence) for f in result.fields] == [
        ("invoice_number", "INV-2041", 0.97),
        ("total_amount", "12400", 0.92),
    ]
    [table] = result.tables
    assert table.rows == [["Widget", "4"]]


def test_layout_failure_falls_back_to_vision_model():
    agent, client = _agent(fail_models={LAYOUT})
    output = agent.extract(_doc(), source_id=uuid.uuid4())
    page3 = output.result.pages[2]
    assert page3.model == VISION
    assert page3.status == "fallback"
    failed = [u for u in output.usage if u.status == "failed"]
    assert [(u.role, u.model) for u in failed] == [(ModelRole.LAYOUT, LAYOUT)]
    assert output.result.status == ExtractionStatus.COMPLETED


def test_both_image_models_down_falls_back_to_local_text():
    agent, _ = _agent(fail_models={LAYOUT, VISION})
    output = agent.extract(_doc(), source_id=uuid.uuid4())
    page3 = output.result.pages[2]
    assert page3.route == ExtractionRoute.TEXT
    assert page3.status == "fallback"
    assert any("page 3" in w for w in output.result.warnings)


def test_unreadable_unit_marks_the_result_partial():
    doc = ProcessedDocument(
        "pdf",
        [
            DocumentUnit(1, "page", "Readable", PageDifficulty.EASY, ExtractionRoute.TEXT),
            DocumentUnit(2, "page", "", PageDifficulty.HARD, ExtractionRoute.LAYOUT, page_image=IMG),
        ],
    )
    agent, _ = _agent(fail_models={LAYOUT, VISION})
    result = agent.extract(doc, source_id=uuid.uuid4()).result
    assert result.status == ExtractionStatus.PARTIAL
    assert result.pages[1].status == "failed"


def test_nothing_readable_fails_without_calling_the_extract_model():
    doc = ProcessedDocument(
        "image", [DocumentUnit(1, "image", "", PageDifficulty.HARD, ExtractionRoute.VISION, images=[IMG])]
    )
    agent, client = _agent(fail_models={VISION})
    with pytest.raises(ExtractionFailed) as caught:
        agent.extract(doc, source_id=uuid.uuid4())
    assert not [c for c in client.calls if c["kind"] == "chat_json"]
    assert caught.value.usage[0].status == "failed"


def test_extract_model_failure_keeps_the_usage_it_cost():
    agent, _ = _agent(fail_models={EXTRACT})
    with pytest.raises(ExtractionFailed) as caught:
        agent.extract(_doc(), source_id=uuid.uuid4())
    extract_usage = [u for u in caught.value.usage if u.role == ModelRole.EXTRACT]
    assert extract_usage[0].status == "failed"
    assert extract_usage[0].prompt_tokens == 10


def test_instructions_reach_the_prompt_as_guidance():
    agent, client = _agent()
    agent.extract(_doc(), source_id=uuid.uuid4(), instructions="capture the policy number")
    [call] = [c for c in client.calls if c["kind"] == "chat_json"]
    assert "<user_guidance>\ncapture the policy number\n</user_guidance>" in call["user"]


def test_skipped_pages_make_the_result_partial():
    doc = _doc()
    doc.skipped_units = 3
    agent, _ = _agent()
    assert agent.extract(doc, source_id=uuid.uuid4()).result.status == ExtractionStatus.PARTIAL


@pytest.mark.parametrize("field", ["llm_extract_model", "llm_vision_model", "llm_layout_model"])
def test_only_nvidia_models_are_accepted(field):
    with pytest.raises(ValidationError, match="NVIDIA model"):
        Settings(database_url="postgresql+psycopg://x/y", **{field: "meta/llama-3.1-70b"})


def test_provider_switch_changes_endpoint_and_key():
    nebius = Settings(
        database_url="postgresql+psycopg://x/y",
        llm_provider="nebius", nebius_api_key="nb", nvidia_api_key="nv",
    )
    assert nebius.llm_base_url.startswith("https://api.tokenfactory.nebius.com")
    assert nebius.llm_api_key == "nb"
    nvidia = Settings(database_url="postgresql+psycopg://x/y", nvidia_api_key="nv")
    assert nvidia.llm_base_url == "https://integrate.api.nvidia.com/v1"
    assert nvidia.llm_api_key == "nv"
