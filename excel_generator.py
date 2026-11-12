import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.comments import Comment

def _number(value, default=0.0):
    try: 
        return float(value)
    except (TypeError, ValueError): 
        return default

def generate_excel_quote(order_id: int, items: list[dict], client_name: str = "ООО «КабельПро»", discount_percent: float = 0.0) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Спецификация"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    font_bold = Font(name="Calibri", size=11, bold=True)
    font_regular = Font(name="Calibri", size=11)
    
    thin_border = Border(
        left=Side(style='thin', color="D3D3D3"),
        right=Side(style='thin', color="D3D3D3"),
        top=Side(style='thin', color="D3D3D3"),
        bottom=Side(style='thin', color="D3D3D3")
    )
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # Шапка документа
    ws.merge_cells("A1:F1")
    ws["A1"] = f"СПЕЦИФИКАЦИЯ К ЗАКАЗУ № {order_id}"
    ws["A1"].font = Font(name="Calibri", size=15, bold=True, color="1F4E79")
    ws.row_dimensions[1].height = 25

    discount_percent = _number(discount_percent)
    ws.merge_cells("A2:F2")
    disc_text = f" | Скидка: {discount_percent:g}%" if discount_percent > 0 else ""
    ws["A2"] = f"Контрагент: {client_name}{disc_text}"
    ws["A2"].font = Font(name="Calibri", size=11, italic=True, color="555555")
    ws.row_dimensions[2].height = 20

    headers = ["№", "Наименование кабельной продукции", "Кол-во", "Ед. изм.", "Цена за метр (с учетом наценки/скидки), ₽", "Сумма, ₽"]
    ws.append([])  # Пустая строка 3
    ws.append(headers)  # Строка 4 (шапка таблицы)
    ws.row_dimensions[4].height = 25

    for col_num in range(1, 7):
        cell = ws.cell(row=4, column=col_num)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    current_row = 5
    for idx, item in enumerate(items, start=1):
        product = item.get("selected_item")
        proposed_analogue = False
        if not isinstance(product, dict):
            product = None
        # Unconfirmed analogs still export with the first catalog recommendation.
        if product is None and item.get("status") == "AMBIGUOUS":
            candidates = item.get("candidates") or []
            first_candidate = candidates[0] if candidates else None
            if isinstance(first_candidate, dict):
                proposed = first_candidate.get("item")
                if isinstance(proposed, dict):
                    product = proposed
                    proposed_analogue = True
        raw_name = item.get("raw_name")
        qty = _number(item.get("qty", 1.0), 0.0)
        manual_price = _number(item.get("price", 0.0), 0.0)
        if not product and not (raw_name and (qty != 0.0 or manual_price != 0.0)):
            continue

        product = product if isinstance(product, dict) else None
        base_price = _number(item.get("price", product.get("price", 0) if product else 0.0), 0.0)
        if product and base_price <= 0:
            base_price = _number(product.get("price", 0), 0.0)
        if base_price <= 0:
            continue
        final_price = round(base_price * (1 - discount_percent / 100), 2)

        ws.cell(row=current_row, column=1, value=idx).alignment = align_center
        name_cell = ws.cell(row=current_row, column=2, value=product.get("name") if product else item.get("raw_name", "Не указано"))
        name_cell.alignment = align_left
        if proposed_analogue:
            name_cell.comment = Comment(f"Предложен аналог: {product.get('name', '')}", "КабельПро")
        ws.cell(row=current_row, column=3, value=qty).alignment = align_right
        ws.cell(row=current_row, column=4, value=item.get("unit") or (product.get("unit") if product else "м")).alignment = align_center
        
        cell_price = ws.cell(row=current_row, column=5, value=final_price)
        cell_price.alignment = align_right
        cell_price.number_format = '#,##0.00'

        cell_sum = ws.cell(row=current_row, column=6, value=f"=C{current_row}*E{current_row}")
        cell_sum.alignment = align_right
        cell_sum.number_format = '#,##0.00'

        for col_num in range(1, 7):
            c = ws.cell(row=current_row, column=col_num)
            c.font = font_regular
            c.border = thin_border
            
        current_row += 1

    # Строка ИТОГО
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=5)
    total_label = ws.cell(row=current_row, column=1, value="ИТОГО К ОПЛАТЕ:")
    total_label.font = font_bold
    total_label.alignment = align_right

    total_sum = ws.cell(row=current_row, column=6, value=f"=SUM(F5:F{current_row-1})" if current_row > 5 else 0.0)
    total_sum.font = font_bold
    total_sum.alignment = align_right
    total_sum.number_format = '#,##0.00'

    for col in range(1, 7):
        ws.cell(row=current_row, column=col).border = thin_border

    vat_row = current_row + 1
    ws.merge_cells(start_row=vat_row, start_column=1, end_row=vat_row, end_column=5)
    ws.cell(row=vat_row, column=1, value="В том числе НДС (22%):").font = font_bold
    vat_formula = f"=ROUND(F{current_row}*22/122, 2)" if current_row > 5 else "=0.00"
    ws.cell(row=vat_row, column=6, value=vat_formula).number_format = '#,##0.00'

    # Настройка ширины колонок (с запасом под многомиллионные суммы без решёток ###)
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 54
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 20
    ws.column_dimensions["F"].width = 24

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output