# Runbook

This project uses a deterministic, local Python implementation of the required multi-agent workflow. It has no third-party Python dependencies.

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
```

The command requires exactly `input/EC_001.json` through `input/EC_050.json`. It writes the matching JSON files to `output/`, replaces `logging/trace.jsonl` with the trace for that run, and writes runtime metadata to `logging/metadata.json`.

Validate that all required artifacts were generated:

```powershell
(Get-ChildItem output -Filter 'EC_*.json').Count
(Get-Content logging/trace.jsonl | Measure-Object -Line).Lines
Get-Content logging/metadata.json
```

Only after the count is 50, make the submission archive:

```powershell
Compress-Archive -Path output\EC_*.json -DestinationPath output_submission.zip -Force
```

## Optional: test a local LM Studio model

The submission pipeline is intentionally rule-based and does not require a model or API key. To test a permitted local model separately, copy `.env.example` to `.env`, set `LMSTUDIO_MODEL` to the exact model ID shown by LM Studio, load that model, and start the server in its **Developer** tab.

```powershell
Copy-Item .env.example .env
# Edit .env: set LMSTUDIO_MODEL to the loaded model ID.
python scripts/lmstudio_smoke_test.py
```

LM Studio uses `http://127.0.0.1:1234/v1` by default. No token is needed unless you turn on authentication in LM Studio. The smoke test only calls the local server; it does not modify `input/`, `output/`, or submission logs.

### LLM candidate run (run this yourself)

Keep the submitted baseline untouched. The following command writes a separate candidate set. One local Qwen3 4B instance is called in three roles: Policy Proposer, Policy Critic and Policy Finalizer. Each sees a read-only evidence packet made from the supplied CSV data; Critic and Finalizer additionally receive a read-only Policy Eligibility Tool response derived from the public policy conditions. The public policy resolver derives refund/action fields from the final model choice, while source-ID validation plus bounded local retries protect the contract. There is no case-ID answer key and neither `input/` nor `data/` is modified.

```powershell
python run.py --policy-mode llm --llm-parameter-size 4B --category-language english --output-dir output_llm_agents_candidate --trace logging/trace_llm_agents_candidate.jsonl --metadata logging/metadata_llm_agents_candidate.json
```

`--category-language english` derives category labels from `product_category_name_translation.csv`; use `source` if the expected schema is confirmed to require the original Portuguese labels. Verify and archive a candidate only after you decide it is better than the submitted baseline.
