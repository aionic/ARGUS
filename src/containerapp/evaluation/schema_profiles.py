"""Convert canonical JSON Schemas into Content Understanding analyzer profiles."""

from __future__ import annotations

import re
from typing import Any

_COMMERCIAL_NUMERIC = re.compile(
    r"(price|total|value|weight|quantity|count|discount|tax|amount|charge)",
    re.IGNORECASE,
)

_CMS_DESCRIPTIONS = {
    "od_id_number_1": "Insured identification number from CMS-1500 box 1a.",
    "od_pat_name_1": "Patient full name from CMS-1500 box 2, preserving the printed order.",
    "od_pat_dob_1": "Patient date of birth from CMS-1500 box 3.",
    "od_ins_name_1": "Insured full name from CMS-1500 box 4.",
    "od_pat_addr_1": "Patient street address from CMS-1500 box 5.",
    "od_pat_city_1": "Patient city from CMS-1500 box 5.",
    "od_pat_state_1": "Patient state from CMS-1500 box 5.",
    "od_pat_zip_1": "Patient ZIP or postal code from CMS-1500 box 5.",
    "od_pat_phone_1": "Patient telephone number from CMS-1500 box 5.",
    "od_ins_grp_1": "Insured policy or group number from CMS-1500 box 11.",
    "od_pat_acct_no_1": "Patient account number from CMS-1500 box 26.",
    "od_total_chg_1": "Total charges from CMS-1500 box 28.",
    "od_amount_paid_1": "Amount paid from CMS-1500 box 29.",
    "od_balance_1": "Balance due from CMS-1500 box 30.",
}

_CMS_SERVICE_FIELDS = {
    "from_date": "service start date",
    "to_date": "service end date",
    "place_ser": "place of service code",
    "code": "procedure or HCPCS code",
    "mod": "procedure modifier",
    "dcode": "diagnosis pointer",
    "charges": "line charge amount",
    "days": "days or units",
    "epsdt": "EPSDT indicator",
    "emg": "emergency indicator",
}

CRITICAL_CONFIDENCE_FIELDS = {
    "cms1500": [
        "od_id_number_1",
        "od_pat_name_1",
        "od_pat_dob_1",
        "od_pat_acct_no_1",
        "od_total_chg_1",
        "od_diag_*",
        "od_code_*",
        "od_charges_*",
    ],
    "commercial-documents": [
        "invoice_date",
        "invoice_number",
        "invoice_total",
        "total",
        "vendor_name",
        "vendor_tax_id",
        "customer_number",
        "customer_po",
    ],
    "enrollments": [
        "medical_group_number",
        "benefit_number",
        "proposed_effective_date",
        "company_name",
        "employee_dob",
        "employee_last_name",
        "employee_first_name",
        "employee_ssn",
        "dependents[]*",
    ],
    "invoice-demo": [
        "Customer Name",
        "Invoice Number",
        "Date",
        "Payment Due",
        "Billing info.Customer ID",
        "Table.Total",
    ],
}


def json_schema_to_example(schema: dict[str, Any]) -> dict[str, Any]:
    if _schema_type(schema) != "object":
        raise ValueError("Root extraction schema must be an object")
    return {
        name: _example_value(field_schema)
        for name, field_schema in (schema.get("properties") or {}).items()
        if isinstance(field_schema, dict)
    }


def build_example_schema(
    dataset: str,
    schema: dict[str, Any],
    profile: str,
) -> dict[str, Any]:
    flat = json_schema_to_example(schema)
    if dataset != "cms1500" or profile == "baseline":
        return flat

    repeated = _cms_repeated_fields(schema)
    repeated_names = {name for field_names in repeated.values() for name in field_names}
    claim = {name: value for name, value in flat.items() if name not in repeated_names}
    service_line = {base: flat[field_names[0]] for base, field_names in repeated.items()}
    return {"claim": claim, "service_lines": [service_line]}


def adapt_extracted_data(
    dataset: str,
    profile: str,
    extracted: dict[str, Any],
    flat_template: dict[str, Any],
) -> dict[str, Any]:
    if dataset != "cms1500" or profile == "baseline":
        return extracted

    canonical = deepcopy_json(flat_template)
    claim = extracted.get("claim")
    if isinstance(claim, dict):
        for name, value in claim.items():
            if name in canonical:
                canonical[name] = value
    repeated = _cms_repeated_fields_from_names(flat_template.keys())
    service_lines = extracted.get("service_lines")
    if isinstance(service_lines, list):
        for index, line in enumerate(service_lines[:6], start=1):
            if not isinstance(line, dict):
                continue
            for base, value in line.items():
                field_name = f"od_{base}_{index}"
                if field_name in canonical and base in repeated:
                    canonical[field_name] = value
    return canonical


def adapt_confidence(
    dataset: str,
    profile: str,
    confidence: dict[str, float],
    flat_template: dict[str, Any],
) -> dict[str, float]:
    if dataset != "cms1500" or profile == "baseline":
        return confidence

    canonical: dict[str, float] = {}
    for path, score in confidence.items():
        if path.startswith("claim."):
            target = path[len("claim.") :]
        else:
            match = re.fullmatch(r"service_lines\[(\d+)\]\.(.+)", path)
            if not match:
                continue
            index = int(match.group(1)) + 1
            base = match.group(2)
            target = f"od_{base}_{index}"
            if target not in flat_template and base.startswith("f_"):
                target = f"od_{base[2:]}_{index}"
        if target in flat_template:
            canonical[target] = score
    return canonical


def build_analyzer_options(
    dataset: str,
    schema: dict[str, Any],
    profile: str,
    explicit_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if profile != "baseline":
        field_hints = (
            _cms_semantic_field_hints(schema)
            if dataset == "cms1500" and profile != "baseline"
            else _field_hints(dataset, schema)
        )
        options = {
            "schema_name": schema.get("title") or schema.get("schema_id") or dataset,
            "schema_description": _schema_description(dataset, schema),
            "field_hints": field_hints,
        }
    if profile in {"selective-confidence", "full-confidence"}:
        if profile == "full-confidence":
            options["confidence_fields"] = ["*"]
        elif dataset == "cms1500":
            options["confidence_fields"] = [
                "claim.od_id_number_1",
                "claim.od_pat_name_1",
                "claim.od_pat_dob_1",
                "claim.od_pat_acct_no_1",
                "claim.od_total_chg_1",
                "claim.od_diag_*",
                "service_lines[].code",
                "service_lines[].charges",
            ]
        else:
            options["confidence_fields"] = CRITICAL_CONFIDENCE_FIELDS.get(dataset, [])
    if profile == "full-confidence":
        options["default_method"] = "extract"
        options["config"] = {"estimateFieldSourceAndConfidence": True}
    if profile == "ocr-only":
        options["config"] = {"enableLayout": False, "returnDetails": True, "omitContent": False}
    return _merge(options, explicit_options or {})


def _field_hints(dataset: str, schema: dict[str, Any], prefix: str = "") -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    for name, field_schema in (schema.get("properties") or {}).items():
        if not isinstance(field_schema, dict):
            continue
        path = f"{prefix}.{name}" if prefix else name
        field_type = _preferred_cu_type(dataset, name, field_schema)
        hint: dict[str, Any] = {
            "description": _field_description(dataset, name, field_schema),
        }
        if field_type:
            hint["type"] = field_type
        if field_schema.get("enum"):
            hint["enum"] = field_schema["enum"]
            hint["method"] = "classify"
        hints[path] = hint

        if _schema_type(field_schema) == "object":
            hints.update(_field_hints(dataset, field_schema, path))
        elif _schema_type(field_schema) == "array":
            item_schema = field_schema.get("items")
            if isinstance(item_schema, dict) and _schema_type(item_schema) == "object":
                hints.update(_field_hints(dataset, item_schema, f"{path}[]"))
    return hints


def _cms_semantic_field_hints(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    repeated = _cms_repeated_fields(schema)
    repeated_names = {name for field_names in repeated.values() for name in field_names}
    for name, field_schema in (schema.get("properties") or {}).items():
        if not isinstance(field_schema, dict) or name in repeated_names:
            continue
        hints[f"claim.{name}"] = {
            "description": _field_description("cms1500", name, field_schema),
            "type": _preferred_cu_type("cms1500", name, field_schema) or "string",
        }
    for base, field_names in repeated.items():
        field_schema = (schema.get("properties") or {})[field_names[0]]
        description = _CMS_SERVICE_FIELDS.get(base, _humanize(base))
        hints[f"service_lines[].{base}"] = {
            "description": f"CMS-1500 box 24 {description} for this service line.",
            "type": _preferred_cu_type("cms1500", field_names[0], field_schema) or "string",
        }
    return hints


def _cms_repeated_fields(
    schema: dict[str, Any],
) -> dict[str, list[str]]:
    return _cms_repeated_fields_from_names((schema.get("properties") or {}).keys())


def _cms_repeated_fields_from_names(
    names: Any,
) -> dict[str, list[str]]:
    candidates: dict[str, dict[int, str]] = {}
    for name in names:
        match = re.fullmatch(r"od_(.+)_([1-6])", str(name))
        if not match:
            continue
        candidates.setdefault(match.group(1), {})[int(match.group(2))] = str(name)
    return {
        base: [indexed[index] for index in range(1, 7)]
        for base, indexed in candidates.items()
        if set(indexed) == set(range(1, 7))
    }


def _field_description(dataset: str, name: str, schema: dict[str, Any]) -> str:
    if schema.get("description"):
        return str(schema["description"])
    source_label = schema.get("x-source-label") or schema.get("x-source-field")
    if dataset == "cms1500":
        if name in _CMS_DESCRIPTIONS:
            return _CMS_DESCRIPTIONS[name]
        service_match = re.fullmatch(r"od_(.+)_([1-6])", name)
        if service_match and service_match.group(1) in _CMS_SERVICE_FIELDS:
            label = _CMS_SERVICE_FIELDS[service_match.group(1)]
            return f"CMS-1500 box 24 service line {service_match.group(2)} {label}."
        diagnosis_match = re.fullmatch(r"od_diag_([a-l])_1", name)
        if diagnosis_match:
            return f"Diagnosis code {diagnosis_match.group(1).upper()} from CMS-1500 box 21."
        return f"Extract the CMS-1500 field {_humanize(name)} exactly as printed on the form."
    if dataset == "commercial-documents":
        label = source_label or _humanize(name)
        return f"Extract the commercial document value labeled '{label}' exactly as shown."
    if dataset == "enrollments":
        label = source_label or _humanize(name)
        return (
            f"Extract the enrollment form value labeled '{label}'. The value may be handwritten; "
            "preserve identifiers, names, and dates exactly."
        )
    return f"Extract {_humanize(name)} exactly as shown in the document."


def _preferred_cu_type(dataset: str, name: str, schema: dict[str, Any]) -> str | None:
    schema_type = _schema_type(schema)
    if schema_type in {"object", "array", "boolean"}:
        return schema_type
    if schema.get("format") == "date":
        return "date"
    if dataset == "commercial-documents" and _COMMERCIAL_NUMERIC.search(name):
        return "number"
    return "string"


def _schema_type(schema: dict[str, Any]) -> str:
    value = schema.get("type", "string")
    if isinstance(value, list):
        preferred = [item for item in value if item != "null"]
        return str(preferred[0]) if preferred else "string"
    return str(value)


def _example_value(schema: dict[str, Any]) -> Any:
    schema_type = _schema_type(schema)
    if schema_type == "object":
        return {
            name: _example_value(child)
            for name, child in (schema.get("properties") or {}).items()
            if isinstance(child, dict)
        }
    if schema_type == "array":
        item_schema = schema.get("items")
        return [_example_value(item_schema)] if isinstance(item_schema, dict) else []
    if schema_type == "boolean":
        return False
    if schema_type in {"number", "integer"}:
        return 0.0
    return ""


def _schema_description(dataset: str, schema: dict[str, Any]) -> str:
    title = schema.get("title") or schema.get("schema_id") or dataset
    if dataset == "cms1500":
        return f"{title}. Extract professional medical claim fields from the CMS-1500 form."
    if dataset == "commercial-documents":
        return f"{title}. Extract invoice, shipping, customs, vendor, and order information."
    if dataset == "enrollments":
        return f"{title}. Extract typed and handwritten enrollment, coverage, and dependent information."
    return str(title)


def _humanize(name: str) -> str:
    text = re.sub(r"^od_", "", name, flags=re.IGNORECASE)
    return re.sub(r"_+", " ", text).strip()


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = {key: value for key, value in base.items()}
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def deepcopy_json(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _copy_value(child) for key, child in value.items()}


def _copy_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_copy_value(child) for child in value]
    return value
