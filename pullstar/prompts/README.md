# PullStar prompts

Each file here is one **version** of the system prompt used to turn a scored
GitHub-activity profile into a 1-on-1 brief. They ship as package data, so
`pip install pullstar` includes every version.

| File | Selected with |
| --- | --- |
| `brief_v1.txt` | default (or `--prompt brief_v1`) |
| `brief_v2.txt` | `--prompt brief_v2` |
| `brief_<style>_v1.txt` | `--prompt brief_<style>_v1` |

```bash
python scripts/generate_brief.py --login jsmith --mode local --prompt brief_v2
python scripts/agent_prepare_1on1.py --login jsmith --prompt brief_v2
```

The chosen version is recorded as `metadata.prompt` in
`llm_input_{login}.json` and as `prompt` in `output_{login}.json`.

## Contributing a prompt

Prompt improvements are welcome as pull requests.

1. **Add a new file** — never edit an existing version in place. Bump the
   number in the filename (`brief_v2.txt`) or add a named variant
   (`brief_concise_v1.txt`). Existing briefs must stay reproducible.
2. Keep the filename `brief_*.txt`, lowercase, no spaces.
3. In the PR description, say what changed and why, and include a sample
   brief generated with `--mode local --prompt <your_file>` (the prompt only
   affects `--mode local`; `--mode stub` is deterministic from scores).
4. Changing the **default** (`brief_v1`) is a separate discussion — open an
   issue first.

## Using a prompt that isn't packaged yet

Point `--prompt` at a path to iterate locally before opening a PR:

```bash
python scripts/generate_brief.py --login jsmith --mode stub --prompt ./drafts/brief_v2.txt
```

Loaded-from-path prompts are recorded as `prompt: "custom"`.
