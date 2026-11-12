import json
import re
from pathlib import Path

CATALOG = []
_LATIN = str.maketrans({'v':'в','a':'а','p':'п','c':'с','e':'е','k':'к','h':'н','o':'о','b':'в','t':'т','x':'х'})
_MARKS = ('кгтп-хл', 'кгтп', 'кг-хл', 'кг', 'сип-4', 'сип', 'nym-j', 'nym', 'нум', 'пугв', 'пув', 'пнсв', 'ввг-пнг-frls', 'ввг-пнг-ls', 'ввгнг-frls', 'ввгнг-ls', 'авбшв', 'вббшв', 'вбшв', 'шввп', 'пвс', 'ввг-пнг', 'ввгнг', 'ввг-п', 'ввг')
_CYRILLIC = str.maketrans({'у':'у','м':'м'})

def _safe_text(value): return value if isinstance(value,str) else ''
def _norm(value):
    s=_safe_text(value).lower().replace('ё','е')
    # Preserve NYM as a canonical token before translating mixed keyboard layouts.
    s=re.sub(r'(?i)n\s*[yу]\s*m', 'nym', s)
    s=s.translate(_LATIN)
    s=re.sub(r'ввг\s*п', 'ввг-п', s)
    s=re.sub(r'ввг-п\s*нг', 'ввг-пнг', s)
    s=re.sub(r'ввгнг\s*\(\s*а\s*\)', 'ввгнг', s)
    s=re.sub(r'ввг-пнг\s*\(\s*а\s*\)', 'ввг-пнг', s)
    s=re.sub(r'\s+',' ',s).strip()
    return s

def _canon_mark(s):
    s=_norm(s).replace(' ','')
    s=s.replace('(а)','').replace('(a)','')
    if s in ('nym','nym-j','нум','нум-j','нум-й'): return 'nym-j'
    if s in ('кг','кг-хл','кгхл','кг-hl'): return 'кг-хл' if s != 'кг' else 'кг'
    if s in ('сип','сип-4'): return 'сип-4'
    for m in ('кгтп-хл','кгтп','пугв','пув','пнсв','ввг-пнг-frls','ввг-пнг-ls','ввгнг-frls','ввгнг-ls','вббшв','вбшв','шввп','пвс','авбшв','ввг-пнг','ввгнг','ввг-п','ввг'):
        if s.startswith(m): return m
    return s

def reload_catalog():
    global CATALOG
    try:
        d=json.loads(Path(__file__).with_name('catalog.json').read_text(encoding='utf-8')); CATALOG=d if isinstance(d,list) else []
    except (OSError,ValueError,TypeError): CATALOG=[]

def parse_cable_query(raw_query: str) -> dict:
    s=_norm(raw_query)
    compound=re.search(r'(?<!\d)(\d+)\s*[хx*]\s*(\d+(?:[.,]\d+)?)\s*\+\s*(\d+)\s*[хx*]\s*(\d+(?:[.,]\d+)?)(?!\d)',s)
    dim=compound or re.search(r'(?<!\d)(\d+)\s*(?:х|на|\*)\s*(\d+(?:[.,]\d+)?)(?!\d)',s)
    mark=None
    for pattern,canon in ((r'кгтп\s*-?\s*хл','кгтп-хл'),(r'кгтп','кгтп'),(r'пугв','пугв'),(r'пув','пув'),(r'пнсв','пнсв'),(r'ввг-пнг(?:\(а\))?-frls','ввг-пнг-frls'),(r'ввг-пнг(?:\(а\))?-ls','ввг-пнг-ls'),(r'ввгнг(?:\(а\))?-frls','ввгнг-frls'),(r'ввгнг(?:\(а\))?-ls','ввгнг-ls'),(r'вббшв','вббшв'),(r'вбшв','вбшв'),(r'шввп','шввп'),(r'пвс','пвс'),(r'авбшв','авбшв'),(r'nym(?:-j)?|нум(?:-j|-й)?','nym-j'),(r'кг(?:\s*-?\s*(?:хл|hl))','кг-хл'),(r'кг','кг'),(r'сип(?:-4)?','сип-4'),(r'ввг-пнг','ввг-пнг'),(r'ввгнг','ввгнг'),(r'ввг-п','ввг-п'),(r'ввг','ввг')):
        if re.search(pattern,s): mark=canon; break
    flat=bool(re.search(r'ввг\s*-?\s*п|плос',s))
    execn='FRLS' if 'frls' in s else ('LS' if re.search(r'ls|лс',s) else ('HF' if 'hf' in s else ('нг' if 'нг' in s else None)))
    std='ТУ' if re.search(r'\bту\b',s) else ('ГОСТ' if 'гост' in s else None)
    return {'cores':int(dim.group(1)) if dim else None,'section':float(dim.group(2).replace(',','.')) if dim else None,'extra_cores':int(compound.group(3)) if compound else None,'extra_section':float(compound.group(4).replace(',','.')) if compound else None,'mark':mark,'execution':execn,'is_flat':flat,'standard':std}

def match_item(raw_name: str) -> dict:
    raw=_safe_text(raw_name); q=parse_cable_query(raw)
    base={'status':'NOT_FOUND','matched_item':None,'candidates':[],'raw_name':raw}
    if q['cores'] is None or q['section'] is None or q['mark'] is None:return base
    rows=[i for i in CATALOG if isinstance(i,dict) and i.get('cores')==q['cores'] and isinstance(i.get('section'),(int,float)) and i['section']==q['section']]
    eligible=[]
    for i in rows:
        im=_canon_mark(i.get('mark','')); compatible=im==q['mark']
        if q['mark']=='кг' and im=='кг-хл': compatible=True
        if q['mark']=='кгтп-хл' and im=='кг-хл': compatible=True
        if q['mark']=='сип-4' and im=='сип-4': compatible=True
        if q['mark']=='ввг-пнг-ls' and im=='ввг-пнг-ls': compatible=True
        if q['mark']=='ввгнг-ls' and im=='ввгнг-ls': compatible=True
        if q['mark']=='ввгнг-frls' and im=='ввгнг-frls': compatible=True
        if q['mark']=='nym-j' and im=='nym-j': compatible=True
        if q['mark']=='авбшв' and im=='авбшв': compatible=True
        if q['mark'] in ('ввг-пнг','ввгнг','ввг-п','ввг'): compatible=im.startswith(q['mark'])
        if compatible: eligible.append(i)
    if not eligible:return base
    exact=[]; candidates=[]
    for i in eligible:
        im=_canon_mark(i.get('mark','')); same_shape=bool(i.get('is_flat'))==q['is_flat']
        mark_exact=im==q['mark']
        exec_exact=q['execution'] is None or (('frls' in im and q['execution']=='FRLS') or ('ls' in im and q['execution']=='LS') or (q['execution']=='нг' and 'нг' in im) or (q['execution']=='HF' and 'hf' in im))
        if mark_exact and same_shape and exec_exact and (q['standard'] is None or i.get('standard')==q['standard']):
            if q['standard']=='ТУ' and any(j is not i and j.get('standard')=='ГОСТ' and _canon_mark(j.get('mark',''))==im for j in eligible):
                candidates.append({'item':i,'reason':'Отличается стандарт (ГОСТ / ТУ)'})
            else: exact.append(i)
        else:
            if q['mark']=='кг' and im=='кг-хл': reason='Предложен хладостойкий аналог КГ-ХЛ'
            elif q['mark']=='кгтп-хл' and im=='кг-хл': reason='Предложен кабель в резиновой изоляции КГ-ХЛ вместо термоэластопласта'
            elif q['standard'] and i.get('standard')!=q['standard']: reason='Отличается стандарт (ГОСТ / ТУ)'
            elif q['is_flat'] and not i.get('is_flat') and im.startswith('ввг'): reason='Предложен круглый кабель вместо плоского'
            elif q['mark']=='ввгнг-ls' and im=='nym-j': reason='Функциональный аналог по европейскому стандарту'
            elif q['mark'] in ('кг','сип-4'): reason='Функциональный аналог'
            else: reason='Совпадают жилы и сечение; отличается форма или исполнение'
            candidates.append({'item':i,'reason':reason})
    if exact:return {**base,'status':'EXACT','matched_item':exact[0]}
    if candidates:return {**base,'status':'AMBIGUOUS','candidates':candidates[:3]}
    return base

reload_catalog()

if __name__ == "__main__":
    test_cases = [
        ("Кабель ВВГнг(А)-LS 3х2.5 (ГОСТ)", "EXACT"),
        ("ввгнг-ls 3х2.5", "EXACT"),
        ("ВВГ-Пнг(А)-LS 3х1.5", "EXACT"),
        ("ввгп нг-ls 3*1,5", "EXACT"),
        ("NYM 3х1.5", "EXACT"),
        ("nym-j 3x2.5", "EXACT"),
        ("нум 3х2.5", "EXACT"),
        ("КГ 3х2.5", "AMBIGUOUS"),
        ("ВВГнг-LS 3х2.5 ТУ", "AMBIGUOUS"),
        ("ВВГнг-LS 5х16", "EXACT"),
        ("СИП 4х50", "EXACT"),
        ("Автоматический выключатель 16А", "NOT_FOUND"),
        ("3х2.5", "NOT_FOUND"),
    ]
    for text, expected in test_cases:
        res = match_item(text)
        status = res["status"]
        assert status == expected, f"FAIL on '{text}': got {status}, expected {expected}"
    print("ALL TESTS PASSED SUCCESSFULLY!")
