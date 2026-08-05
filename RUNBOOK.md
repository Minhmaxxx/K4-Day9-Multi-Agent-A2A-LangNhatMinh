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
