"""Extraction schemas and prompts for the Conduent per-document-type pricing run.

Schemas for CMS-1500, commercial invoices, and enrollments are derived from the
canonical corpus under ``demo/conduent-datasets/schemas``. UB-04, IRS W-9, and
EOB/remittance have no supplied truth data, so their field sets are authored here
from the standard published form layouts purely so extraction (and therefore token
cost) is representative of production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_SCHEMA_DIR = REPO_ROOT / "demo" / "conduent-datasets" / "schemas"

# Standard UB-04 (CMS-1450) form locators.
UB04_FIELDS = [
    "fl01_provider_name",
    "fl01_provider_address",
    "fl03a_patient_control_number",
    "fl04_type_of_bill",
    "fl05_federal_tax_id",
    "fl06_statement_from_date",
    "fl06_statement_through_date",
    "fl08_patient_name",
    "fl09_patient_address",
    "fl10_patient_birth_date",
    "fl11_patient_sex",
    "fl12_admission_date",
    "fl14_priority_type_of_admission",
    "fl15_source_of_admission",
    "fl17_patient_discharge_status",
    "fl42_revenue_code",
    "fl43_revenue_description",
    "fl44_hcpcs_rate_codes",
    "fl45_service_date",
    "fl46_service_units",
    "fl47_total_charges",
    "fl48_non_covered_charges",
    "fl50_payer_name",
    "fl51_health_plan_id",
    "fl56_billing_provider_npi",
    "fl58_insured_name",
    "fl60_insured_unique_id",
    "fl63_treatment_authorization_code",
    "fl66_diagnosis_version_qualifier",
    "fl67_principal_diagnosis_code",
    "fl69_admitting_diagnosis_code",
    "fl76_attending_provider_npi",
    "fl76_attending_provider_name",
]

# IRS Form W-9 (Request for Taxpayer Identification Number and Certification).
W9_FIELDS = [
    "line1_name",
    "line2_business_name",
    "line3_federal_tax_classification",
    "line3_llc_tax_classification",
    "line4_exempt_payee_code",
    "line4_fatca_exemption_code",
    "line5_address",
    "line6_city_state_zip",
    "line7_account_numbers",
    "requester_name_and_address",
    "part1_ssn",
    "part1_ein",
    "part2_signature_present",
    "part2_date_signed",
]

# Explanation of benefits / remittance advice.
EOB_FIELDS = [
    "payer_name",
    "payer_address",
    "payee_name",
    "payee_npi",
    "check_or_eft_number",
    "check_or_eft_date",
    "check_amount",
    "patient_name",
    "patient_account_number",
    "claim_number",
    "service_date_from",
    "service_date_to",
    "procedure_code",
    "procedure_description",
    "service_units",
    "billed_amount",
    "allowed_amount",
    "deductible_amount",
    "coinsurance_amount",
    "copay_amount",
    "paid_amount",
    "patient_responsibility",
    "adjustment_reason_code",
    "remark_code",
    "total_billed",
    "total_paid",
]

_BASE_PROMPT = (
    "You are extracting structured data from a scanned {description}. "
    "Return one JSON object using exactly the keys in the provided schema. "
    "Copy values verbatim as they appear on the form, preserving the original formatting of "
    "dates, identifiers, and currency amounts. Do not infer, normalize, or calculate values. "
    "If a field is absent, illegible, or not applicable, return an empty string for that key. "
    "Do not add keys that are not in the schema."
)

_SPECS: dict[str, dict[str, Any]] = {
    "cms1500": {
        "corpus_schema": "cms1500.schema.json",
        "description": "CMS-1500 health insurance claim form",
    },
    "commercial-document": {
        "corpus_schema": "commercial-document.schema.json",
        "description": "commercial or customs invoice",
    },
    "enrollment": {
        "corpus_schema": "enrollment.schema.json",
        "description": "handwritten employee enrollment or change form",
    },
    "ub04": {
        "fields": UB04_FIELDS,
        "description": "UB-04 (CMS-1450) institutional claim form",
    },
    "w9": {
        "fields": W9_FIELDS,
        "description": "IRS Form W-9 taxpayer identification and certification form",
    },
    "eob": {
        "fields": EOB_FIELDS,
        "description": "explanation of benefits or remittance advice statement",
    },
}


def _flatten_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a JSON Schema object into a flat example object of empty strings."""
    example: dict[str, Any] = {}
    for name, definition in (schema.get("properties") or {}).items():
        if not isinstance(definition, dict):
            example[name] = ""
            continue
        types = definition.get("type")
        types = types if isinstance(types, list) else [types]
        if "object" in types or definition.get("properties"):
            example[name] = _flatten_json_schema(definition)
        elif "array" in types:
            items = definition.get("items")
            example[name] = [_flatten_json_schema(items)] if isinstance(items, dict) and items.get("properties") else []
        else:
            example[name] = ""
    return example


def example_schema(schema_key: str) -> dict[str, Any]:
    """Return the flat example schema ARGUS uses to shape extraction output."""
    spec = _SPECS[schema_key]
    corpus_schema = spec.get("corpus_schema")
    if corpus_schema:
        raw = json.loads((CORPUS_SCHEMA_DIR / corpus_schema).read_text(encoding="utf-8"))
        return _flatten_json_schema(raw)
    return {field: "" for field in spec["fields"]}


def model_prompt(schema_key: str) -> str:
    """Return the extraction prompt for a schema key."""
    return _BASE_PROMPT.format(description=_SPECS[schema_key]["description"])


def schema_source(schema_key: str) -> str:
    """Describe where a schema came from, for report provenance."""
    corpus_schema = _SPECS[schema_key].get("corpus_schema")
    return f"corpus:{corpus_schema}" if corpus_schema else "authored:standard-form-layout"
