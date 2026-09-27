import json

import rag

DOCS = [
    {"id": "P1", "text": "[P1] Sony WH-1000XM5 (category: Electronics; MSRP: $399.99)\n"
                         "- Amazon: latest $328.00 (as of 2026-09-20); lowest $298.00; change since first observation: -18.0% (-$71.99)\n"
                         "- Best Buy: latest $349.99 (as of 2026-09-19)"},
    {"id": "N4", "text": "[N4] (Slickdeals, 2026-09-18) Sony headphones hit record low"},
]


def fake_llm(payload):
    return lambda system, user: json.dumps(payload)


def test_grounded_answer_passes():
    r = rag.answer_question("Where is the Sony cheaper?", docs=DOCS, log=False, llm=fake_llm(
        {"answer": "Amazon is cheaper at $328.00 vs $349.99 at Best Buy, down 18% since tracking began.",
         "citations": ["P1"], "insufficient_context": False}))
    assert r["grounded"] and r["citations"] == ["P1"]


def test_whole_dollar_rounding_allowed():
    ok, _ = rag.check_grounding({"answer": "About $328 on Amazon.", "citations": ["P1"]}, DOCS)
    assert ok


def test_invented_number_rejected():
    r = rag.answer_question("Where is the Sony cheaper?", docs=DOCS, log=False, llm=fake_llm(
        {"answer": "Walmart has it for $279.99.", "citations": ["P1"], "insufficient_context": False}))
    assert not r["grounded"] and r["answer"] == rag.FALLBACK and r["reason"].startswith("unsupported_numbers")


def test_number_must_be_in_cited_doc_not_just_any_doc():
    # $349.99 is in P1, but the answer only cites N4
    ok, reason = rag.check_grounding({"answer": "It's $349.99 at Best Buy.", "citations": ["N4"]}, DOCS)
    assert not ok and reason.startswith("unsupported_numbers")


def test_fabricated_citation_rejected():
    ok, reason = rag.check_grounding({"answer": "Cheapest is Amazon.", "citations": ["P99"]}, DOCS)
    assert not ok and reason.startswith("cited_unretrieved_docs")


def test_missing_citations_and_malformed_output_rejected():
    assert rag.check_grounding({"answer": "Amazon.", "citations": []}, DOCS) == (False, "no_citations")
    r = rag.answer_question("q", docs=DOCS, log=False, llm=lambda s, u: "Sure! Amazon is cheapest.")
    assert r["reason"] == "malformed_llm_output"


def test_insufficient_context_and_empty_retrieval():
    ok, reason = rag.check_grounding({"answer": "", "citations": [], "insufficient_context": True}, DOCS)
    assert reason == "model_reported_insufficient_context"
    called = []
    r = rag.answer_question("q", docs=[], log=False, llm=lambda s, u: called.append(1))
    assert r["reason"] == "no_relevant_data_retrieved" and not called   # LLM never called


def test_llm_error_falls_back():
    def boom(s, u):
        raise TimeoutError()
    r = rag.answer_question("q", docs=DOCS, log=False, llm=boom)
    assert not r["grounded"] and r["reason"] == "llm_error:TimeoutError"


def test_json_in_code_fence_is_parsed():
    assert rag.parse_llm_json('```json\n{"answer": "x", "citations": ["P1"]}\n```')["citations"] == ["P1"]
