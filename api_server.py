from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import json
import glob
import sqlite3
import uuid
from datetime import datetime

app = Flask(__name__)
CORS(app)  # 启用CORS，允许跨域访问

DATA_DIR = "data"
DANMAKU_DB_PATH = os.path.join(DATA_DIR, "danmaku.db")
DANMAKU_MAX_ROWS = 100


def _get_danmaku_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DANMAKU_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_danmaku_db():
    conn = _get_danmaku_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS danmaku (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            wuxia_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_danmaku_created_at ON danmaku(created_at)"
    )
    conn.commit()
    conn.close()


def _danmaku_trim_oldest(conn, cap=DANMAKU_MAX_ROWS):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM danmaku")
    n = cur.fetchone()[0]
    if n > cap:
        cur.execute(
            """
            DELETE FROM danmaku WHERE rowid IN (
                SELECT rowid FROM danmaku ORDER BY created_at ASC LIMIT ?
            )
            """,
            (n - cap,),
        )
        conn.commit()

DEFAULT_BANK_GOLD = [
    {"id": 1, "name": "工商银行积存金", "price": "0.00", "change": 0.0, "changePercent": 0.0, "status": "交易中"},
    {"id": 2, "name": "建设银行积存金", "price": "0.00", "change": 0.0, "changePercent": 0.0, "status": "交易中"},
    {"id": 3, "name": "中国银行积存金", "price": "0.00", "change": 0.0, "changePercent": 0.0, "status": "交易中"},
]

DEFAULT_FUND_LIST = [
    {"id": 1, "name": "黄金ETF联接A", "nav": "0.0000", "yearChange": "0.00%", "dayChange": "0.00%", "date": "[--]"},
    {"id": 2, "name": "黄金ETF联接C", "nav": "0.0000", "yearChange": "0.00%", "dayChange": "0.00%", "date": "[--]"},
]

def get_latest_data(data_type):
    """获取指定类型的最新数据"""
    pattern = os.path.join(DATA_DIR, f"gold_{data_type}_data_*.json")
    files = glob.glob(pattern)
    
    if not files:
        return None
    
    # 按时间戳排序，获取最新文件
    latest_file = sorted(files, key=lambda x: os.path.basename(x), reverse=True)[0]
    
    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"读取文件 {latest_file} 失败: {e}")
        return None


def get_latest_aux_market():
    """读取 crawler/fetch_cngold_market.py 生成的银行/金店/基金 JSON。"""
    pattern = os.path.join(DATA_DIR, "gold_market_aux_*.json")
    files = glob.glob(pattern)
    if not files:
        return None
    latest_file = sorted(files, key=lambda x: os.path.basename(x), reverse=True)[0]
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"读取 {latest_file} 失败: {e}")
        return None


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_percent(value):
    if isinstance(value, str) and value.endswith("%"):
        return value
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _empty_metal(name, unit):
    return {
        "name": name,
        "price": "0.00",
        "change": "0.00",
        "changePercent": "0.00%",
        "unit": unit,
        "updateTime": datetime.now().isoformat(),
        "source": "fallback"
    }


def _normalize_news_item(item, index):
    return {
        "id": item.get("id") or str(index + 1),
        "title": item.get("title", "未命名资讯"),
        "summary": item.get("content", "")[:260],
        "content": item.get("content", ""),
        "url": item.get("url", ""),
        "image": item.get("picUrl", ""),
        "source": item.get("source", "Kitco"),
        "publishTime": item.get("publish_time") or item.get("ctime", ""),
    }


def build_realtime_payload():
    """构建统一实时行情响应，优先读取本地 price JSON。"""
    prices = {
        "gold": _empty_metal("黄金", "美元/盎司"),
        "silver": _empty_metal("白银", "美元/盎司"),
        "platinum": _empty_metal("铂金", "美元/盎司"),
        "palladium": _empty_metal("钯金", "美元/盎司"),
    }

    pattern = os.path.join(DATA_DIR, "gold_price_data_*.json")
    files = sorted(glob.glob(pattern), key=lambda x: os.path.basename(x), reverse=True)

    metal_alias = {
        "gold": "gold",
        "黄金": "gold",
        "xau": "gold",
        "silver": "silver",
        "白银": "silver",
        "xag": "silver",
        "platinum": "platinum",
        "铂金": "platinum",
        "xpt": "platinum",
        "palladium": "palladium",
        "钯金": "palladium",
        "xpd": "palladium",
    }

    found = set()
    for path in files:
        if len(found) == 4:
            break
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        data_node = payload.get("data", {})
        structured = data_node.get("structured_prices", {})
        timestamp = payload.get("timestamp") or datetime.now().isoformat()
        source = payload.get("source", "crawler")
        for raw_name, item in structured.items():
            key = metal_alias.get(str(raw_name).strip().lower())
            if not key or key in found:
                continue
            price = _safe_float(item.get("price"))
            prices[key].update({
                "price": f"{price:.2f}",
                "change": "0.00",
                "changePercent": "0.00%",
                "unit": item.get("unit") or prices[key]["unit"],
                "updateTime": timestamp,
                "source": item.get("source") or source,
            })
            found.add(key)

    gold_price = _safe_float(prices["gold"]["price"])
    bank_gold = []
    for idx, bank_item in enumerate(DEFAULT_BANK_GOLD):
        offset = idx * 0.15
        bank_gold.append({
            **bank_item,
            "price": f"{max(gold_price - offset, 0):.2f}",
            "change": 0.0,
            "changePercent": 0.0,
            "status": "交易中"
        })

    fund_list = [dict(x) for x in DEFAULT_FUND_LIST]
    store_list = []

    aux = get_latest_aux_market()
    if aux:
        if aux.get("bankGoldList"):
            bank_gold = aux["bankGoldList"]
        if aux.get("fundList"):
            fund_list = aux["fundList"]
        store_list = aux.get("storeList") or []
        aux_metal = aux.get("metalPrices") or {}
        metal_alias = {
            "gold": "gold",
            "silver": "silver",
            "platinum": "platinum",
            "palladium": "palladium",
        }
        for raw_key, item in aux_metal.items():
            mk = metal_alias.get(str(raw_key).lower())
            if not mk or not isinstance(item, dict):
                continue
            price = item.get("price")
            if price is not None:
                prices[mk]["price"] = str(price)
            if item.get("change") is not None:
                prices[mk]["change"] = str(item.get("change"))
            if item.get("changePercent") is not None:
                prices[mk]["changePercent"] = str(item.get("changePercent"))
            if item.get("unit"):
                prices[mk]["unit"] = item["unit"]
            prices[mk]["source"] = "cngold_jijinhao"

    domestic_block = {
        "price": prices["gold"]["price"],
        "change": prices["gold"]["change"],
        "changePercent": prices["gold"]["changePercent"],
        "unit": "元/克",
    }
    international_block = {
        "price": prices["gold"]["price"],
        "change": prices["gold"]["change"],
        "changePercent": prices["gold"]["changePercent"],
        "unit": prices["gold"]["unit"],
    }
    if aux and isinstance(aux.get("priceData"), dict):
        aux_pd = aux["priceData"]
        if aux_pd.get("domestic"):
            domestic_block.update(aux_pd["domestic"])
        if aux_pd.get("international"):
            international_block.update(aux_pd["international"])

    return {
        "gold": prices["gold"],
        "silver": prices["silver"],
        "platinum": prices["platinum"],
        "palladium": prices["palladium"],
        "domestic": domestic_block,
        "international": international_block,
        "bankGoldList": bank_gold,
        "fundList": fund_list,
        "storeList": store_list,
        "updateTime": datetime.now().isoformat(),
        "source": "local-data-aggregator"
    }


def build_news_payload(keyword="黄金", page=1, page_size=10):
    """构建统一资讯响应，优先读取本地 news JSON。"""
    data = get_latest_data("news")
    news_articles = []
    if data and data.get("data"):
        news_articles = data["data"].get("news_articles", [])

    normalized = [_normalize_news_item(item, idx) for idx, item in enumerate(news_articles)]
    keyword = (keyword or "").strip()
    if keyword:
        normalized = [item for item in normalized if keyword in item["title"] or keyword in item["summary"]]

    total = len(normalized)
    page = max(page, 1)
    page_size = max(min(page_size, 50), 1)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "list": normalized[start:end],
        "page": page,
        "pageSize": page_size,
        "total": total,
        "keyword": keyword,
        "source": "local-news-aggregator",
        "updateTime": datetime.now().isoformat(),
    }

@app.route('/api/prices', methods=['GET'])
def get_prices():
    """获取最新贵金属价格数据"""
    data = get_latest_data('price')
    if data:
        return jsonify(data)
    return jsonify({"error": "未找到价格数据"}), 404

@app.route('/api/news', methods=['GET'])
def get_news():
    """获取最新新闻资讯"""
    data = get_latest_data('news')
    if data:
        return jsonify(data)
    return jsonify({"error": "未找到新闻数据"}), 404

@app.route('/api/analysis', methods=['GET'])
def get_analysis():
    """获取市场分析数据"""
    data = get_latest_data('analysis')
    if data:
        return jsonify(data)
    return jsonify({"error": "未找到分析数据"}), 404

@app.route('/api/prices/<metal>', methods=['GET'])
def get_metal_price(metal):
    """获取特定金属的价格"""
    # 这里可以根据金属名称过滤数据
    # 目前返回所有价格数据
    data = get_latest_data('price')
    if data:
        return jsonify(data)
    return jsonify({"error": f"未找到{metal}价格数据"}), 404

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "Gold Data API"
    })


@app.route('/api/market/realtime', methods=['GET'])
def market_realtime():
    """统一实时贵金属行情接口。"""
    payload = build_realtime_payload()
    return jsonify({
        "code": 0,
        "msg": "ok",
        "data": payload
    })


@app.route('/api/market/news', methods=['GET'])
def market_news():
    """统一黄金资讯接口。"""
    keyword = request.args.get("keyword", "黄金")
    page = _safe_float(request.args.get("page", 1), 1)
    page_size = _safe_float(request.args.get("pageSize", 10), 10)
    payload = build_news_payload(
        keyword=keyword,
        page=int(page),
        page_size=int(page_size),
    )
    return jsonify({
        "code": 0,
        "msg": "ok",
        "data": payload
    })


@app.route("/api/danmaku", methods=["GET"])
def danmaku_list():
    """返回最多 100 条弹幕，按时间从新到旧。"""
    init_danmaku_db()
    conn = _get_danmaku_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, text, wuxia_name, created_at FROM danmaku
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (DANMAKU_MAX_ROWS,),
    )
    rows = cur.fetchall()
    conn.close()
    items = [
        {
            "id": r["id"],
            "text": r["text"],
            "wuxiaName": r["wuxia_name"] or "",
            "createdAt": r["created_at"],
        }
        for r in rows
    ]
    return jsonify({"code": 0, "msg": "ok", "data": {"list": items}})


@app.route("/api/danmaku", methods=["POST"])
def danmaku_create():
    """保存一条弹幕；库中只保留最新 100 条，超出则删除最老的。"""
    init_danmaku_db()
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"code": 1, "msg": "弹幕内容不能为空"}), 400
    if len(text) > 120:
        text = text[:120]
    wuxia_name = (body.get("wuxiaName") or "").strip()[:32]

    did = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")

    conn = _get_danmaku_conn()
    conn.execute(
        "INSERT INTO danmaku (id, text, wuxia_name, created_at) VALUES (?, ?, ?, ?)",
        (did, text, wuxia_name, now),
    )
    conn.commit()
    _danmaku_trim_oldest(conn)
    conn.close()

    return jsonify(
        {
            "code": 0,
            "msg": "ok",
            "data": {
                "id": did,
                "text": text,
                "wuxiaName": wuxia_name,
                "createdAt": now,
            },
        }
    )


if __name__ == '__main__':
    init_danmaku_db()
    print("启动贵金属数据API服务...")
    print("API接口:")
    print("  GET /api/prices - 获取最新价格数据")
    print("  GET /api/news - 获取最新新闻资讯")
    print("  GET /api/analysis - 获取市场分析数据")
    print("  GET /api/prices/:metal - 获取特定金属价格")
    print("  GET /api/health - 健康检查")
    print("  GET /api/market/realtime - 统一实时行情")
    print("  GET /api/market/news - 统一黄金资讯")
    print("  GET/POST /api/danmaku - 弹幕列表 / 保存弹幕（最多保留100条）")
    print("\n服务启动在 http://localhost:5000")
    
    app.run(host='0.0.0.0', port=5000, debug=True)
