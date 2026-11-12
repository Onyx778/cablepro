import html

def num_to_words_ru(amount: float) -> str:
    """Переводит сумму в рубли прописью по правилам бухгалтерского учета РФ"""
    units = (
        ('', ''), ('один', 'одна'), ('два', 'две'), ('три', 'три'), ('четыре', 'четыре'),
        ('пять', 'пять'), ('шесть', 'шесть'), ('семь', 'семь'), ('восемь', 'восемь'), ('девять', 'девять')
    )
    teens = (
        'десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать',
        'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать'
    )
    tens = ('', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят', 'семьдесят', 'восемьдесят', 'девяносто')
    hundreds = ('', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот', 'шестьсот', 'семьсот', 'восемьсот', 'девятьсот')

    def triplet(n, gender):
        h = n // 100
        t = (n % 100) // 10
        u = n % 10
        words = []
        if h > 0:
            words.append(hundreds[h])
        if t == 1:
            words.append(teens[u])
        else:
            if t > 1:
                words.append(tens[t])
            if u > 0:
                words.append(units[u][gender])
        return ' '.join(words)

    rub = int(amount)
    kop = int(round((amount - rub) * 100))

    if rub == 0:
        rub_str = "ноль рублей"
    else:
        parts = []
        # Миллионы
        m = (rub // 1_000_000) % 1000
        if m > 0:
            form = "миллионов" if (m % 100) in range(11, 20) or m % 10 in [0, 5, 6, 7, 8, 9] else ("миллион" if m % 10 == 1 else "миллиона")
            parts.append(f"{triplet(m, 0)} {form}")
        # Тысячи
        th = (rub // 1_000) % 1000
        if th > 0:
            form = "тысяч" if (th % 100) in range(11, 20) or th % 10 in [0, 5, 6, 7, 8, 9] else ("тысяча" if th % 10 == 1 else "тысячи")
            parts.append(f"{triplet(th, 1)} {form}")
        # Единицы
        un = rub % 1000
        if un > 0 or not parts:
            form = "рублей" if (un % 100) in range(11, 20) or un % 10 in [0, 5, 6, 7, 8, 9] else ("рубль" if un % 10 == 1 else "рубля")
            if un > 0:
                parts.append(f"{triplet(un, 0)} {form}")
            else:
                parts.append(form)

        rub_str = ' '.join(parts).strip()

    return f"{rub_str.capitalize()} {kop:02d} копеек"

def render_invoice_html(order: dict) -> str:
    order_id = order["order_id"]
    created_at = order.get("created_at", "")
    client_name = html.escape(order.get("client_name") or "ООО «СтройГрупп»")
    discount = float(order.get("discount_percent", 0.0))
    items = order["items"]
    
    confirmed = [it for it in items if it.get("selected_item")]
    
    table_rows = []
    total_raw = 0.0
    for idx, it in enumerate(confirmed, start=1):
        prod = it["selected_item"]
        qty = float(it["qty"])
        base_price = float(prod["price"])
        unit_price = round(base_price * (1 - discount / 100), 2)
        row_sum = round(qty * unit_price, 2)
        total_raw += row_sum
        
        table_rows.append(f"""
        <tr>
          <td class="text-center">{idx}</td>
          <td>{html.escape(prod['name'])}</td>
          <td class="text-center">{qty:g}</td>
          <td class="text-center">{html.escape(it.get('unit', 'шт'))}</td>
          <td class="text-right">{unit_price:,.2f}</td>
          <td class="text-right">{row_sum:,.2f}</td>
        </tr>
        """)

    vat_sum = round(total_raw * 20 / 120, 2)
    amount_in_words = num_to_words_ru(total_raw)
    items_count = len(confirmed)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Счет на оплату № {order_id}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      font-size: 11pt;
      color: #000;
      background: #e2e8f0;
      margin: 0;
      padding: 20px;
    }}
    .sheet {{
      background: #fff;
      max-width: 800px;
      margin: 0 auto;
      padding: 40px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.15);
      border-radius: 4px;
    }}
    .no-print-bar {{
      max-width: 800px;
      margin: 0 auto 15px auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #18181b;
      color: #fff;
      padding: 12px 20px;
      border-radius: 8px;
    }}
    .btn {{
      background: #2563eb;
      color: #fff;
      border: none;
      padding: 8px 16px;
      border-radius: 6px;
      font-weight: bold;
      font-size: 13px;
      cursor: pointer;
    }}
    .btn:hover {{ background: #1d4ed8; }}
    table.bank-table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 20px;
      font-size: 9.5pt;
    }}
    table.bank-table td {{
      border: 1px solid #000;
      padding: 4px 6px;
      vertical-align: top;
    }}
    h1.title {{
      font-size: 15pt;
      font-weight: bold;
      margin: 15px 0 20px 0;
      border-bottom: 2px solid #000;
      padding-bottom: 8px;
    }}
    .requisites {{
      margin-bottom: 15px;
      line-height: 1.4;
      font-size: 10pt;
    }}
    table.items-table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 15px;
      font-size: 10pt;
    }}
    table.items-table th, table.items-table td {{
      border: 1px solid #000;
      padding: 6px;
    }}
    table.items-table th {{
      background-color: #f3f4f6;
    }}
    .text-center {{ text-align: center; }}
    .text-right {{ text-align: right; }}
    .totals-block {{
      float: right;
      width: 320px;
      margin-bottom: 20px;
      font-size: 10.5pt;
    }}
    .totals-block div {{
      display: flex;
      justify-content: space-between;
      padding: 3px 0;
    }}
    .clear {{ clear: both; }}
    .words-block {{
      font-size: 10pt;
      margin: 15px 0 30px 0;
      line-height: 1.5;
    }}
    .signatures {{
      margin-top: 40px;
      display: flex;
      justify-content: space-between;
      font-size: 10pt;
      position: relative;
    }}
    .sig-line {{
      border-bottom: 1px solid #000;
      width: 140px;
      display: inline-block;
      margin: 0 5px;
    }}
    .stamp-badge {{
      position: absolute;
      right: 80px;
      top: -20px;
      width: 110px;
      height: 110px;
      border: 2px dashed #1e40af;
      border-radius: 50%;
      color: #1e40af;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      font-size: 8.5pt;
      font-weight: bold;
      transform: rotate(-8deg);
      opacity: 0.85;
    }}
    @media print {{
      body {{ background: #fff; padding: 0; }}
      .no-print-bar {{ display: none; }}
      .sheet {{ box-shadow: none; padding: 0; max-width: 100%; }}
    }}
  </style>
</head>
<body>

  <div class="no-print-bar">
    <div>
      <b>Счет на оплату № {order_id}</b> (готов к печати)
    </div>
    <div style="display: flex; gap: 10px;">
      <button class="btn" onclick="window.print()">🖨 Распечатать / Сохранить в PDF</button>
      <button class="btn" style="background: #3f3f46;" onclick="window.close()">Закрыть</button>
    </div>
  </div>

  <div class="sheet">
    <table class="bank-table">
      <tr>
        <td colspan="2" rowspan="2" style="width: 60%;">
          ПАО СБЕРБАНК Г. МОСКВА<br>
          <small style="color:#555;">Банк получателя</small>
        </td>
        <td style="width: 15%;">БИК</td>
        <td style="width: 25%;">044525225</td>
      </tr>
      <tr>
        <td>Сч. №</td>
        <td>30101810400000000225</td>
      </tr>
      <tr>
        <td style="width: 30%;">ИНН 7701984512</td>
        <td style="width: 30%;">КПП 770101001</td>
        <td rowspan="2">Сч. №</td>
        <td rowspan="2">40702810938000054321</td>
      </tr>
      <tr>
        <td colspan="2">
          ООО "ТД СТРОЙСНАБ ОПТ"<br>
          <small style="color:#555;">Получатель</small>
        </td>
      </tr>
    </table>

    <h1 class="title">Счет на оплату № {order_id} от {created_at} г.</h1>

    <div class="requisites">
      <b>Поставщик:</b> ООО "ТД СТРОЙСНАБ ОПТ", ИНН 7701984512, КПП 770101001, 115280, г. Москва, ул. Ленинская Слобода, д. 19, тел.: +7 (495) 789-00-11<br>
      <b>Покупатель:</b> {client_name}
    </div>

    <table class="items-table">
      <thead>
        <tr>
          <th style="width: 5%;">№</th>
          <th style="width: 52%;">Товары (работы, услуги)</th>
          <th style="width: 10%;">Кол-во</th>
          <th style="width: 8%;">Ед.</th>
          <th style="width: 12%;">Цена, ₽</th>
          <th style="width: 13%;">Сумма, ₽</th>
        </tr>
      </thead>
      <tbody>
        {''.join(table_rows)}
      </tbody>
    </table>

    <div class="totals-block">
      <div><span>Итого:</span> <b>{total_raw:,.2f} ₽</b></div>
      <div><span>В том числе НДС (20%):</span> <b>{vat_sum:,.2f} ₽</b></div>
      <div style="font-size: 11.5pt; margin-top: 5px; border-top: 1px solid #000; padding-top: 4px;">
        <span>Всего к оплате:</span> <b>{total_raw:,.2f} ₽</b>
      </div>
    </div>
    <div class="clear"></div>

    <div class="words-block">
      Всего наименований {items_count}, на сумму {total_raw:,.2f} руб.<br>
      <b>{amount_in_words}</b>
    </div>

    <div class="signatures">
      <div>Руководитель <span class="sig-line"></span> / Смирнов А. В. /</div>
      <div>Бухгалтер <span class="sig-line"></span> / Васильева Е. И. /</div>
      <div class="stamp-badge">
        М.П.<br>ООО "ТД СТРОЙСНАБ"<br>ОГРН 1187746
      </div>
    </div>
  </div>

</body>
</html>
"""