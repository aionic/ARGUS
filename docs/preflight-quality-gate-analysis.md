# Preflight Quality Gate — Signal Analysis

> How ARGUS decides a scanned document is too low-quality to extract reliably, why
> the obvious signals don't work, and why per-word OCR confidence does.

**Status:** Shipped (commit `6f68081`).
**Scope:** Document-level preflight "is this scan legible enough to extract?" gate.
**Backends covered:** Content Understanding (CU) and Document Intelligence + GPT.
**Historical validation corpus:** 37 Conduent CMS-1500 / semi-structured
invoice samples, now consolidated under `demo/conduent-datasets`.

> **Dataset correction:** `bad.png` is pixel-identical to
> `WO7U9NJQprod.tiff`, so it is not an independent labeled-bad sample. The
> measurements below remain useful historical evidence, but recall figures that
> treat the alias as a separate negative must not be used as final bake-off
> quality claims.

---

## 1. Problem statement

The pipeline should **flag** documents that are too degraded to extract well and route
them to human review (and the auto-generated "bad scan" email workflow) **before**
burning extraction tokens on them. The historical degraded target was the
`bad.png` alias: a faint invoice scan with heavy black edge-bleed on the right
margin.

The original gate relied on OpenCV global image statistics (blur, contrast,
brightness, skew) plus, on the CU backend, the CU field-confidence map. Neither
flagged `bad.png`. This document explains why, what we measured, and the fix.

---

## 2. Key finding: `bad.png` is not an outlier

`bad.png` is **pixel-identical to `WO7U9NJQprod.tiff`** — same render, same metrics,
same 675-character OCR output. It is the *faintest member of a 7-document degraded
invoice family* (`WO7U9*` + `EO7U`), not a uniquely broken file.

Consequence: any signal that tries to isolate `bad.png` as an outlier against the
rest of the corpus is fighting the wrong battle. Several of its siblings are equally
or more degraded by pixel/text statistics, while the legitimate CMS-1500 claims
produce "noisy-looking" OCR of their own. The only thing that consistently separates
the truly *illegible* scans from the merely *sparse or structured* ones is **how
confident the OCR engine is in the characters it read**.

---

## 3. Signals evaluated

We empirically tested four cheap document-level signals across all 37 samples. **All
four fail** — each either misses `bad.png` or false-flags legitimate claims.

| # | Signal | What it measures | Why it fails on this corpus |
|---|--------|------------------|------------------------------|
| 1 | cv2 global blur / contrast / brightness | Whole-page sharpness & tone | Black edge-bleed blobs inflate variance, so `bad.png` reads "sharp" (blur var ≈ 1985, contrast ≈ 70). The page is ~90% white and the skew detector returns 0 (fails silently). Passes as good. |
| 2 | cv2 border-blob ratio (`border_black`) | Fraction of dark pixels in the outer 6% margin | Measures **scanner edge noise, not legibility**. The *readable* sibling `WO7U9OB4` has the **highest** border ratio (0.63) while illegible `bad.png` is lower (0.33). Cannot track legibility within the family. |
| 3 | CU field-confidence map | Per-field extraction confidence from Content Understanding | **Inverted** for this corpus. Sparse clean CMS-1500 forms read *low* (many empty boxes → field min ≈ 0.21), while `bad.png` reads *high* (min ≈ 0.63). No threshold separates good from bad. The flag never fires usefully. |
| 4 | OCR garbage-ratio | Fraction of alpha tokens that aren't word-like (no vowel / too long) | Dominated by **form structure, not legibility**. Eight legitimate CMS-1500 claims score *higher* (0.74–0.87) than `bad.png` (0.66) because sparse forms are full of codes, abbreviations, and field labels that look like "garbage" to a dictionary heuristic. |

### 3.1 Garbage-ratio evidence (selected, all 37 ranked)

```
garbage  nalpha  category      name
 0.867    233    cms1500       20211210NE1000800_nfnb.00000.tif   <- legit claim, HIGHEST
 0.849    199    cms1500       20211210NE1000226_nfnb.00000.tif   <- legit claim
 0.847    236    cms1500       20211210NE1000190_dfnc.tif         <- legit claim
 ...
 0.723    292    semi-struct   WO7U9R3I.tiff
 0.664    113    semi-struct   WO7U9NJQprod.tiff
 0.664    113    TARGET-BAD    bad.png            <- target sits MID-PACK
 ...
 0.016    247    cms1500       250128SB1001368.00004.tif
```

At **every** threshold, garbage-ratio flags 9–11 legitimate claims to catch `bad.png`.
It is unusable as a gate.

---

## 4. The signal that works: per-word OCR recognition confidence

Document Intelligence (`prebuilt-read` / `prebuilt-layout`) returns a **confidence
score per recognized word** — the engine's certainty about the characters it read.
This is fundamentally different from the four signals above:

- **It is not fooled by sparse content.** A clean form with few words still gets
  *high* confidence on the words it does have.
- **It is not fooled by non-dictionary text.** Crisp form codes (`HCFA`, `NPI`,
  procedure codes) get *high* recognition confidence even though they aren't English
  words — exactly where garbage-ratio breaks.
- **It directly tracks legibility.** Faint, broken, or warped glyphs produce low
  recognition confidence regardless of layout or border noise.

### 4.1 Measured separation (representative subset, CU backend)

| Document | mean word conf | frac words < 0.70 | Flagged? | Verdict |
|----------|---------------:|------------------:|:--------:|---------|
| **bad.png** (target) | **0.42** | **0.875** | **YES** | caught with huge margin |
| WO7U9OB4 (readable sibling) | 0.91 | 0.13 | no | correctly passed |
| WO7U9R3I (degraded sibling) | 0.78 | 0.36 | YES | genuinely degraded → review |
| 250128SB1001368 (clean claim) | 0.94 | 0.07 | no | passed |
| 20211210NE1000001 (clean claim) | 0.98 | 0.01 | no | passed |
| 20211210NE1000800 (clean, garbage-ratio 0.87) | 0.91 | 0.13 | no | **the FP that fooled signal #4 — passes** |
| 4106000027 (handwriting) | 0.97 | 0.04 | no | passed |

The clean claim that scored *worst* on garbage-ratio (0.87) reads at **0.91** word
confidence and is correctly **not** flagged. `bad.png` at **0.42** is far below the
0.80 threshold. The signal cleanly separates illegible from legible — even within the
same document family (readable `WO7U9OB4` 0.91 vs illegible `bad.png` 0.42).

---

## 5. Implementation

Backend-agnostic, with the confidence signal sourced for free wherever possible.

| File | Change |
|------|--------|
| `src/containerapp/ai_ocr/azure/doc_intelligence.py` | `summarize_word_confidence(result)` aggregates per-word confidence into `{n_words, mean, p10, min, frac_low, word_min}`. `get_read_confidence(path)` runs the lightweight `prebuilt-read` model purely for the signal (CU path). `get_ocr_results_with_confidence(path)` reuses the existing `prebuilt-layout` call so the GPT/azure path gets the signal at **no extra cost**. |
| `src/containerapp/ai_ocr/process.py` | `run_ocr_processing` captures per-chunk confidence on the azure OCR path into `properties._ocr_conf_chunks`. |
| `src/containerapp/blob_processing.py` | `_ocr_confidence_preflight` (gather via read probe) + `_aggregate_ocr_confidence` (token-weighted aggregation, flag reasons). CU branch runs the read probe; GPT branch reuses captured layout stats (falls back to a probe if OCR was skipped). Stores `properties.ocr_confidence`; flags `low_ocr_confidence`. The inverted CU field-confidence flag was **removed**. `_set_flag` now **merges** reasons across stages instead of overwriting. |
| `scripts/cms1500_score.py` | `_poll_document` waits for `state.processing_completed` (was `ocr_completed`, which fired before extraction on the GPT path and returned empty results). |

### 5.1 Backend behavior

- **Content Understanding** does its own OCR and exposes only (inverted) field
  confidence, so preflight runs a dedicated `prebuilt-read` probe. Cost ≈
  **$0.0010–0.0015/page** extra — the legibility gate's price on this backend.
- **Document Intelligence + GPT** already runs `prebuilt-layout`; the confidence is
  harvested from that same call, so **no extra cost**.

---

## 6. Thresholds (env-tunable, no redeploy)

| Variable | Default | Meaning |
|----------|--------:|---------|
| `ENABLE_OCR_CONFIDENCE_PREFLIGHT` | `true` | Master switch for the gate. |
| `OCR_CONFIDENCE_WORD_MIN` | `0.70` | A word below this counts as "low confidence". |
| `OCR_CONFIDENCE_MEAN_MIN` | `0.80` | Flag if token-weighted mean confidence is below this. |
| `OCR_CONFIDENCE_LOW_FRAC_MAX` | `0.25` | Flag if more than this fraction of words are low-confidence. |

A document is flagged (`low_ocr_confidence` / `high_low_confidence_word_fraction`) if
**either** the mean drops below `MEAN_MIN` **or** the low-confidence fraction exceeds
`LOW_FRAC_MAX`. Because thresholds are env vars, they can be retuned on the running
container app and take effect on the next document — no rebuild.

**Sensitivity vs precision:** the default (0.80) flags `bad.png` *and* genuinely
degraded siblings like `WO7U9R3I` (0.78). To fire only on catastrophic scans, lower
`OCR_CONFIDENCE_MEAN_MIN` to ~0.72 — `bad.png` (0.42) still flags while `WO7U9R3I`
(0.78) passes.

---

## 7. Validation results (37 samples, CU backend)

Image `diconf-20260624004847`, dataset on Content Understanding / standard tier.
Full report: `files/cms1500_cu_diconf.json`.

| Metric | Value |
|--------|------:|
| Known-bad recall (`bad.png`) | **1.0** |
| False negatives | **0** |
| False positives on the 22 labeled claims | **0** |
| Additional flags | 1 (`WO7U9R3I`, a genuinely degraded invoice — correct) |
| FP rate on "good" set (harness, treats only bad.png as known-bad) | 2.8% (1/36) |
| Avg cost / page (CU) | ≈ $0.01 |

`bad.png` is now caught with a wide margin, no legitimate labeled claim is flagged,
and the only extra flag is a document a human reviewer would also reject.

> Note: extraction field accuracy on this run (≈0.38 micro) vs an earlier CU run
> (≈0.47) reflects CU run-to-run variance on these degraded synthetic scans; the
> preflight changes do not touch CU extraction logic.

---

## 8. Why not combine signals?

Combining cv2 border-blob + faint-ink + garbage-ratio was considered and rejected:
each adds false positives (border noise on readable docs, degenerate Otsu on color
TIFFs, form-code "garbage" on real claims) without improving separation that
word-confidence already achieves cleanly on its own. The cv2 global quality report is
still computed and stored (`properties.image_quality`) as diagnostic metadata and for
the blank-page / tiny-image cases it does handle well — it is simply no longer the
primary legibility gate.

---

## 9. Stored telemetry

Every processed document now carries, under `properties`:

- `ocr_confidence`: `{ mean, frac_low, min, n_words, word_min, per_chunk[] }`
- `flag`: `{ flagged, reasons[], stage, flagged_at }` (reasons merged across stages)
- `image_quality` / `image_quality_warning`: cv2 diagnostics (unchanged)

This supports cost/quality dashboards and the bad-scan email workflow, and lets
thresholds be re-tuned from real production distributions.

---

## 10. Future work

- Surface `ocr_confidence.mean` and the flag reasons in the frontend review screen,
  next to the cost-per-page tier controls.
- Feed `ocr_confidence` into the auto-generated "bad scan" email so the message can
  cite the concrete legibility problem.
- On the CU backend, evaluate whether CU's own OCR exposes a span/line confidence we
  could harvest to avoid the extra `prebuilt-read` probe cost.
- Calibrate thresholds per dataset once a larger labeled distribution is available.

## 11. PaddleOCR pre-gate experiment (cost-saving probe)

Section 4 established that **recognition confidence** is the only reliable legibility
signal. The Document Intelligence (DI) `prebuilt-read` probe that produces it, however,
is itself a *paid* Document Intelligence call — so on the CU backend we pay for DI just
to decide whether to pay for CU. This experiment asks: can a **self-hosted, ~free OCR
engine** produce the same confidence signal *before* any paid call, and short-circuit
bad scans to save the DI **and** CU/GPT spend?

### 11.1 Design

A standalone **PaddleOCR** microservice (FastAPI, PP-OCRv4 English, CPU) runs as an
**internal-ingress Azure Container App** (`ca-argus-paddleocr`, scale-to-zero 0–3,
2 vCPU / 4 GiB). The backend renders page images and POSTs them to `POST /assess`, which
returns aggregate per-line recognition confidence `{mean, frac_low, min, n_lines}` — the
same shape as the DI word-confidence gate.

- **Insertion point:** `blob_processing.process_blob`, immediately after `file_paths`
  is built and **before** the CU-vs-GPT extraction branch (so no paid call has run yet).
- **Verdict:** `bad` if `mean < PADDLE_CONFIDENCE_MEAN_MIN` (0.80) **or**
  `frac_low > PADDLE_CONFIDENCE_LOW_FRAC_MAX` (0.25); low line = score `< PADDLE_CONFIDENCE_WORD_MIN` (0.70).
  These mirror the DI-gate thresholds.
- **Modes:** `block` (default) short-circuits — skips DI/CU, flags the document
  (`stage=paddle_pregate`), records the avoided cost, and marks it complete for review.
  `advisory` flags only and proceeds.
- **Enablement:** opt-in — `ENABLE_PADDLE_PREGATE` (default off) with a per-dataset
  `processing_options.enable_paddle_pregate` override. Requires `PADDLE_OCR_URL`.
- **Backstop:** the DI word-confidence gate (section 4) still runs for any document that
  *passes* Paddle, so Paddle only ever *adds* recall — it never weakens it.

### 11.2 Calibration results (37 samples, `/assess`, default thresholds)

| Metric | Result |
| --- | --- |
| Recall on known-bad (`bad.png` ≡ `WO7U9NJQprod`) | **1.0** (blocked: mean 0.62, frac_low 0.89) |
| False positives on 22 labeled-good claims | **0** (0.0%) |
| Total blocked | 3 — `bad.png`, `WO7U9NJQprod` (= bad.png), `WO7U9SLIprod` (mean 0.64, genuinely degraded) |
| Good-claim confidence range | mean 0.88–0.98, frac_low ≤ 0.17 |

Paddle cleanly separates the worst scans from legitimate sparse/structured forms with
**zero false positives** on real claims. One borderline degraded invoice that the DI gate
flagged (`WO7U9R3I`, DI conf 0.78) reads 0.85 on Paddle and passes the pre-gate — the DI
backstop still catches it downstream, exactly as designed (Paddle is the cheap first
filter, DI the precise backstop). Harness: `scripts/paddle_pregate_experiment.py`;
report: `files/paddle_pregate_experiment.json`.

### 11.3 Live end-to-end validation (Azure)

Deployed to `rg-argus-dev` and exercised through the live API:

| Document | Paddle | `extraction_backend_used` | Flagged | Cost |
| --- | --- | --- | --- | --- |
| `bad.png` | mean 0.68 → **block** | `skipped_paddle_pregate` | `paddle_pregate` | CU call **avoided** |
| `NE1000001` (good) | mean 0.97 → pass | `content_understanding` | no | $0.01/page |

The Paddle app cold-started from zero on first call (proving internal-ingress
reachability), blocked `bad.png` before Content Understanding ran, and let the good claim
flow through normally. Stored telemetry: `properties.paddle_pregate` (stats) and
`properties.flag` (`stage=paddle_pregate`).

### 11.4 Cost model

Per blocked document the pipeline avoids the DI `prebuilt-read` probe **and** the
CU/GPT extraction call (≈ $0.01/page CU on this dataset, more on GPT tiers). PaddleOCR
compute is scale-to-zero, so it bills only while assessing. The pre-gate therefore turns
the *most expensive* inputs (illegible scans that would extract poorly anyway) into the
*cheapest* outcome (a flag + review routing).

## 12. OpenCV's role, re-evaluated

The original preprocessor used OpenCV pixel metrics (Laplacian blur variance,
brightness/contrast, skew) as a quality **gate**. Sections 2–3 showed this is the wrong
gate: it *missed* `bad.png` (blur 5190, contrast 73 — black artifacts inflate the
metrics) and *false-flagged ~89%* of legitimate sparse B&W forms before per-dataset
tuning. With Paddle + DI now owning the legibility decision on recognition confidence,
OpenCV's pixel metrics are **demoted from gating to advisory**:

- `ENABLE_QUALITY_FLAGGING` (default **off**) gates whether cv2 metrics set a hard
  `quality` flag. cv2 metrics are still **computed and stored** (`properties.image_quality`,
  `image_quality_warning`) as hints for the rescan-email screen — they just no longer
  block or route on their own.
- OpenCV's genuinely additive capability is **enhancement** (`enhance_image`: deskew,
  denoise, CLAHE, upscale, binarize), which *fixes* borderline scans rather than judging
  them. This remains available but **opt-in** (`enable_enhancement`) and unvalidated on
  this corpus — it should be A/B'd against extraction accuracy before being relied upon,
  since aggressive denoise can erase faint handwriting.
