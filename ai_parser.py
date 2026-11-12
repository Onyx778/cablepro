import os
import json
import re
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

client = None
_client_api_key = os.getenv("GEMINI_API_KEY")
if _client_api_key:
    try:
        client = genai.Client(api_key=_client_api_key)
    except Exception:
        client = None
MODEL_NAME = "gemini-2.5-flash"

class ExtractedPosition(BaseModel):
    raw_name: str = Field(description="Строго наименование кабеля, без количества и единиц")
    quantity: float = Field(default=1.0, description="Числовое количество")
    unit: str = Field(default="м", description="Единица измерения кабеля")

class OrderExtraction(BaseModel):
    positions: list[ExtractedPosition]

class AnalogueItem(BaseModel):
    catalog_id: str = Field(description="Артикул товара из каталога")
    reason: str = Field(description="Краткая причина рекомендации")

class AnalogueResult(BaseModel):
    has_analogue: bool
    suggestions: list[AnalogueItem] = Field(default=[])

SYSTEM_PROMPT = """
Ты извлекаешь позиции кабельной продукции из текста, аудио или фото заявки.
Верни JSON с positions. Для каждой позиции укажи марку, количество и единицу.
raw_name — строго только наименование кабеля с числом жил и сечением, без количества заказа и единицы: например «ВВГнг-LS 3х2.5», «СИП-4 4х50», «NYM 3х1.5».
quantity — числовое количество заказа. unit — явно указанную единицу (м, бухт, барабан, км); если единица не указана, используй «м».
Не придумывай характеристики, которых нет в заявке.
"""

class AIParseException(Exception):
    """Понятная ошибка распознавания заявки для вызывающего приложения."""

_PARSE_ERROR = "Не удалось распознать документ. Проверьте качество файла или повторите попытку."

def _clean_response_text(text):
    if not isinstance(text, str):
        return ""
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s*```\s*$", "", text).strip()

def _parse_response(response):
    try:
        if response.parsed:
            return response.parsed.positions
        return OrderExtraction.model_validate_json(_clean_response_text(response.text)).positions
    except Exception as exc:
        raise AIParseException(_PARSE_ERROR) from exc

def _require_ai_client():
    if client is None:
        raise AIParseException("API-ключ не настроен на сервере")
    return client

def parse_text_order(text: str) -> list[ExtractedPosition]:
    try:
        response = _require_ai_client().models.generate_content(
        model=MODEL_NAME,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=OrderExtraction,
            temperature=0.1,
        ),
        )
        return _parse_response(response)
    except AIParseException: raise
    except Exception as exc: raise AIParseException(_PARSE_ERROR) from exc

def parse_image_order(image_bytes: bytes, mime_type: str = "image/jpeg") -> list[ExtractedPosition]:
    try:
        response = _require_ai_client().models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            "Извлеки спецификацию кабельной продукции и проводов с этого фото/документа."
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=OrderExtraction,
            temperature=0.1,
        ),
        )
        return _parse_response(response)
    except AIParseException: raise
    except Exception as exc: raise AIParseException(_PARSE_ERROR) from exc

def parse_voice_order(voice_bytes: bytes, mime_type: str = "audio/webm") -> list[ExtractedPosition]:
    try:
        response = _require_ai_client().models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Part.from_bytes(data=voice_bytes, mime_type=mime_type),
            "Прослушай аудиосообщение и извлеки заказанные марки кабеля, метраж и сечения."
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=OrderExtraction,
            temperature=0.1,
        ),
        )
        return _parse_response(response)
    except AIParseException: raise
    except Exception as exc: raise AIParseException(_PARSE_ERROR) from exc

def find_local_analogues(raw_name: str, catalog: list[dict]) -> list[dict]:
    """Мгновенный локальный поиск аналогов по корням слов и размерам (0.01 сек)"""
    q = raw_name.lower()
    q_words = [w for w in re.findall(r'[а-яa-z]+', q) if len(w) >= 3]
    q_dims = re.findall(r'\d+(?:[\.,]\d+)?', q)
    
    scored = []
    for item in catalog:
        c_name = item["name"].lower()
        c_words = [w for w in re.findall(r'[а-яa-z]+', c_name) if len(w) >= 3]
        
        cat_score = 0
        for qw in q_words:
            for cw in c_words:
                if qw == cw:
                    cat_score += 4
                elif len(qw) >= 4 and len(cw) >= 4 and (qw[:4] == cw[:4]):
                    cat_score += 3
                    
        if cat_score < 3:
            continue
            
        c_dims = re.findall(r'\d+(?:[\.,]\d+)?', c_name)
        reason = "Товар из той же категории на складе"
        if q_dims and c_dims:
            if q_dims[0] in c_dims:
                reason = "Ближайший типоразмер в наличии"
                cat_score += 2

        scored.append((item, cat_score, reason))
        
    scored.sort(key=lambda x: x[1], reverse=True)
    return [{"item": it, "reason": reason} for it, _, reason in scored[:3]]

def find_analogues_with_ai(raw_name: str, catalog: list[dict]) -> list[dict]:
    """
    1. Сначала мгновенно ищет локально (0.01 сек).
    2. Если локально ничего не найдено — быстрый опрос Gemini без задержек.
    """
    local_results = find_local_analogues(raw_name, catalog)
    if local_results:
        return local_results

    _require_ai_client()

    # Быстрый опрос LLM только при необходимости
    compact_catalog = [{"id": x["id"], "name": x["name"], "price": x["price"]} for x in catalog]
    prompt = f"Клиент запросил: «{raw_name}». Найди до 3 близких замен в каталоге:\n{json.dumps(compact_catalog, ensure_ascii=False)}"
    
    try:
        response = _require_ai_client().models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AnalogueResult,
                temperature=0.1,
            ),
        )
        res: AnalogueResult = response.parsed or AnalogueResult.model_validate_json(response.text)
        if not res.has_analogue:
            return []

        cat_map = {x["id"]: x for x in catalog}
        output = []
        for s in res.suggestions:
            if s.catalog_id in cat_map:
                output.append({"item": cat_map[s.catalog_id], "reason": s.reason})
        return output
    except Exception as e:
        print(f"ИИ поиск аналога завершен: {e}")
        return []
