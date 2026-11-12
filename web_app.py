import os
import io
import json
import csv
import re
import random
import hashlib
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from openpyxl import load_workbook
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request, Response, Header
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

import ai_parser
import matcher
import excel_generator
import database
import invoice_generator
import excel_reader
from typing import Optional, Literal

load_dotenv()

app = FastAPI(title="AI Procurement Suite")
DEMO_USER = {"username": "manager", "full_name": "Менеджер снабжения", "company": "ООО «КабельПро»", "is_admin": True}

# Хранилище сессий в памяти {session_token: user_dict}
ACTIVE_SESSIONS = {}

class ParsedAttributes(BaseModel):
    mark: Optional[str] = None
    cores: Optional[int] = None
    section: Optional[float] = None
    standard: Optional[str] = None

class SuggestedMatch(BaseModel):
    catalog_code: Optional[str] = None
    stock_name: Optional[str] = None
    match_type: Literal["EXACT", "ANALOG", "NOT_FOUND"]
    confidence: float = Field(ge=0.0, le=1.0)
    price: float = 0.0
    note: Optional[str] = None

class ParsedItemResponse(BaseModel):
    line_number: int
    raw_string: str
    parsed: ParsedAttributes
    quantity: float
    unit: str
    nmc_unit_price: Optional[float] = None
    suggested_match: Optional[SuggestedMatch] = None

class ParseResponse(BaseModel):
    status: Literal["success", "error"]
    total_items: int
    items: list[ParsedItemResponse]

async def verify_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    configured={v.strip() for v in os.getenv("PARTNER_API_KEYS", "").split(",") if v.strip()}
    valid=configured or {"kabelpro_partner_demo_2026"}
    if not x_api_key or x_api_key not in valid:
        raise HTTPException(status_code=401,detail="Неверный или отсутствующий X-API-Key")

def get_current_user(request: Request):
    return DEMO_USER

def require_admin(user: dict = Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return user

@app.post("/api/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    user = database.verify_user_login(username, password)
    if user:
        # Генерируем уникальный сессионный токен
        token_data = f"{user['username']}_{datetime.now().timestamp()}_{random.random()}"
        session_token = hashlib.sha256(token_data.encode("utf-8")).hexdigest()
        
        ACTIVE_SESSIONS[session_token] = user
        response.set_cookie(
            key="auth_session", 
            value=session_token, 
            httponly=True, 
            max_age=86400 * 7,
            samesite="lax"
        )
        return {
            "ok": True, 
            "username": user["username"], 
            "full_name": user["full_name"],
            "is_admin": bool(user["is_admin"])
        }
    raise HTTPException(status_code=401, detail="Неверный логин или пароль")

@app.get("/api/logout")
async def logout(response: Response, request: Request):
    token = request.cookies.get("auth_session")
    if token in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS.pop(token, None)
    response.delete_cookie(key="auth_session")
    return {"ok": True}

@app.get("/api/check-auth")
async def check_auth_endpoint(request: Request):
    token = request.cookies.get("auth_session")
    if token and token in ACTIVE_SESSIONS:
        u = ACTIVE_SESSIONS[token]
        return {
            "authenticated": True, 
            "username": u["username"], 
            "full_name": u["full_name"],
            "is_admin": bool(u["is_admin"])
        }
    return {"authenticated": False}

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()

# ----------------- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (АДМИН) -----------------

class CreateUserPayload(BaseModel):
    username: str
    password: str
    full_name: str

@app.get("/api/admin/users")
async def get_users(_ = Depends(require_admin)):
    return database.list_all_users()

@app.post("/api/admin/users")
async def add_user(payload: CreateUserPayload, _ = Depends(require_admin)):
    ok, msg = database.create_user(payload.username, payload.password, payload.full_name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}

@app.delete("/api/admin/users/{user_id}")
async def remove_user(user_id: int, _ = Depends(require_admin)):
    database.delete_user_by_id(user_id)
    return {"ok": True}

# ----------------- ОБРАБОТКА ЗАЯВОК -----------------

def process_extracted_positions(extracted_positions, user_name: str):
    order_id = int(datetime.now().strftime("%y%m%d%H%M%S"))
    items = []

    for pos in extracted_positions:
        raw_name = getattr(pos, "raw_name", "") or ""
        try:
            match_res = matcher.match_item(raw_name)
            status = match_res.get("status", "NOT_FOUND")
            if status == "NOT_FOUND": status = "unrecognized"
            selected = match_res.get("matched_item")
            candidates = match_res.get("candidates", [])
        except Exception:
            status, selected, candidates = "unrecognized", None, []
        try: qty = float(getattr(pos, "quantity", 0.0) or 0.0)
        except (TypeError, ValueError): qty = 0.0
        unit = (getattr(pos, "unit", None) or "м").strip()
        unit_key = unit.casefold().rstrip(".")
        if unit_key in {"км", "километр", "километра", "километров", "километры"}:
            qty *= 1000.0
            unit = "м"
        elif unit_key in {"", "бухта", "бухты", "бухт", "моток", "мотка"}:
            unit = "м"
        items.append({"raw_name": raw_name, "qty": qty, "unit": unit, "status": status,
                      "selected_item": selected, "candidates": candidates,
                      "price": float(selected.get("price", 0.0)) if isinstance(selected, dict) else 0.0})

    order_data = {
        "order_id": order_id,
        "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "client_name": user_name or DEMO_USER["company"],
        "discount_percent": 0.0,
        "items": items
    }
    
    database.save_order(order_data)
    return order_data

@app.post("/api/process/text")
async def handle_text(text: str = Form(...), user: dict = Depends(get_current_user)):
    try:
        positions = await asyncio.to_thread(ai_parser.parse_text_order, text)
        return process_extracted_positions(positions, user["full_name"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка обработки: {str(e)}")

@app.post("/api/process/image")
async def handle_image(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    try:
        content = await file.read()
        mime_type = file.content_type or "image/jpeg"
        positions = await asyncio.to_thread(ai_parser.parse_image_order, content, mime_type)
        return process_extracted_positions(positions, user["full_name"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка обработки: {str(e)}")

@app.post("/api/process/voice")
async def handle_voice(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    try:
        content = await file.read()
        mime_type = file.content_type or "audio/webm"
        positions = await asyncio.to_thread(ai_parser.parse_voice_order, content, mime_type)
        return process_extracted_positions(positions, user["full_name"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка обработки: {str(e)}")

def extract_tender_positions(rows):
    def norm(v): return str(v or "").strip().lower().replace("ё", "е")
    header_idx = name_col = qty_col = unit_col = nmc_col = None
    for r_idx, row in enumerate(rows):
        cells=[norm(c) for c in row]
        if any(any(key in cell for key in ("наименование", "продукция", "товар")) for cell in cells) and any(any(key in cell for key in ("количество", "кол-во", "кол.", "колво")) for cell in cells):
            header_idx=r_idx
            for c_idx, cell in enumerate(cells):
                if name_col is None and any(k in cell for k in ("наименование", "продукция", "товар")): name_col=c_idx
                if qty_col is None and any(k in cell for k in ("количество", "кол-во", "кол.", "колво")): qty_col=c_idx
                if any(k in cell for k in ("ед.", "ед изм", "единица")): unit_col=c_idx
                if "нмц" in cell or "начальная максимальная" in cell: nmc_col=c_idx
            break
    if header_idx is None or name_col is None or qty_col is None:
        raise ValueError("Не найдена таблица товаров с колонками наименования и количества")
    positions=[]
    for row in rows[header_idx+1:]:
        name=str(row[name_col]).strip() if name_col<len(row) else ""
        if not name or len(name)<4: continue
        try:
            float(name.replace(",", "."))
            continue
        except ValueError:
            pass
        try: qty=float(str(row[qty_col]).replace(" ","").replace(",","."))
        except (ValueError,TypeError): continue
        unit=str(row[unit_col]).strip() if unit_col is not None and unit_col<len(row) and row[unit_col] else "м"
        nmc=None
        # EIS forms place unit NMC per metre in column K (zero-based index 10).
        nmc_index = 10 if len(row) > 10 else nmc_col
        if nmc_index is not None and nmc_index<len(row):
            try: nmc=float(str(row[nmc_index]).replace(" ","").replace(",","."))
            except (ValueError,TypeError): pass
        positions.append({"raw_name":name,"quantity":qty,"unit":unit,"nmc_price":nmc})
    return positions

@app.post("/api/process/excel")
async def handle_excel(file: UploadFile=File(...), user: dict=Depends(get_current_user)):
    try:
        content=await file.read()
        filename=file.filename or "upload.xlsx"
        if filename.lower().endswith(".csv"):
            decoded=None
            for encoding in ("utf-8-sig","utf-8","cp1251"):
                try:
                    decoded=content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if decoded is None:
                raise ValueError("Не удалось определить кодировку CSV (поддерживаются UTF-8 и CP1251)")
            sample=decoded[:8192]
            try: dialect=csv.Sniffer().sniff(sample, delimiters=",;")
            except csv.Error: dialect=csv.excel
            rows=list(csv.reader(io.StringIO(decoded),dialect))
        else:
            rows=excel_reader.read_sheet_rows(content,filename)
        extracted=extract_tender_positions(rows)
        order=process_extracted_positions([type("Position",(),p) for p in extracted],user["full_name"])
        for item, source in zip(order["items"],extracted): item["nmc_price"]=source.get("nmc_price")
        database.save_order(order)
        return order
    except Exception as e:
        raise HTTPException(status_code=400,detail=f"Ошибка обработки Excel: {e}")

def _partner_items(records):
    result=[]
    for idx, record in enumerate(records,1):
        raw=str(record.get("raw_name") or "").strip()
        if not raw: continue
        attrs=matcher.parse_cable_query(raw)
        match=matcher.match_item(raw)
        product=match.get("matched_item")
        note=None
        if match.get("status")=="AMBIGUOUS":
            candidate=(match.get("candidates") or [{}])[0]
            product=candidate.get("item") if isinstance(candidate,dict) else None
            note=candidate.get("reason") if isinstance(candidate,dict) else None
            match_type="ANALOG" if product else "NOT_FOUND"
        elif match.get("status")=="EXACT":
            match_type="EXACT"
        else:
            match_type="NOT_FOUND"
        suggested=SuggestedMatch(catalog_code=str(product.get("id")) if product else None,
            stock_name=product.get("name") if product else None,match_type=match_type,
            confidence=1.0 if match_type=="EXACT" else (0.75 if match_type=="ANALOG" else 0.0),
            price=float(product.get("price",0) or 0) if product else 0.0,note=note)
        unit=str(record.get("unit") or "м").strip()
        result.append(ParsedItemResponse(line_number=idx,raw_string=raw,
            parsed=ParsedAttributes(mark=attrs.get("mark"),cores=attrs.get("cores"),section=attrs.get("section"),standard=attrs.get("standard")),
            quantity=float(record.get("quantity",0) or 0),unit=unit,
            nmc_unit_price=record.get("nmc_price"),suggested_match=suggested))
    return ParseResponse(status="success",total_items=len(result),items=result)

def _parse_partner_text(raw_text):
    records=[]
    for line in raw_text.splitlines():
        line=line.strip()
        if not line: continue
        m=re.match(r"^(.*?)(?:\\s+[-–—:]?\\s*)(\\d+(?:[.,]\\d+)?)\\s*(км|километр(?:а|ов)?|м|шт|бухт(?:а|ы)?)?\\s*$",line,re.I)
        if m and m.group(1).strip():
            qty=float(m.group(2).replace(",","."));unit=(m.group(3) or "м").lower()
            name=m.group(1).strip()
            if unit.startswith("километр") or unit=="км":qty*=1000;unit="м"
            records.append({"raw_name":name,"quantity":qty,"unit":unit})
        else:
            records.append({"raw_name":line,"quantity":1.0,"unit":"м"})
    return records

PARTNER_1C_EXAMPLE = '''Пример интеграции в 1С:Предприятие 8.3:\n```bsl\n// Пример интеграции в 1С:Предприятие 8.3\nСервер = "kabelpro-api.onrender.com";\nРесурс = "/api/v1/parse";\nHTTPСоединение = Новый HTTPСоединение(Сервер, 443,,,, 30, Новый ЗащищенноеСоединениеOpenSSL());\nHTTPЗапрос = Новый HTTPЗапрос(Ресурс);\nHTTPЗапрос.Заголовки.Вставить("X-API-Key", "kabelpro_partner_demo_2026");\n// Файл передается multipart/form-data; JSON-ответ разбирается ЧтениеJSON.\n```'''

@app.post("/api/v1/parse", response_model=ParseResponse,
          summary="Разобрать файл или текст сметы для 1С",
          description="Принимает Excel/CSV, TXT, PDF/сканы или сырой текст; возвращает структуру для загрузки в 1С.\n\n"+PARTNER_1C_EXAMPLE)
async def partner_parse(request: Request, file: Optional[UploadFile]=File(None), raw_text: Optional[str]=Form(None), _=Depends(verify_api_key)):
    records=[]
    try:
        if file is None and raw_text is None:
            if "application/json" in request.headers.get("content-type",""):
                payload=await request.json();raw_text=payload.get("raw_text") if isinstance(payload,dict) else None
        if file is not None:
            filename=(file.filename or "").lower();content=await file.read()
            if filename.endswith((".xlsx",".xls",".csv")):
                if filename.endswith(".csv"):
                    decoded=None
                    for enc in ("utf-8-sig","utf-8","cp1251"):
                        try:decoded=content.decode(enc);break
                        except UnicodeDecodeError:pass
                    if decoded is None:raise ValueError("Не удалось прочитать CSV")
                    try:dialect=csv.Sniffer().sniff(decoded[:8192],delimiters=",;")
                    except csv.Error:dialect=csv.excel
                    rows=list(csv.reader(io.StringIO(decoded),dialect))
                else: rows=excel_reader.read_sheet_rows(content,filename)
                records=extract_tender_positions(rows)
            elif filename.endswith(".txt"):
                try: text=content.decode("utf-8-sig")
                except UnicodeDecodeError:text=content.decode("cp1251")
                records=_parse_partner_text(text)
            elif filename.endswith(".pdf") or filename.endswith((".png",".jpg",".jpeg",".webp")):
                mime=file.content_type or ("application/pdf" if filename.endswith(".pdf") else "image/jpeg")
                parsed=await asyncio.to_thread(ai_parser.parse_image_order,content,mime)
                records=[{"raw_name":x.raw_name,"quantity":x.quantity,"unit":x.unit} for x in parsed]
            else: raise ValueError("Формат файла не поддерживается")
        elif raw_text:
            records=_parse_partner_text(raw_text)
        else:
            raise ValueError("Передайте файл или raw_text")
        return _partner_items(records)
    except HTTPException: raise
    except Exception as exc:
        raise HTTPException(status_code=400,detail=f"Ошибка разбора: {exc}") from exc

# ----------------- ЗАКАЗЫ -----------------

@app.get("/api/orders")
async def get_orders_list(_ = Depends(get_current_user)):
    return database.list_recent_orders()

@app.get("/api/orders/{order_id}")
async def get_single_order(order_id: int, _ = Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return order

class UpdateQtyPayload(BaseModel):
    item_idx: int
    new_qty: float

@app.post("/api/orders/{order_id}/update-qty")
async def update_qty(order_id: int, payload: UpdateQtyPayload, _ = Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order or payload.item_idx >= len(order["items"]):
        raise HTTPException(status_code=404, detail="Позиция не найдена")
        
    order["items"][payload.item_idx]["qty"] = max(float(payload.new_qty or 0.0), 0.0)
    database.save_order(order)
    return order

class DeleteItemPayload(BaseModel):
    item_idx: int

@app.post("/api/orders/{order_id}/delete-item")
async def delete_item(order_id: int, payload: DeleteItemPayload, _ = Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order or payload.item_idx >= len(order["items"]):
        raise HTTPException(status_code=404, detail="Позиция не найдена")
        
    order["items"].pop(payload.item_idx)
    database.save_order(order)
    return order

class AddItemPayload(BaseModel):
    product_id: str
    qty: float = 1.0

@app.post("/api/orders/{order_id}/add-item")
async def add_item_from_catalog(order_id: int, payload: AddItemPayload, _ = Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
        
    prod = next((p for p in matcher.CATALOG if p["id"] == payload.product_id), None)
    if not prod:
        raise HTTPException(status_code=404, detail="Товар не найден в каталоге")

    order["items"].append({
        "raw_name": prod["name"],
        "qty": payload.qty,
        "unit": prod.get("unit", "шт"),
        "status": "EXACT",
        "selected_item": prod,
        "candidates": []
    })
    database.save_order(order)
    return order

class UpdateDiscountPayload(BaseModel):
    discount_percent: float

@app.post("/api/orders/{order_id}/discount")
async def update_discount(order_id: int, payload: UpdateDiscountPayload, _ = Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
        
    order["discount_percent"] = max(min(payload.discount_percent, 90.0), 0.0)
    database.save_order(order)
    return order

class UpdateOrderItemPayload(BaseModel):
    quantity: float | None = None
    price: float | None = None
    candidate_idx: int | None = None
    product_id: str | None = None

class DemoPositionPayload(BaseModel):
    raw_name: str
    quantity: float = 0.0
    unit: str = "м"

class DemoOrderPayload(BaseModel):
    positions: list[DemoPositionPayload]

@app.post("/api/order/demo")
async def create_demo_order(payload: DemoOrderPayload, _=Depends(get_current_user)):
    positions=[type("Position", (), {"raw_name":p.raw_name,"quantity":p.quantity,"unit":p.unit}) for p in payload.positions]
    return process_extracted_positions(positions, DEMO_USER["company"])

@app.post("/api/order/{order_id}/item/{item_idx}")
async def update_order_item(order_id: int, item_idx: int, payload: UpdateOrderItemPayload, _=Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order or not 0 <= item_idx < len(order["items"]): raise HTTPException(status_code=404, detail="Позиция не найдена")
    item = order["items"][item_idx]
    if payload.quantity is not None: item["qty"] = float(payload.quantity)
    if payload.price is not None: item["price"] = float(payload.price)
    product = None
    if payload.product_id: product = next((p for p in matcher.CATALOG if p.get("id")==payload.product_id), None)
    elif payload.candidate_idx is not None:
        cands=item.get("candidates",[])
        if not 0 <= payload.candidate_idx < len(cands): raise HTTPException(status_code=400, detail="Кандидат не найден")
        product=cands[payload.candidate_idx].get("item")
    if payload.product_id and product is None: raise HTTPException(status_code=404, detail="Товар не найден")
    if product:
        was_candidate = payload.candidate_idx is not None
        item["selected_item"]=product; item["price"]=float(product.get("price",0.0) or 0.0); item["unit"]=product.get("unit",item.get("unit","м")); item["status"]="ANALOG_SELECTED" if was_candidate else "EXACT"
        item["candidates"]=[]
    database.save_order(order)
    return order

class UpdateClientPayload(BaseModel):
    client_name: str

@app.post("/api/orders/{order_id}/client")
async def update_client(order_id: int, payload: UpdateClientPayload, _ = Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
        
    order["client_name"] = payload.client_name.strip() or "ООО «СтройГрупп»"
    database.save_order(order)
    return order

class SelectVariantPayload(BaseModel):
    order_id: int
    item_idx: int
    candidate_idx: int

@app.post("/api/select-variant")
async def select_variant(payload: SelectVariantPayload, _ = Depends(get_current_user)):
    order = database.get_order(payload.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    item = order["items"][payload.item_idx]
    chosen = item["candidates"][payload.candidate_idx]["item"]
    item["selected_item"] = chosen
    item["status"] = "EXACT"

    database.save_order(order)
    return order

class ReplaceItemPayload(BaseModel):
    order_id: int
    item_idx: int
    product_id: str

@app.post("/api/replace-item")
async def replace_item(payload: ReplaceItemPayload, _ = Depends(get_current_user)):
    order = database.get_order(payload.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    product = next((p for p in matcher.CATALOG if p["id"] == payload.product_id), None)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")

    item = order["items"][payload.item_idx]
    item["selected_item"] = product
    item["status"] = "EXACT"
    item["candidates"] = []

    database.save_order(order)
    return order

class SuggestAnaloguePayload(BaseModel):
    raw_name: str

@app.post("/api/suggest-analogue")
async def suggest_analogue(payload: SuggestAnaloguePayload, _ = Depends(get_current_user)):
    analogues = await asyncio.to_thread(ai_parser.find_analogues_with_ai, payload.raw_name, matcher.CATALOG)
    return {"analogues": analogues}

@app.get("/api/search-catalog")
async def search_catalog(q: str = "", _ = Depends(get_current_user)):
    query = q.lower().strip()
    if not query:
        return matcher.CATALOG[:15]
    results = [item for item in matcher.CATALOG if query in item["name"].lower() or query in item["id"].lower()]
    return results[:10]

@app.get("/invoice/{order_id}", response_class=HTMLResponse)
async def view_invoice(order_id: int, _ = Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return invoice_generator.render_invoice_html(order)

@app.get("/api/download-excel/{order_id}")
async def download_excel(order_id: int, _ = Depends(get_current_user)):
    order = database.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    confirmed_items = [it for it in order["items"] if it.get("selected_item")]
    if not confirmed_items:
        raise HTTPException(status_code=400, detail="Нет подтвержденных позиций")

    excel_stream = excel_generator.generate_excel_quote(
        order_id, 
        confirmed_items, 
        client_name=order.get("client_name", "ООО «СтройГрупп»"),
        discount_percent=order.get("discount_percent", 0.0)
    )
    
    headers = {
        'Content-Disposition': f'attachment; filename="Specification_Order_{order_id}.xlsx"'
    }
    return StreamingResponse(
        excel_stream, 
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
        headers=headers
    )

@app.get("/api/order/{order_id}/excel")
async def download_order_excel(order_id: int, _=Depends(get_current_user)):
    order=database.get_order(order_id)
    if not order: raise HTTPException(status_code=404, detail="Заказ не найден")
    stream=excel_generator.generate_excel_quote(order_id, order.get("items",[]), order.get("client_name",DEMO_USER["company"]), order.get("discount_percent",0.0))
    return StreamingResponse(stream,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="KP_KabelPro_{order_id}.xlsx"'})

@app.get("/api/catalog-info")
async def catalog_info(_ = Depends(get_current_user)):
    return {"count": len(matcher.CATALOG)}

@app.get("/api/catalog")
async def get_catalog(_ = Depends(get_current_user)):
    return matcher.CATALOG

@app.post("/api/upload-catalog")
async def upload_catalog(file: UploadFile = File(...), _ = Depends(get_current_user)):
    try:
        content = await file.read()
        wb = load_workbook(io.BytesIO(content), data_only=True)
        ws = wb.active

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

        with open("catalog.json", "w", encoding="utf-8") as f:
            json.dump(new_catalog, f, ensure_ascii=False, indent=2)

        matcher.reload_catalog()
        return {"ok": True, "count": len(new_catalog)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка Excel: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print("\n" + "="*50)
    print("🚀 AI PROCUREMENT SUITE запущен!")
    print("👤 Логин админа: admin")
    print("🔑 Пароль админа: admin12345")
    print(f"👉 Терминал: http://127.0.0.1:{port}")
