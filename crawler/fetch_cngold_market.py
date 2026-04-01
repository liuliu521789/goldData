"""
抓取金投网银行贵金属页中的银行条块与品牌金店报价（页面内行情码 JO_*，行情接口为 api.jijinhao.com）。
黄金基金：金投基金「黄金」分类 URL 会跳首页，无稳定列表接口，故联接基金净值使用东方财富公开 pingzhongdata + 移动端净值接口。

用法: python crawler/fetch_cngold_market.py
输出:
  - data/gold_market_aux_<timestamp>.json（备份）
  - static/market_bundle.js（小程序 require，无需后端）
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

import requests

BANK_GOLD_URL = "https://www.cngold.org/img_date/bank_gold.html"
JIJIN_QUOTE_URL = "https://api.jijinhao.com/history/quotejs.htm"
HEADERS_PAGE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://www.cngold.org/",
}
HEADERS_QUOTE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://www.cngold.org/",
}

# 与小程序黄金基金页名称一致的联接 C 类（东方财富基金代码）
GOLD_LINK_FUNDS = [
    ("014661", "天弘上海金ETF联接C"),
    ("009504", "富国上海金ETF联接C"),
    ("008987", "广发上海金ETF联接C"),
    ("000217", "华安黄金ETF联接C"),
    ("002611", "博时黄金ETF联接C"),
    ("007976", "易方达黄金ETF联接C"),
    ("004253", "国泰黄金ETF联接C"),
    ("002963", "前海开源黄金ETF联接C"),
]

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data")
STATIC_DIR = os.path.join(ROOT_DIR, "static")
CHUNK = 18

# 金投网首页/纸黄金页使用的 jijinhao 行情码（与页面 title 一致）
METAL_USD_SPECS = [
    ("gold", "JO_42757", "黄金", "美元/盎司"),
    ("silver", "JO_42758", "白银", "美元/盎司"),
    ("platinum", "JO_42759", "铂金", "美元/盎司"),
    ("palladium", "JO_52643", "钯金", "美元/盎司"),
]
# 国内 Hero「元/克」：上金所黄金 T+D（AuT+D）。勿用 JO_92233（XAU 现货）——其 q63 与国际金同量级，会误显示为「元/克」。
DOMESTIC_GOLD_CODE = "JO_9753"


def _parse_quot_str(text: str) -> list:
    m = re.search(r"var\s+quot_str\s*=\s*(\[.*\])\s*;?", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def _float_str(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fetch_page_html() -> str:
    r = requests.get(BANK_GOLD_URL, headers=HEADERS_PAGE, timeout=25)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def _table_kind(header_cells: list[str]) -> str | None:
    if not header_cells:
        return None
    c0 = header_cells[0]
    # 表0 首列为「银行」实为上交所行情表，勿与「银行/机构」贵金属报价表混淆
    if c0 == "银行/机构" or ("银行" in c0 and "机构" in c0):
        return "bank"
    if c0 in ("品牌", "金店"):
        return "jewelry"
    return None


def _extract_rows(html: str) -> tuple[list[dict], list[dict]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    bank_rows: list[dict] = []
    jewelry_rows: list[dict] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        kind = _table_kind(header_cells)
        if kind not in ("bank", "jewelry"):
            continue

        for tr in rows[1:]:
            tds = tr.find_all("td")
            if not tds:
                continue
            m = re.search(r"JO_\d+", str(tr))
            if not m:
                continue
            code = m.group(0)
            cells = [td.get_text(strip=True) for td in tds]
            if kind == "bank":
                bank = cells[0] if len(cells) > 0 else ""
                product = cells[1] if len(cells) > 1 else ""
                label = f"{bank} {product}".strip() or product or bank
                bank_rows.append({"code": code, "name": label, "kind": "bank"})
            else:
                brand = cells[0] if len(cells) > 0 else ""
                product = cells[1] if len(cells) > 1 else ""
                label = f"{brand} {product}".strip() or product or brand
                jewelry_rows.append({"code": code, "name": label, "kind": "jewelry"})

    def dedupe(seq: list[dict]) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for item in seq:
            c = item["code"]
            if c in seen:
                continue
            seen.add(c)
            out.append(item)
        return out

    return dedupe(bank_rows), dedupe(jewelry_rows)


def _fetch_quotes_chunk(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    params = {
        "codes": ",".join(codes),
        "currentPage": 1,
        "pageSize": len(codes),
    }
    r = requests.get(JIJIN_QUOTE_URL, params=params, headers=HEADERS_QUOTE, timeout=25)
    r.raise_for_status()
    parsed = _parse_quot_str(r.text)
    if not parsed:
        return {}
    data = parsed[0].get("data") or []
    out: dict[str, dict] = {}
    for block in data:
        q = (block or {}).get("quote") or {}
        code = q.get("q124")
        if code and code not in out:
            out[code] = q
    return out


def _quote_to_bank_store_item(
    name: str, q: dict, *, is_store: bool
) -> dict | None:
    price = _float_str(q.get("q2"))
    chg = _float_str(q.get("q70"))
    pct = _float_str(q.get("q80"))
    if is_store and (price < 200 or price > 6000):
        return None
    if not is_store and (price < 1 or price > 20000):
        return None

    price_s = f"{price:.2f}元/克"
    raw_q59 = (q.get("q59") or "").strip()
    quote_day = raw_q59[:10] if len(raw_q59) >= 10 else ""

    if is_store:
        # q59 为数据源标注的行情日，常为非交易日时停留在上一日，与「今天抓取时间」无关
        return {
            "name": name,
            "price": price_s,
            "date": quote_day or "--",
            "type": "brand",
        }

    return {
        "name": name,
        "price": price_s,
        "change": round(chg, 2),
        "changePercent": round(pct, 2),
        "status": "交易中",
        "quoteDate": quote_day or "",
    }


def _fmt_price2(v: float) -> str:
    return f"{v:.2f}"


def _fmt_change_pct(pct: float) -> str:
    return f"{pct:.2f}%"


def _quote_to_mini_metal(name: str, unit: str, q: dict) -> dict:
    price = _float_str(q.get("q63") or q.get("q2"))
    chg = _float_str(q.get("q70"))
    pct = _float_str(q.get("q80"))
    return {
        "name": name,
        "price": _fmt_price2(price),
        "change": _fmt_price2(chg),
        "changePercent": _fmt_change_pct(pct),
        "unit": unit,
    }


def _quote_to_price_block(q: dict, unit: str) -> dict:
    price = _float_str(q.get("q63") or q.get("q2"))
    chg = _float_str(q.get("q70"))
    pct = _float_str(q.get("q80"))
    return {
        "price": _fmt_price2(price),
        "change": _fmt_price2(chg),
        "changePercent": _fmt_change_pct(pct),
        "unit": unit,
    }


def _q59_date_str(q: dict) -> str:
    raw = (q.get("q59") or "").strip()
    return raw[:10] if len(raw) >= 10 else ""


def fetch_au9999_recent_history(limit: int = 10, *, exclude_today: bool = True) -> list[dict]:
    """从金投网 jijinhao 拉取 AU9999(黄金T+D)近 N 个交易日收盘价。"""
    page_size = max(limit + 5, 16)
    params = {
        "codes": DOMESTIC_GOLD_CODE,
        "currentPage": 1,
        "pageSize": page_size,
    }
    r = requests.get(JIJIN_QUOTE_URL, params=params, headers=HEADERS_QUOTE, timeout=25)
    r.raise_for_status()
    parsed = _parse_quot_str(r.text)
    if not parsed:
        return []
    data = (parsed[0] or {}).get("data") or []
    today = datetime.now().strftime("%Y-%m-%d")
    rows: list[dict] = []
    for block in data:
        q = (block or {}).get("quote") or {}
        d = _q59_date_str(q)
        if not d:
            continue
        if exclude_today and d == today:
            continue
        price = _float_str(q.get("q2") or q.get("q63"))
        if price < 100 or price > 3000:
            continue
        rows.append({"date": d, "price": round(price, 2)})
        if len(rows) >= limit:
            break
    rows.sort(key=lambda x: x["date"])
    return rows


def fetch_cngold_precious_spot(quotes: dict[str, dict]) -> dict:
    """从已拉取的 jijinhao quote 映射中组装首页贵金属卡片与国内外金价。"""
    metal_prices: dict[str, dict] = {}
    for key, code, cname, unit in METAL_USD_SPECS:
        q = quotes.get(code)
        if not q:
            continue
        metal_prices[key] = _quote_to_mini_metal(cname, unit, q)

    domestic = None
    intl = None
    q_au = quotes.get(DOMESTIC_GOLD_CODE)
    q_usd = quotes.get("JO_42757")
    if q_au:
        domestic = _quote_to_price_block(q_au, "元/克")
    if q_usd:
        intl = _quote_to_price_block(q_usd, "美元/盎司")

    dates = []
    for _, code, _, _ in METAL_USD_SPECS:
        qq = quotes.get(code)
        if qq:
            d = _q59_date_str(qq)
            if d:
                dates.append(d)
    if q_au:
        d = _q59_date_str(q_au)
        if d:
            dates.append(d)

    update_day = max(dates) if dates else ""

    price_data = {}
    if domestic:
        price_data["domestic"] = domestic
    if intl:
        price_data["international"] = intl
    if domestic:
        price_data["au9999"] = dict(domestic)

    return {
        "metalPrices": metal_prices,
        "priceData": price_data,
        "metalSpotUpdateTime": update_day,
    }


def _fetch_fund_row(fcode: str, fname: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    ping = requests.get(
        f"https://fund.eastmoney.com/pingzhongdata/{fcode}.js",
        headers=headers,
        timeout=20,
    )
    ping.raise_for_status()
    js = ping.text
    m_y = re.search(r'syl_1y="([0-9.-]+)"', js)
    year_pct = m_y.group(1) if m_y else "--"

    nav_r = requests.get(
        "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList",
        params={
            "FCODE": fcode,
            "pageIndex": 1,
            "pageSize": 1,
            "deviceid": "Wap",
            "plat": "Wap",
            "product": "EFund",
            "Version": "6.3.9",
        },
        headers=headers,
        timeout=20,
    )
    nav_r.raise_for_status()
    j = nav_r.json()
    rows = j.get("Datas") or []
    nav = "--"
    day_pct = "--"
    nav_date = "--"
    if rows:
        row = rows[0]
        nav = str(row.get("DWJZ") or "--")
        dz = row.get("JZZZL")
        if dz is not None and dz != "" and dz != "--":
            try:
                day_pct = f"{float(dz):.2f}%"
            except ValueError:
                day_pct = str(dz)
        fs = row.get("FSRQ") or ""
        if len(fs) >= 10:
            nav_date = fs[:10]

    year_disp = f"{year_pct}%" if year_pct != "--" else "--"
    return {
        "name": fname,
        "nav": nav,
        "yearChange": year_disp,
        "dayChange": day_pct if day_pct != "--" else "0.00%",
        # FSRQ 为净值所属交易日，通常晚于自然日一天（T+1 披露），故常与「今天」不一致
        "date": nav_date,
    }


def run() -> dict:
    html = _fetch_page_html()
    bank_specs, jewelry_specs = _extract_rows(html)
    all_codes = [x["code"] for x in bank_specs] + [x["code"] for x in jewelry_specs]
    metal_codes = list({c for _, c, _, _ in METAL_USD_SPECS} | {DOMESTIC_GOLD_CODE})
    for c in metal_codes:
        if c not in all_codes:
            all_codes.append(c)
    quotes: dict[str, dict] = {}
    for i in range(0, len(all_codes), CHUNK):
        chunk = all_codes[i : i + CHUNK]
        quotes.update(_fetch_quotes_chunk(chunk))
        time.sleep(0.35)

    spot_bundle = fetch_cngold_precious_spot(quotes)
    try:
        au9999_history = fetch_au9999_recent_history(limit=10, exclude_today=True)
    except Exception:
        au9999_history = []

    bank_gold_list: list[dict] = []
    for i, spec in enumerate(bank_specs):
        q = quotes.get(spec["code"])
        if not q:
            continue
        item = _quote_to_bank_store_item(spec["name"], q, is_store=False)
        if item:
            item["id"] = i + 1
            bank_gold_list.append(item)

    store_list: list[dict] = []
    for i, spec in enumerate(jewelry_specs):
        q = quotes.get(spec["code"])
        if not q:
            continue
        item = _quote_to_bank_store_item(spec["name"], q, is_store=True)
        if item:
            item["id"] = i + 1
            store_list.append(item)

    fund_list: list[dict] = []
    for i, (fcode, fname) in enumerate(GOLD_LINK_FUNDS):
        try:
            row = _fetch_fund_row(fcode, fname)
            row["id"] = i + 1
            fund_list.append(row)
        except Exception:
            fund_list.append(
                {
                    "id": i + 1,
                    "name": fname,
                    "nav": "--",
                    "yearChange": "--",
                    "dayChange": "--",
                    "date": "--",
                }
            )
        time.sleep(0.12)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "timestamp": datetime.now().isoformat(),
        "source": "cngold_bank_page+jijinhao_quote+eastmoney_fund_nav+cngold_precious_spot",
        "bankGoldList": bank_gold_list,
        "storeList": store_list,
        "fundList": fund_list,
        "metalPrices": spot_bundle.get("metalPrices") or {},
        "priceData": spot_bundle.get("priceData") or {},
        "metalSpotUpdateTime": spot_bundle.get("metalSpotUpdateTime") or "",
        "au9999History": au9999_history,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, f"gold_market_aux_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("written", out_path)

    os.makedirs(STATIC_DIR, exist_ok=True)
    bundle_path = os.path.join(STATIC_DIR, "market_bundle.js")
    bundle_obj = {
        "updateTime": payload["timestamp"],
        "source": payload["source"],
        "bankGoldList": payload["bankGoldList"],
        "storeList": payload["storeList"],
        "fundList": payload["fundList"],
        "metalPrices": payload["metalPrices"],
        "priceData": payload["priceData"],
        "metalSpotUpdateTime": payload["metalSpotUpdateTime"],
        "au9999History": payload["au9999History"],
    }
    with open(bundle_path, "w", encoding="utf-8") as f:
        f.write("module.exports = ")
        f.write(json.dumps(bundle_obj, ensure_ascii=False, indent=2))
        f.write(";\n")
    print("written", bundle_path)
    return payload


if __name__ == "__main__":
    run()
