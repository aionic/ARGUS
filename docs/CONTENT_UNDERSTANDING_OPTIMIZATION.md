# Content Understanding optimization and evaluation

## Current ARGUS configuration

ARGUS creates one deterministic custom analyzer per dataset schema. The analyzer:

- Inherits from `prebuilt-document`.
- Enables OCR, layout, and detailed output.
- Infers field types from example JSON values.
- Uses the original JSON key as each field description.
- Reuses an analyzer until its complete definition changes.
- Captures Content Understanding page meters, contextualization tokens, model tokens, field confidence, latency, and calculated cost.

Dataset `processing_options` can now include a `content_understanding` object with analyzer configuration, schema guidance, model overrides, and confidence scope.

## Findings and recommendations

| Priority | Finding | Recommendation | Expected effect |
|---|---|---|---|
| P0 | Confidence and grounding are opt-in, but ARGUS only sets `returnDetails`. | Explicitly enable confidence for fields that drive automation or human review. Prefer `confidence_fields` over enabling all fields. | Reliable confidence UI with lower cost than full confidence. |
| P0 | Field descriptions are currently just raw JSON keys. | Add clear descriptions with aliases, location hints, formats, and affirmative wording. | Highest-probability no-training quality improvement. |
| P0 | Literal document fields don't specify `method: extract`. | Use extractive mode for values copied from the document; keep generated fields on automatic or `generate`. | Better grounding and confidence semantics. |
| P1 | Every scanned page uses layout and therefore the standard content-extraction meter. | Benchmark `enableLayout: false` for fixed forms where raw OCR is sufficient. Keep layout for invoices and tables unless measured quality remains acceptable. | Potential standard-to-basic meter reduction. |
| P1 | The invoice dataset uses a generic custom analyzer. | Compare the custom schema with `prebuilt-invoice` separately before production adoption; prebuilt output needs a field mapping layer. | Potential quality gain from invoice-specific knowledge. |
| P1 | No labeled samples are attached to analyzers. | After schema tuning, add one corrected sample per materially different template, focusing on low-confidence or repeatedly incorrect fields. | Better adaptation to template variation. |
| P1 | Confidence thresholds are global. | Calibrate thresholds per field criticality from golden results instead of using a single threshold. | Better straight-through processing and review routing. |
| P2 | Analyzer creation occurs on first document. | Pre-generate variants with `prepare_cu_analyzer` or `--prepare-only` before a production rollout. | Removes first-document analyzer build latency. |
| P2 | Full OCR markdown is always returned. | Use `omitContent` only in extraction-only workflows that don't need OCR text, evidence display, or downstream chunking. | Smaller responses and lower transfer/storage overhead. |

Microsoft notes that confidence/grounding and labeled examples increase contextualization and model usage. Labeled samples also add embedding and completion input tokens. Cost decisions must therefore use observed `usage` data rather than analyzer names.

## Configuring a dataset

Add analyzer options under dataset `processing_options`:

```json
{
  "extraction_backend": "content_understanding",
  "content_understanding": {
    "humanize_field_names": true,
    "schema_description": "Commercial invoice fields and line items.",
    "confidence_fields": [
      "Invoice Number",
      "Payment Due",
      "Table.Total"
    ],
    "field_hints": {
      "Invoice Number": {
        "description": "Unique invoice identifier, also labeled Invoice No. or Invoice #."
      }
    },
    "config": {
      "enableLayout": true,
      "returnDetails": true
    }
  }
}
```

Supported ARGUS options include:

- `humanize_field_names`
- `schema_name` and `schema_description`
- `default_method`
- `confidence_fields` with glob patterns
- `field_hints` keyed by dotted field path
- `config` for Content Understanding analyzer settings
- `models` for completion and embedding model names
- `base_analyzer_id`

## Conduent bake-off benchmark

The canonical benchmark covers 34 golden documents across CMS-1500,
commercial documents, handwritten enrollments, and the synthetic invoice. It
emits:

- Accuracy on populated truth fields, blank-field accuracy, hallucination rate,
  presence precision/recall, and balanced accuracy.
- Per-field evidence with expected, actual, exact/normalized match, and
  confidence.
- Field-confidence coverage, Brier score, and expected calibration error.
- OCR word-confidence mean and low-confidence-word fraction.
- Product grounding coverage and source-text consistency. Spatial grounding
  accuracy remains unavailable until bounding-box truth is labeled.
- CU page meters, contextualization tokens, model tokens, DI preflight cost,
  total cost, cost per correct field, and cost per passing document.
- Preprocessing, extraction, and end-to-end latency with P50/P95 summaries.
- Corpus, analyzer, preprocessing, document, and code provenance hashes.

Validate local corpus integrity:

```powershell
uv run --project src\containerapp python scripts\cu_golden_eval.py --validate-only
```

The Azure AI Services account is private-endpoint-only, so live evaluation runs
inside a VNet-integrated Container Apps Job:

```powershell
# One document per eligible dataset/variant.
.\scripts\run_conduent_bakeoff.ps1 -Stage Pilot

# Full tuning and calibration split.
.\scripts\run_conduent_bakeoff.ps1 -Stage Tune

# Prepare analyzers and freeze the exact corpus/configuration hash.
.\scripts\run_conduent_bakeoff.ps1 -Stage Freeze

# A holdout run is rejected unless the matching frozen lock is present.
.\scripts\run_conduent_bakeoff.ps1 -Stage Holdout
```

The launch script builds a dedicated evaluation image from the deployed backend
image, overlays the current evaluator code, and includes only the active corpus
files (never `source-archive`). The job
publishes that canonical subset to a private blob prefix using its user-assigned
managed identity, then writes JSON, CSV, Markdown, field-level evidence, raw
vendor responses, and the run manifest to the private result prefix. The
production profiling endpoint reads the same canonical private corpus rather
than carrying a second bundled copy.

After the frozen holdout completes, rebuild the combined report from cached raw
results and retrieve it without making additional AI calls:

```powershell
.\scripts\run_conduent_bakeoff.ps1 -Stage Finalize
.\scripts\run_conduent_bakeoff.ps1 -Stage Report -SkipImageBuild
```

## Frozen bake-off results

The tuning, calibration, and single blind-holdout run completed against corpus
hash `15bb0b7a...e9ca2`. The combined package contains 147 successful
document-variant runs at a measured processing cost of **$2.665560**. The blind
holdout accounts for 96 runs and **$1.778283** of that total.

| Dataset | Recommended holdout variant | Populated accuracy | Blank accuracy | OCR confidence | Cost | P95 latency |
|---|---|---:|---:|---:|---:|---:|
| CMS-1500 | `semantic-guided` for quality | 54.8% | 80.4% | 95.9% | $0.315728 | 39.42s |
| CMS-1500 | `full-confidence` for cost/latency | 53.6% | 75.1% | 95.9% | $0.273368 | 33.17s |
| Commercial | `baseline-current` | 41.9% | 76.3% | 74.7% | $0.044326 | 16.85s |
| Enrollments | `full-confidence` | 64.3% | 84.3% | 89.8% | $0.059881 | 22.03s |

No holdout document met the strict gate of 80% populated-field accuracy and 95%
blank-field accuracy. The synthetic invoice was the only passing benchmark
document, so it must not be used as evidence that the scanned production
datasets are ready for straight-through processing.

The results support these configuration decisions:

1. Use semantic schema guidance for CMS-1500 when extraction quality is the
   primary objective; use full confidence when the small accuracy tradeoff is
   justified by lower cost and latency.
2. Keep the commercial workflow on the baseline analyzer. Guided and confidence
   variants materially reduced populated-field accuracy on the blind holdout.
3. Use full confidence for enrollments. Populated accuracy was tied across
   variants, while full confidence produced the best blank accuracy, complete
   grounding coverage, lowest cost, and lowest latency.
4. Do not promote any scanned-dataset variant to unattended processing yet.
   Continue field-level error analysis and add representative labeled samples
   before another separately frozen holdout.
5. Treat OCR confidence as a scan-quality signal rather than extraction
   correctness. Commercial scans had the lowest OCR confidence and extraction
   accuracy, but high OCR confidence alone did not make CMS extraction pass.

## Decision gates

1. Adopt schema guidance when normalized accuracy improves without a material cost increase.
2. Enable selective confidence when confidence coverage includes the critical fields and calibrated thresholds meet review-risk requirements.
3. Use preprocessing only when its gain holds on calibration data; never apply
   the handwriting enhancement to clean CMS forms by default.
4. Add labeled samples only for template-specific errors that remain after description and method tuning.
5. Freeze all analyzer and preprocessing hashes before opening the holdout.
6. Promote a variant only after the blind holdout, not from the synthetic
   invoice or tuning subset.

## Microsoft references

- [Content Understanding best practices](https://learn.microsoft.com/azure/ai-services/content-understanding/concepts/best-practices)
- [Analyzer reference](https://learn.microsoft.com/azure/ai-services/content-understanding/concepts/analyzer-reference)
- [Document analyzer confidence, grounding, and labeled samples](https://learn.microsoft.com/azure/ai-services/content-understanding/document/analyzer-improvement)
- [Content Understanding pricing explainer](https://learn.microsoft.com/azure/ai-services/content-understanding/pricing-explainer)
- [Prebuilt analyzers](https://learn.microsoft.com/azure/ai-services/content-understanding/concepts/prebuilt-analyzers)
