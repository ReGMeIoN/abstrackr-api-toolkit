# `prompts/` — screening prompts

The prompt is part of the method, not a throwaway message. These files are
**templates**: replace the `{{...}}` placeholders and keep the result in the
workdir, so every batch of a pass is screened under byte-identical instructions.

| File | Stage | Notes |
|---|---|---|
| `screen_tiab.zh-CN.md` | title/abstract (中文) | includes a full worked `{{DOMAIN_LADDER}}` block from a real review |
| `screen_tiab.en.md` | title/abstract (English) | same skeleton, English wording |
| `screen_fulltext.zh-CN.md` | full text (中文) | Include/Exclude + exclusion codes, Hold-closure checks |

## Why they look the way they do

* **One batch per call, one file out, one line back.** The detail belongs in the
  decision file; a long chat report only burns the orchestrator's context.
* **Three output shapes with conditional fields.** A screener that fills every
  field on every record starts inventing the optional ones.
* **Effect sizes are verbatim-only.** An invented number flows straight into the
  extraction table; a blank one costs a phone call.
* **"Prefer Hold over a false Exclude" is stated explicitly.** Title/abstract
  screening optimises recall, and a false exclusion is unrecoverable.
* **The domain ladder is the only review-specific block.** Everything else is
  process and can be reused unchanged.

The controlled vocabularies quoted inside a filled-in prompt must match
`protocol.json`; `pipeline/s2_validate.py` enforces that they do.
