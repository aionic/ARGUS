# CMS-1500 Claims — Pipeline Validation Dataset

Synthetic / de-identified health-insurance claim documents used to validate the
ARGUS extraction pipeline end-to-end: **preflight quality flagging** and
**structured metadata extraction** scored against ground truth.

> All documents are synthetic or de-identified ("fake but realistic") sample data.
> No production PHI/PII is included.

## Layout

```
demo/cms1500-claims/
  samples/            # 37 pipeline input images (.tif/.tiff/.png)
  ground-truth/
    BW Truth.csv      # 6 labeled CMS-1500 forms (black-ink)  — ~200 od_* fields/row
    Red Truth.csv     # 16 labeled CMS-1500 forms (red-ink)   — ~200 od_* fields/row
    structured-claims-manifest.json   # sample -> truth row + deid_status mapping
    document_review.xlsx              # human review helper
```

## Sample inventory (maps to the 3 POC use cases)

| Group | Files | Truth? | POC use case |
|---|---|---|---|
| `20211210NE1000*.tif` | 22 | ✅ BW/Red Truth.csv (all 22 rows matched) | (1) Structured claims |
| `250128SB*`, `250205C22*` | 3 | ❌ | (1) Structured claims (newer batch) |
| `4106*_HW*.tiff` | 4 | ❌ | (2) Handwriting extraction |
| `EO7*`, `WO7*prod.tiff` | 7 | ❌ | (3) Semi-structured (EOB-style) |
| `bad.png` | 1 | ❌ (negative test) | Preflight — degraded/faded scan |

## Ground-truth schema

The `*_Truth.csv` columns are CMS-1500 form boxes encoded as `od_<field>_<n>`
(e.g. `od_pat_name_1`, `od_pat_dob_1`, `od_diag_a_1`, `od_charges_1..6`,
`od_total_chg_1`, `od_24j_npi_id_*`). The `ImageName` column joins a CSV row to a
file in `samples/` (case-insensitive). These rows are the **scoring reference**
for extraction accuracy.

## Notes / limitations

- `bad.png` is a genuinely degraded scan (faded text, skew, heavy edge artifacts).
  It is the intended preflight failure case.
- Internal account/deal context and the Safe Harbor / de-identification reference
  documents from the original resource bundle are intentionally **not** committed
  here; this folder contains only the pipeline test fixtures.
