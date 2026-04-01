import json
import os
import re

import requests
from bs4 import BeautifulSoup

from api_server import build_realtime_payload


def _extract_publish_time(text: str) -> str:
  """从文本中粗略提取发布时间（例如 2026年3月31日 或 03-31 14:02 等）。"""
  if not text:
    return ""
  # 日期+时间
  m = re.search(r"(20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2})", text)
  if m:
    return m.group(1)
  # 仅日期
  m = re.search(r"(20\d{2}年\d{1,2}月\d{1,2}日)", text)
  if m:
    return m.group(1)
  # 月-日 时:分
  m = re.search(r"(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})", text)
  if m:
    return m.group(1)
  return ""


def _extract_article_content(detail_soup: BeautifulSoup) -> (str, str):
  """从金投网文章详情页中提取正文和发布时间。"""
  content = ""

  # 常见正文容器选择器（根据金投网结构做宽松匹配）
  selectors = [
    {"tag": ["div", "article", "section"], "class": re.compile(r"(article|content|main|body|text)", re.I)},
    {"tag": ["div"], "id": re.compile(r"(article|content|main|text)", re.I)},
  ]

  content_blocks = []
  for sel in selectors:
    if "class" in sel:
      blocks = detail_soup.find_all(sel["tag"], class_=sel["class"])
    else:
      blocks = detail_soup.find_all(sel["tag"], id=sel["id"])
    if blocks:
      content_blocks = blocks
      break

  if not content_blocks:
    # 降级：取整页所有 p 段落
    paragraphs = detail_soup.find_all("p")
  else:
    paragraphs = []
    for block in content_blocks:
      paragraphs.extend(block.find_all("p"))
      if paragraphs:
        break

  lines = []
  for p in paragraphs:
    text = p.get_text(strip=True)
    if not text:
      continue
    # 过滤导航/版权等明显无关内容
    if len(text) < 8:
      continue
    lines.append(text)

  content = "\n\n".join(lines)

  # 发布时间：从包含“来源”、“时间”等字段的元素里提取
  publish_time = ""
  time_candidates = detail_soup.find_all(
    ["span", "div", "p"],
    class_=re.compile(r"(time|date|source|info)", re.I),
  )
  texts = [el.get_text(" ", strip=True) for el in time_candidates]
  # 也把整个 body 文本纳入检测，防止漏掉
  texts.append(detail_soup.get_text(" ", strip=True))
  for t in texts:
    publish_time = _extract_publish_time(t)
    if publish_time:
      break

  return content, publish_time


def fetch_cngold_news(max_items: int = 20):
  """从金投网抓取黄金相关资讯列表 + 详情，仅用于生成前端静态数据。"""
  url = "https://www.cngold.org/"
  headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.baidu.com/",
  }

  try:
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
  except Exception as e:
    print(f"抓取金投网首页失败: {e}")
    return {"list": [], "source": "cngold", "updateTime": ""}

  soup = BeautifulSoup(resp.text, "html.parser")

  items = []
  for a in soup.find_all("a"):
    title = a.get_text(" ", strip=True)
    href = a.get("href") or ""
    if not title or len(title) < 8:
      continue

    # 只要和黄金/金价/贵金属相关的标题
    if not any(k in title for k in ["黄金", "金价", "贵金属", "现货金", "伦敦金"]):
      continue

    # 只要站内新闻链接
    if href.startswith("//"):
      href = "https:" + href
    elif href.startswith("/"):
      href = "https://www.cngold.org" + href
    elif not href.startswith("http"):
      href = "https://www.cngold.org/" + href

    if "cngold.org/c/" not in href:
      continue

    items.append((title, href))

  # 去重并裁剪数量
  seen = set()
  clean_list = []
  for title, href in items:
    key = title + "|" + href
    if key in seen:
      continue
    seen.add(key)

    # 抓取详情页
    detail_content = ""
    publish_time = ""
    try:
      detail_resp = requests.get(
        href,
        headers=headers,
        timeout=15,
      )
      detail_resp.raise_for_status()
      detail_resp.encoding = "utf-8"
      detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
      detail_content, publish_time = _extract_article_content(detail_soup)
    except Exception as e:
      print(f"抓取金投网文章详情失败: {href} {e}")
      detail_content = ""
      publish_time = ""

    # 过滤掉没有有效正文的条目（如登录/注册提示页）
    invalid_markers = [
      "还没有帐号？免费注册一个吧",
      "还没有账号？注册一个吧",
      "我的金投",
      "登录后才能查看全文",
    ]
    if not detail_content or len(detail_content) < 50 or any(m in detail_content for m in invalid_markers):
      continue

    clean_list.append(
      {
        "title": title,
        "url": href,
        "summary": detail_content.split("\n\n")[0] if detail_content else "",
        "content": detail_content,
        "publishTime": publish_time,
        "source": "金投网",
      }
    )
    if len(clean_list) >= max_items:
      break

  return {
    "list": clean_list,
    "source": "cngold",
    "updateTime": "",
  }


def main():
  """生成前端直接 require 的静态行情与资讯 JS 模块。"""
  root_dir = os.path.dirname(os.path.abspath(__file__))
  data_dir = os.path.join(root_dir, "data")
  os.makedirs(data_dir, exist_ok=True)

  # 行情数据（仍然使用聚合逻辑）
  realtime = build_realtime_payload()
  latest_market_js = os.path.join(data_dir, "latest_market.js")
  with open(latest_market_js, "w", encoding="utf-8") as f:
    f.write("module.exports = ")
    json.dump(realtime, f, ensure_ascii=False, indent=2)
    f.write(";\n")

  # 黄金资讯数据（只用金投网）
  news = fetch_cngold_news(max_items=20)
  latest_news_js = os.path.join(data_dir, "latest_news.js")
  with open(latest_news_js, "w", encoding="utf-8") as f:
    f.write("module.exports = ")
    json.dump(news, f, ensure_ascii=False, indent=2)
    f.write(";\n")

  print(f"静态行情已生成: {latest_market_js}")
  print(f"静态资讯已生成(来源: 金投网): {latest_news_js}")


if __name__ == "__main__":
  main()

