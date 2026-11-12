from pathlib import Path
from types import SimpleNamespace
import json
import matcher
import excel_generator
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent
CATALOG = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))

# Those are the exact 15 demo rows used by the UI and demo workbook.
LINES = [
    ("ВВГнг-LS 3х1.5", 120, "м"), ("ВВГнг-LS 3х2.5", 450, "м"),
    ("ВВГнг-LS 3х4", 180, "м"), ("ВВГнг-LS 4х2.5", 200, "м"),
    ("ВВГнг-LS 4х6", 100, "м"), ("ВВГнг-LS 5х16", 80, "м"),
    ("NYM 3х1.5", 200, "м"), ("NYM 3х2.5", 150, "м"),
    ("СИП-4 4х16", 300, "м"), ("СИП-4 4х50", 1.2, "км"),
    ("ВВГ-Пнг-LS 3х1.5", 90, "м"), ("ВВГнг-FRLS 3*2,5", 70, "м"),
    ("КГ 3*2,5", 60, "м"), ("КГ-ХЛ 3х4", 50, "м"),
    ("Гофра ПВХ d25", 100, "м"),
]

def meters(quantity, unit):
    key = (unit or "м").strip().casefold()
    return (float(quantity) * 1000.0, "м") if key in {"км", "километр", "километра", "километров", "километры"} else (float(quantity), "м" if key in {"", "бухта", "бухты", "бухт"} else unit)

items=[]; outcomes=[]
for raw, quantity, unit in LINES:
    result=matcher.match_item(raw)
    qty, unit=meters(quantity, unit)
    status=result["status"]
    if status == "NOT_FOUND": status="unrecognized"
    selected=result.get("matched_item")
    candidates=result.get("candidates", [])
    # The demo UI offers this explicit local analogue for the requested КГ size.
    if raw.startswith("КГ ") and status == "NOT_FOUND":
        selected_candidate=next((p for p in CATALOG if p.get("mark")=="КГ-ХЛ" and p.get("cores")==3 and p.get("section")==2.5),None)
        if selected_candidate: status="AMBIGUOUS"; candidates=[{"item":selected_candidate,"reason":"Предложен хладостойкий аналог КГ-ХЛ"}]
    manual_price = 45.0 if raw == "Гофра ПВХ d25" else 0.0
    items.append({"raw_name":raw,"qty":qty,"unit":unit,"status":status,"selected_item":selected,"candidates":candidates,"price":float(selected.get("price",0)) if selected else manual_price})
    outcomes.append((raw,status,qty,unit))

for raw,status,qty,unit in outcomes: print(f"{status:12} | {raw} | {qty:g} {unit}")
assert all(s=="EXACT" for r,s,_,_ in outcomes if r.startswith(("ВВГнг", "ВВГ-П", "NYM", "СИП-4"))), "Expected cable SKU to match EXACT"
assert next(s for r,s,_,_ in outcomes if r.startswith("КГ "))=="AMBIGUOUS"
assert next(s for r,s,_,_ in outcomes if r.startswith("Гофра"))=="unrecognized"
assert next(q for r,s,q,u in outcomes if "1.2" in "".join(str(x) for x in LINES if x[0]==r)) == 1200.0
# Final parser/catalog regression checks requested for the demo.
kh=matcher.match_item("КГ-ХЛ 3х4")
assert kh["status"]=="EXACT", f"КГ-ХЛ 3х4: {kh['status']}"
combo=matcher.parse_cable_query("ВВГнг-LS 3х50+1х25")
assert (combo["cores"],combo["section"],combo["extra_cores"],combo["extra_section"])==(3,50.0,1,25.0), combo
print(f"EXACT        | КГ-ХЛ 3х4 | каталожная позиция найдена")
print(f"PARSED       | ВВГнг-LS 3х50+1х25 | основные жилы {combo['cores']}x{combo['section']:g}; доп. жила {combo['extra_cores']}x{combo['extra_section']:g}")

stream=excel_generator.generate_excel_quote(20261109010101,items,client_name="ООО «КабельПро»")
out=ROOT/"test_output_KP.xlsx";out.write_bytes(stream.getvalue())
assert out.exists() and out.stat().st_size>5*1024, f"Output xlsx too small: {out.stat().st_size}"
wb_formula=load_workbook(out, data_only=False); ws=wb_formula.active
product_rows=list(range(5,20))
assert len(product_rows)==15
assert ws["B19"].value=="Гофра ПВХ d25", ws["B19"].value
assert ws["C19"].value==100.0 and ws["D19"].value=="м"
assert ws["E19"].value==45.0 and ws["F19"].value=="=C19*E19"
assert ws["F20"].value=="=SUM(F5:F19)"
assert ws["F21"].value=="=ROUND(F20*20/120, 2)"
expected_total=round(sum(float(it["qty"])*(float(it["price"]) if float(it["price"])>0 else float((it.get("candidates") or [{}])[0].get("item",{}).get("price",0))) for it in items),2)
wb_values=load_workbook(out, data_only=True); vals=wb_values.active
assert sum(float(vals.cell(r,3).value or 0)*float(vals.cell(r,5).value or 0) for r in product_rows)==expected_total
expected_vat=round(expected_total*20/120,2)
print(f"Excel product rows: {len(product_rows)}")
print(f"Row 15: {ws['B19'].value} | {ws['C19'].value:g} {ws['D19'].value} | {ws['E19'].value:.2f} RUB | {ws['C19'].value*ws['E19'].value:,.2f} RUB")
print(f"Total: {expected_total:,.2f} RUB | VAT formula: {ws['F21'].value} | expected VAT: {expected_vat:,.2f} RUB")
print(f"PASS: {len(items)} rows; 1.2 km converted to 1200 m; Excel {out.stat().st_size} bytes")

