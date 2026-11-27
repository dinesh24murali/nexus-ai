# Scratch: 007-rag pass 3 (rag.golden dataset, runner, smoke run, synopsis decision)

Follow-up to pass 2 (TASK-019 open criteria: "RAG suites seeded", "synopsis decision"). Constraint from the developer: **no full eval; smoke only; limited OpenAI credit.**

## Plan

1. **Corpus** (`evals/fixtures/docs/`): 8 short fictional documents as readable source files (`.md`, `.txt`, and `.pdf` / `.docx` *sources* written as text with `---page---` separators and paragraph lines), plus `manifest.json` (file name, format, upload order: last = newest). `backend/evals/corpus.py` builds real PDF/DOCX bytes at run time (the PDF/DOCX builders move from `tests/rag/builders.py` to `evals/filebuild.py`; the tests import them from there). No binary files in the repo.
2. **Dataset** `evals/datasets/rag.golden.jsonl`: 40 records `{id, input, kind, reference_answer, reference_chunks:[{file,page}], evidence?, key_facts?, answerable, smoke}`. kinds: 23 `fact`, 6 `multi`, 4 `whole_doc`, 7 `unanswerable`. Two additive fields vs doc 09 (`kind`, `evidence`, `key_facts`): documented in doc 09. Smoke (10): 3 fact, 1 multi, **all 4 whole_doc** (they are the open design question), 2 unanswerable. AI-drafted, developer review pending.
3. **Tests first** (each seen failing): record model + loading; corpus consistency (every `reference_chunks` file exists in the manifest, every `evidence` phrase really is in the chunk of that file/page after the REAL parse + chunk: this proves the references); metrics `context_recall`, `citation_validity`; `Judges.not_found`; the rag step with a scripted graph and stub judges; ingestion against real Qdrant with fake embeddings (integration).
4. **Code:** `evals/datasets.py` (`RagCase`, registered in MODELS), `evals/corpus.py` (build, ingest into a throwaway alias + fresh user uuid, cleanup, recent-files list), `evals/rag.py` (`run_rag_golden`), `ChatRun` gains `citations` and `retrieved` (tool message texts), `run_chat(user_id=)`; `evals/judges.py` `not_found` + prompt `eval.judge.notfound` v1; `evals/metrics.py`; `evals/run.py`: dataset `rag.golden`, new metric keys, `--no-rag` flag, the step runs only when a RAG environment (Qdrant + corpus) is provided so unit tests need no services.
5. **Metrics:** `rag.context_recall` (reference file+page among retrieved passages; a case where the model never searched scores 0), `rag.faithfulness` (calibrated judge, answer vs the tool results the model saw), `rag.answer_correctness` (+ `.whole_doc`; `fact_retention` judge over `key_facts`), `rag.unanswerable_accuracy` (new judge), `rag.citation_validity` (deterministic: `[n]` markers that point at a returned passage; answerable answers must cite) and `rag.search_rate` (informational). `rag.citation_accuracy` (judge per sentence) is NOT built: costs a judge call per marker.
6. **Run smoke only** (`--dataset rag.golden`, about 10 chat runs + ~18 judge calls). Then the synopsis decision from the whole_doc results, recorded in `memory/ai.md` and the task file. Docs: doc 09 (fields, metrics), CHANGELOG, `evals/AGENTS.md`, coverage gate for `backend/evals`.

## Files to change
`backend/evals/{datasets,judges,metrics,runner,run,corpus,filebuild,rag}.py`, `backend/prompts/eval.judge.notfound.md`, `backend/tests/rag/builders.py`, `backend/tests/evals/*`, `evals/fixtures/docs/*`, `evals/datasets/rag.golden.jsonl`, `evals/eval-baseline.json` (targets only), docs 09, memory, CHANGELOG, task file.

## Uncertainties
- n=1 on 4 whole-doc cases is thin evidence for the synopsis decision; the decision will say how thin. If the result is ambiguous I will not build a synopsis and will say what a bigger run would need.
- The model may not search at all for whole-doc questions ("summarise the handbook"): that shows up as recall 0, and is itself a finding.
- Real answers may be marked wrong by the judge for wording reasons; the cases file keeps every answer so I can read them.

## What will be skipped
- A full run, gate recording, and any `eval-baseline.json` metric (advisory only; ADR-0009 needs a reviewed full run).
- `rag.citation_accuracy` (judge per sentence), `rag.context_precision` (judge per chunk): credit.
- The synopsis / `get_file_overview` implementation itself (only if the smoke evidence says so AND the developer agrees).

## Risks
- Qdrant must be running for any eval run that includes `rag.golden` (`--no-rag` skips it).
- Corpus facts are fictional and AI-drafted: a wrong reference would blame the model unfairly; the evidence-phrase test guards the file/page, the developer must still review the answers.
- Smoke now includes rag.golden, so `make eval-smoke` gets a little slower and costlier (about 10 more chat runs + ~18 judge calls).

## Ready to implement?
Yes.
