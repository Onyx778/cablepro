import os
import io
import random
import json
import asyncio
from dotenv import load_dotenv
from openpyxl import load_workbook
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, 
    CallbackQuery, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    BufferedInputFile
)

import ai_parser
import matcher
import excel_generator

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в файле .env!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Временное хранилище заказов {order_id: {...}}
ORDERS = {}

def build_order_ui(order_id: int):
    """Формирует текст карточки заказа и инлайн-кнопки"""
    order = ORDERS[order_id]
    items = order["items"]
    
    text_lines = [f"📋 <b>Заказ #{order_id}</b>\n"]
    inline_buttons = []
    
    total_sum = 0

    for idx, it in enumerate(items, start=1):
        status = it["status"]
        qty = it["qty"]
        unit = it["unit"]
        
        if status == "EXACT":
            prod = it["selected_item"]
            item_sum = prod["price"] * qty
            total_sum += item_sum
            text_lines.append(
                f"🟢 <b>{idx}. {prod['name']}</b>\n"
                f"   ↳ {qty:g} {unit} × {prod['price']} ₽ = <b>{item_sum:,.0f} ₽</b>"
            )
            
        elif status == "AMBIGUOUS":
            text_lines.append(
                f"🟡 <b>{idx}. Запрос: «{it['raw_name']}» ({qty:g} {unit})</b>\n"
                f"   ↳ <i>Уточните позицию:</i>"
            )
            for c_idx, cand in enumerate(it["candidates"]):
                c_item = cand["item"]
                btn_text = f"👉 {idx}. {c_item['name'][:30]}... ({c_item['price']} ₽)"
                inline_buttons.append([
                    InlineKeyboardButton(
                        text=btn_text, 
                        callback_data=f"sel:{order_id}:{idx-1}:{c_idx}"
                    )
                ])
                
        elif status == "NOT_FOUND":
            text_lines.append(
                f"🔴 <b>{idx}. «{it['raw_name']}» ({qty:g} {unit})</b>\n"
                f"   ↳ <i>Позиция не найдена в каталоге</i>"
            )
        
        text_lines.append("")

    text_lines.append(f"💰 <b>Итого (по подтвержденным): {total_sum:,.0f} ₽</b>")
    
    action_row = [
        InlineKeyboardButton(text="✅ Сформировать КП", callback_data=f"done:{order_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel:{order_id}")
    ]
    inline_buttons.append(action_row)

    return "\n".join(text_lines), InlineKeyboardMarkup(inline_keyboard=inline_buttons)

async def handle_order_processing(message: Message, extracted_positions):
    """Сверяет позиции с каталогом и отправляет карточку менеджеру"""
    if not extracted_positions:
        await message.answer("⚠️ Не удалось выделить позиции стройматериалов. Попробуйте еще раз.")
        return

    order_id = random.randint(1000, 9999)
    items = []

    for pos in extracted_positions:
        match_res = matcher.match_item(pos.raw_name)
        status = match_res["status"]
        
        item_entry = {
            "raw_name": pos.raw_name,
            "qty": pos.quantity,
            "unit": pos.unit,
            "status": status,
            "selected_item": match_res.get("matched_item"),
            "candidates": match_res.get("candidates", [])
        }
        items.append(item_entry)

    ORDERS[order_id] = {
        "order_id": order_id,
        "items": items
    }

    card_text, keyboard = build_order_ui(order_id)
    await message.answer(card_text, parse_mode="HTML", reply_markup=keyboard)

# ----------------- ХЭНДЛЕРЫ ВХОДЯЩИХ -----------------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>ИИ-ассистент менеджера по оптовым продажам готов!</b>\n\n"
        "<b>Как отправить заявку:</b>\n"
        "• 🎙 Голосовое сообщение от прораба\n"
        "• 📸 Фотографию рукописного списка\n"
        "• 💬 Неструктурированный текст из мессенджера\n\n"
        "<b>Обновление каталога:</b>\n"
        "• Просто прикрепите и отправьте Excel-файл (.xlsx) с прайс-листом.",
        parse_mode="HTML"
    )

@dp.message(F.document)
async def process_document(message: Message):
    """Прием и разбор Excel-файла с новым каталогом"""
    doc = message.document
    if not doc.file_name.lower().endswith(".xlsx"):
        await message.answer("⚠️ Пожалуйста, отправьте файл прайс-листа в формате Excel (.xlsx)")
        return

    wait_msg = await message.answer("⏳ <i>Считываю Excel и обновляю номенклатуру...</i>", parse_mode="HTML")
    
    try:
        file_io = await bot.download(doc)
        file_bytes = file_io.getvalue()
        
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        
        # 1. Ищем строку заголовков
        header_row_idx = None
        col_map = {}
        
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            row_str = [str(c).lower().strip() if c is not None else "" for c in row]
            for c_idx, val in enumerate(row_str):
                if any(w in val for w in ["наименование", "номенклатура", "товар", "название"]):
                    col_map["name"] = c_idx
                elif any(w in val for w in ["цена", "стоимость", "прайс"]):
                    col_map["price"] = c_idx
                elif any(w in val for w in ["артикул", "код", "арт"]):
                    col_map["id"] = c_idx
                elif any(w in val for w in ["ед", "изм", "базовая единица"]):
                    col_map["unit"] = c_idx

            if "name" in col_map and "price" in col_map:
                header_row_idx = r_idx
                break

        if not header_row_idx:
            header_row_idx = 1
            col_map = {"id": 0, "name": 1, "unit": 2, "price": 3}

        # 2. Собираем товары
        new_catalog = []
        rows = list(ws.iter_rows(values_only=True))[header_row_idx:]
        
        for idx, row in enumerate(rows, start=1):
            if not row or len(row) <= max(col_map.values()):
                continue
                
            name_val = row[col_map["name"]]
            if not name_val or str(name_val).strip() == "":
                continue
                
            price_val = row[col_map["price"]]
            try:
                price = float(str(price_val).replace(" ", "").replace(",", "."))
            except (ValueError, TypeError):
                price = 0.0

            art_val = row[col_map.get("id", 0)] if "id" in col_map else f"ART-{idx:03d}"
            unit_val = row[col_map.get("unit", 2)] if "unit" in col_map else "шт"

            new_catalog.append({
                "id": str(art_val or f"ART-{idx:03d}").strip(),
                "name": str(name_val).strip(),
                "unit": str(unit_val or "шт").strip(),
                "price": price
            })

        if not new_catalog:
            await wait_msg.edit_text("❌ Не удалось найти товары в таблице. Проверьте формат.")
            return

        # 3. Сохраняем в catalog.json и перезагружаем память
        with open("catalog.json", "w", encoding="utf-8") as f:
            json.dump(new_catalog, f, ensure_ascii=False, indent=2)

        matcher.reload_catalog()

        await wait_msg.edit_text(
            f"✅ <b>Каталог успешно обновлен!</b>\n\n"
            f"📦 Загружено позиций: <b>{len(new_catalog)}</b>\n"
            f"Новые цены и товары уже активны для поиска.",
            parse_mode="HTML"
        )

    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка при чтении Excel: {e}")

@dp.message(F.text)
async def process_text(message: Message):
    wait_msg = await message.answer("⏳ <i>Разбираю текст заявки...</i>", parse_mode="HTML")
    try:
        positions = ai_parser.parse_text_order(message.text)
        await wait_msg.delete()
        await handle_order_processing(message, positions)
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка разбора: {e}")

@dp.message(F.photo)
async def process_photo(message: Message):
    wait_msg = await message.answer("⏳ <i>Распознаю фото через Vision...</i>", parse_mode="HTML")
    try:
        photo = message.photo[-1]
        file_io = await bot.download(photo)
        image_bytes = file_io.getvalue()
        
        positions = ai_parser.parse_image_order(image_bytes)
        await wait_msg.delete()
        await handle_order_processing(message, positions)
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка распознавания фото: {e}")

@dp.message(F.voice)
async def process_voice(message: Message):
    wait_msg = await message.answer("⏳ <i>Слушаю голосовое сообщение...</i>", parse_mode="HTML")
    try:
        voice = message.voice
        file_io = await bot.download(voice)
        voice_bytes = file_io.getvalue()
        
        positions = ai_parser.parse_voice_order(voice_bytes)
        await wait_msg.delete()
        await handle_order_processing(message, positions)
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка обработки аудио: {e}")

# ----------------- КОЛБЭКИ КНОПОК -----------------

@dp.callback_query(F.data.startswith("sel:"))
async def cb_select_item(callback: CallbackQuery):
    _, order_id_s, item_idx_s, cand_idx_s = callback.data.split(":")
    order_id = int(order_id_s)
    item_idx = int(item_idx_s)
    cand_idx = int(cand_idx_s)

    order = ORDERS.get(order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    item = order["items"][item_idx]
    chosen_product = item["candidates"][cand_idx]["item"]
    
    item["selected_item"] = chosen_product
    item["status"] = "EXACT"

    card_text, keyboard = build_order_ui(order_id)
    await callback.message.edit_text(card_text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer("Вариант выбран!")

@dp.callback_query(F.data.startswith("cancel:"))
async def cb_cancel_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    ORDERS.pop(order_id, None)
    await callback.message.edit_text(f"❌ <b>Заказ #{order_id} отменен.</b>", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("done:"))
async def cb_done_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    order = ORDERS.get(order_id)
    
    if not order:
        await callback.answer("Заказ не найден!", show_alert=True)
        return

    confirmed_items = [it for it in order["items"] if it.get("selected_item")]
    if not confirmed_items:
        await callback.answer("Нет подтвержденных позиций для КП!", show_alert=True)
        return

    export_payload = {
        "order_id": order_id,
        "source": "telegram_bot",
        "positions": [
            {
                "article": it["selected_item"]["id"],
                "name": it["selected_item"]["name"],
                "quantity": it["qty"],
                "price": it["selected_item"]["price"],
                "sum": it["qty"] * it["selected_item"]["price"]
            }
            for it in confirmed_items
        ]
    }
    print("\n" + "="*50)
    print(f"🚀 [1C / Склад API ПЕЙЛОАД] Экспорт заказа #{order_id}:")
    print(json.dumps(export_payload, ensure_ascii=False, indent=2))
    print("="*50 + "\n")

    excel_stream = excel_generator.generate_excel_quote(order_id, confirmed_items)
    file_bytes = excel_stream.getvalue()
    
    doc = BufferedInputFile(file_bytes, filename=f"Спецификация_Заказ_{order_id}.xlsx")
    
    await callback.message.answer_document(
        document=doc,
        caption=f"📄 Готово! Спецификация к <b>Заказу #{order_id}</b> сформирована.\n"
                f"Все подтвержденные позиции выгружены.",
        parse_mode="HTML"
    )
    await callback.answer("КП успешно сформировано!")

# ----------------- ЗАПУСК -----------------

async def main():
    print("🤖 Бот запущен! Ждет сообщений в Telegram...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())