# Academician ↔ OpenAlex disambiguation

This repository is a reproducible pipeline for matching 2,052 CAS/CAE academicians to OpenAlex Author IDs and selecting one English-language **non-China journal** DOI as an identity anchor.

## Security
`OPENALEX_API_KEY` must exist only as a GitHub Actions repository secret. Never commit the key to a file.

## Pipeline
1. Use an already verified DOI as the strongest anchor when available.
2. Otherwise generate OpenAlex Author candidates using the documented `display_name.search` filter.
3. Score name, historical/current institution, and field/topic evidence.
4. Only A/B author matches proceed to works retrieval.
5. Retrieve English journal articles with DOI, then require the OpenAlex Source country to be non-`CN`.
6. Export matches, top-5 candidates, review-required rows, and a summary.

## Run
Actions → **Run OpenAlex academician disambiguation** → Run workflow.

Use `max_rows=20` first for a smoke test; use `0` for all 2,052 rows.

## Outputs
The Actions artifact `academician-openalex-results` contains:
- `academician_openalex_matches.csv`
- `author_candidates_top5.csv`
- `review_required.csv`
- `summary.json`

The pipeline is conservative by design: ambiguous homonyms are retained for review instead of being force-matched.
