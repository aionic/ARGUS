# Conduent OCR datasets

This directory contains deduplicated active datasets, normalized truth, repaired
schemas, and immutable copies of the supplied source packages.

See [`SCHEMA_TRUTH_MAPPING.md`](SCHEMA_TRUTH_MAPPING.md) for the complete
document-to-truth mapping and details of the CSV/Excel-to-JSONL transformation.

## Active datasets

| Dataset | Documents | Truth |
|---|---:|---|
| `cms1500` | 22 | Golden BW/Red truth |
| `commercial-documents` | 7 | Golden `MS_Truth_Data.xlsx` truth |
| `enrollments` | 4 | Golden `MS_Truth_Data.xlsx` truth |
| `invoice-demo` | 1 | Golden truth and evaluation policy |
| `medical-demo` | 1 | Schema only |
| `unlabeled` | 3 | No supplied truth |

`demo\cms1500-claims\samples\bad.png` is excluded from active datasets because
it is pixel-identical to `WO7U9NJQprod.tiff`, despite being described as a
degraded negative test. The incomplete Mistral package is also archive-only.

## Conventions

- `manifest.jsonl` contains one record per logical document.
- `truth\records.jsonl` contains normalized golden records linked by
  `document_id` and `truth_record_id`.
- `schemas\` contains versioned JSON Schemas.
- `source-archive\` preserves the original supplied packages unchanged.
- `catalog.json` records hashes, image metadata, duplicate groups, exclusions,
  and dataset coverage.

## Evaluation policy

`evaluation\splits.json` freezes the document-level tuning, calibration,
holdout, and development assignments:

- CMS-1500: 4 tuning, 4 calibration, and 14 holdout, stratified across BW and
  Red forms.
- Commercial documents: 2 tuning, 1 calibration, and 4 holdout.
- Enrollments: all 4 are holdout for the primary zero-shot result.
- The synthetic invoice is tuning-only; medical and unlabeled samples are
  development-only.

Validate the corpus without Azure access:

```powershell
uv run --project src\containerapp python scripts\cu_golden_eval.py --validate-only
```

Live runs execute from the VNet-integrated Container Apps Job through
`scripts\run_conduent_bakeoff.ps1`. Only active datasets, schemas, manifests,
truth, and split metadata are uploaded; `source-archive` is never uploaded.
