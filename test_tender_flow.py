import os
import excel_reader
import matcher


def run_test():
    filename = r"C:\Users\merrlock\Downloads\Форма Коммерческого предложения и Структуры НМЦ ред. от 13.10.2026.xls"
    if not os.path.exists(filename):
        print(f"FAIL: Файл {filename} не найден!")
        return

    with open(filename, "rb") as f:
        file_bytes = f.read()

    try:
        rows = excel_reader.read_sheet_rows(file_bytes, filename)
    except Exception as e:
        print(f"FAIL: Ошибка чтения файла excel_reader: {e}")
        return

    extracted = []
    for r_idx, row in enumerate(rows):
        # Fixed EIS layout: C number, D product, F unit, G qty, K unit NMC.
        def cell(index):
            return row[index] if index < len(row) else ""

        num_val = str(cell(2)).strip()
        name_val = str(cell(3)).strip()
        if num_val and num_val.rstrip(".").isdigit() and len(name_val) > 3:
            try:
                qty = float(str(cell(6)).replace(" ", "").replace(",", "."))
            except (ValueError, TypeError):
                qty = 0.0
            try:
                nmc_unit = float(str(cell(10)).replace(" ", "").replace(",", "."))
            except (ValueError, TypeError):
                nmc_unit = 0.0
            extracted.append({
                "num": num_val,
                "raw_name": name_val,
                "unit": str(cell(5)).strip() or "м",
                "qty": qty,
                "nmc_unit": nmc_unit,
            })

    print(f"Успешно извлечено {len(extracted)} позиций из боевого тендера (ожидалось 19).")
    assert len(extracted) == 19, f"Ожидалось 19 строк, найдено {len(extracted)}"

    success_matches = 0
    print("\n--- РЕЗУЛЬТАТЫ СОПОСТАВЛЕНИЯ ПОЗИЦИЙ ТЕНДЕРА ---")
    for item in extracted:
        res = matcher.match_item(item["raw_name"])
        status = res["status"]
        matched_item = res.get("matched_item")
        matched_name = matched_item.get("name", "—") if isinstance(matched_item, dict) else "—"
        if status in ("EXACT", "AMBIGUOUS"):
            success_matches += 1
        print(f"[{status:9s}] {item['num']:3s} {item['raw_name'][:48]:48s} | Склад: {matched_name}")

    print(f"\nИТОГ: {success_matches} из {len(extracted)} позиций успешно обработаны складом!")
    assert success_matches >= 17, f"Слишком мало точных совпадений: {success_matches}/19"
    print("ALL TENDER TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_test()
