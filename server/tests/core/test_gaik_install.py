"""The gaik install: pinned version, every component the features use, and no torch.

gaik swallows a missing optional dependency at import, so a class of a missing extra is
silently absent. This fails loudly instead, also in the image build.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util

import pytest

from app.core.llm import quiet_stdout

COMPONENTS = [
    ("gaik.software_components.llm", "get_llm_config"),
    ("gaik.software_components.llm", "create_llm_client"),
    ("gaik.software_components.extractor", "SchemaGenerator"),
    ("gaik.software_components.extractor", "DataExtractor"),
    ("gaik.software_components.extractor", "ExtractionRequirements"),
    ("gaik.software_components.extractor", "FieldSpec"),
    ("gaik.software_components.extractor", "create_extraction_model"),
    ("gaik.software_components.source_tracing", "SourceTracing"),
    ("gaik.software_components.transcriber", "Transcriber"),
    ("gaik.software_components.jev_classifier", "JevClassifier"),
    ("gaik.software_components.doc_classifier", "DocumentClassifier"),
    ("gaik.software_components.parsers", "PyMuPDFParser"),
    ("gaik.software_components.parsers", "DocxParser"),
    ("gaik.software_components.parsers", "VisionParser"),
    ("gaik.software_components.vision_extractor", "VisionExtractor"),
    ("gaik.software_components.RAG.embedder", "Embedder"),
    ("gaik.software_components.RAG.vector_store", "VectorStore"),
    ("gaik.software_modules.audio_to_structured_data", "AudioToStructuredData"),
    ("gaik.software_modules.documents_to_structured_data", "DocumentsToStructuredData"),
]


def test_gaik_is_the_pinned_release():
    assert importlib.metadata.version("gaik") == "0.8.14"


@pytest.mark.parametrize(("module_name", "attribute"), COMPONENTS)
def test_component_is_importable(module_name, attribute):
    with quiet_stdout():
        module = importlib.import_module(module_name)
    assert getattr(module, attribute, None) is not None, f"{module_name}.{attribute} is absent"


@pytest.mark.parametrize("package", ["torch", "docling", "transformers", "sentence_transformers"])
def test_heavy_packages_are_not_installed(package):
    assert importlib.util.find_spec(package) is None


def test_mcp_sdk_is_the_v2_line():
    assert importlib.metadata.version("mcp").startswith("2.")
    from mcp.server import MCPServer  # noqa: F401
