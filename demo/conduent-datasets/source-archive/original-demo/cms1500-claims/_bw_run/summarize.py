import json
from pathlib import Path

r = json.loads(Path("demo/cms1500-claims/_bw_run/report.json").read_text())
e = r["extraction"]
print("documents_scored =", e.get("documents_scored"))
for k, v in e.items():
    if isinstance(v, (int, float)) and k != "documents_scored":
        print(f"{k} = {round(v, 4) if isinstance(v, float) else v}")

pd = e.get("per_document") or {}
if pd:
    print("\n--- per document (non-empty field recall) ---")
    for name, s in pd.items():
        acc = s.get("nonempty_accuracy")
        acc = round(acc, 4) if isinstance(acc, float) else acc
        print(f"{name}: {acc}  ({s.get('nonempty_correct')}/{s.get('nonempty_total')})")

worst = e.get("worst_fields") or e.get("worst") or []
if worst:
    print("\n--- 15 weakest fields ---")
    for item in worst[:15]:
        print(f"{item[0]}: {round(item[1], 3)}")
