# 모델 대역 지시서 (녹음용)

You are standing in for the **model behind an API call**. A reviewing assistant receives a fixed system prompt and one user message per
ad, and must answer with a JSON object that follows a fixed schema. Your job is to produce, for each item in your shard, exactly the
answer that model would give. This is a measurement of how an LLM-based reviewer behaves inside a larger pipeline, so answer the way
a careful reviewing assistant would — neither more cautious nor more permissive than the prompt asks.

## What you may read
Read ONLY these files (they are in the folder named in your task message):
1. `system.txt` — the system prompt. Treat it as *your* instructions for every item.
2. `schema.json` — the output schema the API enforces.
3. your shard file `shard_<k>.jsonl` — one JSON object per line: `{"key", "user"}`; `user` is the user message for that item.
4. this brief.

Do NOT read, list, grep or open anything else (no policy documents, no repository files, nothing under /home/user), do not browse the web,
and do not run code from other places. You must decide each item from `system.txt` and the user message alone.

## How to answer
- For every line, write `{"key": <same key>, "response": {"findings": [...]}}`. `findings` follows the schema (each finding has exactly
  `rule_id`, `code`, `field`, `quote`, `confidence`, `rationale`). If you have no candidates, `{"findings": []}`.
- `quote` must be copied verbatim from the named field in the `<creative>` JSON (same characters, including odd spacing or invisible
  characters). The checker rejects anything that is not a verbatim substring.
- Apply the system prompt as written, including what it says about untrusted data and about what NOT to report.
- Decide each item once; keep reasoning short; do not draft alternatives. Write all answers with ONE small Python script (so JSON
  escaping and unusual characters are handled correctly), then run the checker:
  `python3 <folder>/check_responses.py <your output file> <your shard> <folder>/schema.json`
  and fix everything it reports.

## Final message
Short: the output path and the checker's last line. Deliver it via the SubagentHandback call your runtime asks for.
