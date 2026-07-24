# Schema, Truth, and Source Mapping Guide

## Purpose

This guide explains:

- Which documents align to each CSV, Excel sheet, or JSON truth source.
- How the normalized JSON and JSONL files were created.
- How documents, truth records, schemas, and original source files connect.
- Which supplied documents have golden truth, schema-only coverage, or no truth.

No truth values are reproduced in this guide.

## Core relationship

The consolidated structure uses three linked layers:

```text
document file
   |
   | document_id
   v
manifest.jsonl
   |
   +---- truth_record_id ----> truth\records.jsonl
   |
   +---- schema_id ----------> schemas\*.json
```

### Manifest

Each dataset has a `manifest.jsonl` containing one record per logical document.
A manifest record provides:

- `document_id`: stable identifier for the logical document.
- `document_path`: canonical active copy.
- `source_path`: source file selected for the canonical copy.
- `provenance`: every supplied path with the same file content.
- `content_sha256`: byte-level file hash.
- `pixel_sha256`: decoded-image hash used to identify visually identical images.
- `truth_status`: `golden`, `schema_only`, or `unlabeled`.
- `truth_record_id`: pointer to expected extraction values, when available.
- `schema_id`: pointer to the extraction schema.

### Truth record

`truth\records.jsonl` contains one normalized golden record per line. Each record
includes:

- `truth_record_id`.
- `document_id`.
- `schema_id`.
- Original source file, sheet, and physical row information.
- `values`, containing the expected extraction output.

### Schema

Files under `schemas\` are JSON Schemas defining the expected extraction shape.
Schemas define fields and permitted data types; they do not contain the expected
truth values.

## JSON versus JSONL

### JSON

A `.json` file contains one JSON object. It is used for:

- Extraction schemas.
- `catalog.json`.
- Prediction outputs.
- Evaluation configuration and reports.

### JSONL

A `.jsonl` file contains one independent JSON object per line. It is used for:

- Dataset manifests.
- Normalized truth records.

JSONL makes individual documents easy to stream, compare, filter, and append
without loading the entire dataset into memory.

## Dataset summary

| Dataset | Documents | Truth source | Schema |
|---|---:|---|---|
| CMS-1500 | 22 | `BW Truth.csv` and `Red Truth.csv` | `cms1500-v1` |
| Commercial documents | 7 | `MS_Truth_Data.xlsx\Comm_Invoices` | `commercial-document-v1` |
| Enrollments | 4 | `MS_Truth_Data.xlsx\Enrollments` | `enrollment-v1` |
| Invoice demo | 1 | `ground_truth.json` | `invoice-demo-v1` |
| Medical demo | 1 | No truth supplied | `medical-report-v1` |
| Unlabeled CMS documents | 3 | No truth supplied | `cms1500-v1` |

## CMS-1500 claims

### Locations

```text
datasets\cms1500\documents\
datasets\cms1500\manifest.jsonl
datasets\cms1500\truth\records.jsonl
schemas\cms1500.schema.json
```

### Source-to-image join

The original CSV `ImageName` column is the join key. Matching is
case-insensitive. A trailing `.oci` suffix is ignored because the supplied
`.tif.oci` files decode as TIFF images.

`ImageName` is retained as source linkage metadata but is not an extraction
field inside `values`.

### BW Truth mapping

Original truth:

```text
source-archive\bw-truth\BW Truth.csv
```

| Physical CSV row | Canonical image |
|---:|---|
| 2 | `20211210NE1000031_nfnb.00000.tif` |
| 3 | `20211210NE1000044_nfnb.00000.tif` |
| 4 | `20211210NE1000226_nfnb.00000.tif` |
| 5 | `20211210NE1000294_nfnb.00000.tif` |
| 6 | `20211210NE1000800_nfnb.00000.tif` |
| 7 | `20211210NE1000884_nfnb.00000.tif` |

Each image also has an exact source copy under `source-archive\bw-truth` with a
`.tif.oci` filename.

### Red Truth mapping

Original truth:

```text
source-archive\red-truth\Red Truth.csv
```

| Physical CSV row | Canonical image |
|---:|---|
| 2 | `20211210NE1000001_dfnc.tif` |
| 3 | `20211210NE1000035_dfnc.tif` |
| 4 | `20211210NE1000053_dfnc.tif` |
| 5 | `20211210NE1000104_dfnc.tif` |
| 6 | `20211210NE1000112_dfnc.tif` |
| 7 | `20211210NE1000131_dfnc.tif` |
| 8 | `20211210NE1000176_dfnc.tif` |
| 9 | `20211210NE1000190_dfnc.tif` |
| 10 | `20211210NE1000200_dfnc.tif` |
| 11 | `20211210NE1000210_dfnc.tif` |
| 12 | `20211210NE1000356_dfnc.tif` |
| 13 | `20211210NE1000385_dfnc.tif` |
| 14 | `20211210NE1000547_dfnc.tif` |
| 15 | `20211210NE1000807_dfnc.tif` |
| 16 | `20211210NE1000808_dfnc.tif` |
| 17 | `20211210NE1000891_dfnc.tif` |

### CMS schema construction

The schema was built from the union of the two CSV header sets:

```text
BW extraction fields
+ Red-only od_prov_4
+ Red-only od_prov_5
- ImageName join column
= 198 extraction fields
```

The Red CSV uses Windows-1252 encoding, while the BW CSV is UTF-8-compatible.
Both were read and rewritten as UTF-8 JSONL.

### CSV-to-JSONL conversion

For each CSV data row:

1. Read the row into a dictionary keyed by CSV header.
2. Match `ImageName` to the canonical TIFF.
3. Generate stable `document_id` and `truth_record_id` values from the document
   content hash.
4. Store the original dataset and physical CSV row.
5. Copy the 198 extraction fields into `values`.
6. Write one JSON object as one line in `records.jsonl`.

Simplified shape:

```json
{
  "truth_record_id": "truth-cms1500-...",
  "document_id": "cms1500-...",
  "schema_id": "cms1500-v1",
  "source_dataset": "bw-truth",
  "source_row": 2,
  "values": {
    "od_medicare_1": "",
    "od_pat_name_1": "",
    "od_diag_a_1": ""
  }
}
```

The `source_row` number is the physical spreadsheet-style row number, including
the header as row 1.

## Commercial documents

### Locations

```text
datasets\commercial-documents\documents\
datasets\commercial-documents\manifest.jsonl
datasets\commercial-documents\truth\records.jsonl
schemas\commercial-document.schema.json
```

### Truth source

```text
source-archive\ms-truth-data\MS_Truth_Data.xlsx
Sheet: Comm_Invoices
```

### Source-to-image join

`Item ID (DCN)` is matched to the image filename stem after punctuation and case
are normalized. It is retained as `source_item_id`, rather than treated as an
extracted document field.

| Physical Excel row | Canonical image |
|---:|---|
| 2 | `EO7UGK9Bprod.tiff` |
| 3 | `WO7U9NJQprod.tiff` |
| 4 | `WO7U9OB4prod.tiff` |
| 5 | `WO7U9OZ0.tiff` |
| 6 | `WO7U9QP0.tiff` |
| 7 | `WO7U9R3I.tiff` |
| 8 | `WO7U9SLIprod.tiff` |

### Excel header normalization

The original 32 extractable Excel columns were converted to consistent JSON
keys:

```text
Invoice Date       -> invoice_date
Vendor Tax ID      -> vendor_tax_id
C&F Value          -> c_and_f_value
Quantity Ordered   -> quantity_ordered
```

Dates are converted to ISO-8601 strings. Numeric cells remain numeric, empty
cells become `null`, and text is retained as supplied.

Simplified truth record:

```json
{
  "truth_record_id": "truth-commercial-...",
  "document_id": "commercial-...",
  "schema_id": "commercial-document-v1",
  "source_workbook": "MS_Truth_Data.xlsx",
  "source_sheet": "Comm_Invoices",
  "source_row": 2,
  "source_item_id": "EO7UGK9Bprod",
  "values": {
    "invoice_date": null,
    "unit_price": 0,
    "vendor_name": ""
  }
}
```

The values above are illustrative placeholders, not copied truth.

## Enrollment and handwriting documents

### Locations

```text
datasets\enrollments\documents\
datasets\enrollments\manifest.jsonl
datasets\enrollments\truth\records.jsonl
schemas\enrollment.schema.json
```

### Truth source

```text
source-archive\ms-truth-data\MS_Truth_Data.xlsx
Sheet: Enrollments
```

### Source-to-image join

`Item ID (DCN)` is matched to the normalized image filename stem.

| Physical Excel row | Canonical image |
|---:|---|
| 2 | `4106000043_CF_IN_HW.tiff` |
| 3 | `4106000027_CF_FL_HW.tiff` |
| 4 | `4106000020_CF_IL_HW Redacted.tiff` |
| 5 | `4106001767_SG_CA_HW Redated.tiff` |

The images also have exact source copies under
`source-archive\external-handwriting`.

### Enrollment normalization

The source sheet contains 112 columns, including:

- Repeated child/dependent column names.
- An empty unnamed trailing column.
- Many fields that are not populated in the four supplied examples.

Normalization performs the following:

1. Excludes `Item ID (DCN)` from extracted truth and retains it as
   `source_item_id`.
2. Converts ordinary headers to normalized JSON keys.
3. Converts repeated child/dependent groups into a `dependents` array.
4. Removes the empty unnamed trailing schema field.
5. Retains a `source_values` array containing every original column position,
   original label, and raw cell value.

Simplified truth record:

```json
{
  "truth_record_id": "truth-enrollment-...",
  "document_id": "enrollment-...",
  "schema_id": "enrollment-v1",
  "source_workbook": "MS_Truth_Data.xlsx",
  "source_sheet": "Enrollments",
  "source_row": 2,
  "source_item_id": "...",
  "values": {
    "medical_group_number": null,
    "employee_dob": null,
    "dependents": []
  },
  "source_values": [
    {
      "column": 1,
      "label": "Item ID (DCN)",
      "value": "..."
    }
  ]
}
```

## Invoice demo

### Source package

```text
source-archive\original-demo\default-dataset\
```

| Artifact | Purpose |
|---|---|
| `Invoice Sample.pdf` | Input document |
| `ground_truth.json` | Golden expected extraction |
| `output_schema.json` | Original output template |
| `eval_data.jsonl` | Source evaluation example |
| `evaluation_schema.json` | Original evaluation options |
| `system_prompt.txt` | Extraction prompt |

The normalized invoice schema has 13 top-level properties with nested billing,
shipping, line-item, totals, and footer structures.

The supplied evaluation configuration used inconsistent evaluator names and
misspelled options. The consolidated design normalizes these concepts to names
such as:

- `ignore_dots`
- `ignore_number_sign`
- `ignore_commas`
- `ignore_dashes`
- `ignore_parentheses`
- `ignore_percentage_sign`

## Medical demo

### Source package

```text
source-archive\original-demo\medical-dataset\
```

| Artifact | Purpose |
|---|---|
| `eyes_surgery_pre_1_4.pdf` | Input document |
| `output_schema.json` | Extraction schema |
| `system_prompt.txt` | Extraction prompt |

No golden truth was supplied. The document is therefore `schema_only`.

The schema includes:

- `doctor`
- `patient`
- `post_surgery_follow_up`
- `pre_surgery_evaluation`
- `categorization`

## Unlabeled documents

These CMS-like documents have no matching row in BW Truth, Red Truth, or
`MS_Truth_Data.xlsx`:

```text
250128SB1001368.00004.tif
250205C22000227.00012.tif
250205C22001544.00004.tif
```

They use `cms1500-v1` as their candidate schema but have:

```json
{
  "truth_status": "unlabeled",
  "truth_record_id": null
}
```

## Excluded and archive-only material

### `bad.png`

`demo\cms1500-claims\samples\bad.png` is pixel-identical to
`WO7U9NJQprod.tiff`. It was therefore excluded from active datasets rather than
treated as an independent degraded negative test.

### Mistral package

The Mistral folder contains a prompt and a schema semantically identical to the
invoice demo schema, but no input document or truth. It remains archive-only.

## How IDs were created

Each canonical file receives a SHA-256 content hash. Stable UUID5-based IDs are
then derived from that hash and the dataset type:

```text
document content
    -> SHA-256
    -> UUID5 with dataset-specific prefix
    -> document_id / truth_record_id
```

This means exact duplicate source copies resolve to the same logical content and
can be represented by one canonical active document with multiple provenance
paths.

## How to trace a document manually

Given a canonical document:

1. Open the dataset's `manifest.jsonl`.
2. Search for its `original_filename`.
3. Copy its `document_id`, `truth_record_id`, and `schema_id`.
4. Search `truth\records.jsonl` for the `truth_record_id`.
5. Inspect `source_dataset` or `source_workbook`, `source_sheet`, and
   `source_row`.
6. Open the corresponding original CSV or workbook from `source-archive`.
7. Use the physical row number to locate the original truth.
8. Open the schema named by `schema_id` to see the expected extraction shape.

## Generation implementation

The deterministic consolidation process used:

- Python `csv.DictReader` for BW and Red CSV files.
- `openpyxl` with `data_only=True` for `MS_Truth_Data.xlsx`.
- Python JSON serialization for `.json` and `.jsonl`.
- Pillow for decoded-image hashes and metadata.
- SHA-256 and UUID5 for stable identity and deduplication.

The transformation is deterministic: no model-generated values were introduced
into normalized golden truth.

## Active demo manifests

The invoice and medical samples have canonical active manifests under
`datasets\invoice-demo` and `datasets\medical-demo`. The invoice links to its
normalized golden record; the medical sample is explicitly `schema_only`.
Their complete original source packages remain under
`source-archive\original-demo`.
