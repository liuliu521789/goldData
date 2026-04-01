import requests
from bs4 import BeautifulSoup
import json
import os
import re
from datetime import datetime
import urllib.parse
import schedule
import time
import threading
import hashlib

def extract_sge_data(soup):
    """提取上海黄金交易所数据"""
    structured_data = {}
    
    # 获取页面所有文本
    all_text = soup.get_text()
    
    # 提取所有数字
    all_numbers = re.findall(r'(\d{1,5}\.\d{2})', all_text)
    
    # 查找合理的黄金价格（350-550元/克）
    gold_prices = []
    for num in all_numbers:
        try:
            price_float = float(num)
            if 350 <= price_float <= 550:
                gold_prices.append(num)
        except ValueError:
            continue
    
    # 如果找到合理价格，取第一个
    if gold_prices:
        structured_data["黄金"] = {
            "price": gold_prices[0],
            "unit": "元/克",
            "exchange": "上海黄金交易所"
        }
    
    return structured_data

def extract_boc_data(soup):
    """提取中国银行数据"""
    structured_data = {}
    
    # 提取贵金属价格信息
    price_elements = soup.find_all(['div', 'span', 'td'], class_=re.compile(r'price|gold|silver|platinum|palladium', re.I))
    
    for element in price_elements:
        text = element.get_text(strip=True)
        # 查找包含价格的文本
        if re.search(r'(\d{1,5}\.\d{2})', text):
            # 根据文本内容判断贵金属类型
            if '黄金' in text or 'gold' in text.lower():
                structured_data["黄金"] = {
                    "price": re.search(r'(\d{1,5}\.\d{2})', text).group(1),
                    "unit": "元/克",
                    "bank": "中国银行"
                }
            elif '白银' in text or 'silver' in text.lower():
                structured_data["白银"] = {
                    "price": re.search(r'(\d{1,5}\.\d{2})', text).group(1),
                    "unit": "元/克",
                    "bank": "中国银行"
                }
    
    return structured_data

def extract_cngold_data(soup):
    """提取中国黄金网数据"""
    structured_data = {}

    all_text = soup.get_text(" ", strip=True)

    # 优先提取“最新报 xxx美元/盎司”的行情播报数据，避免抓到无关数字
    # 例：现货黄金刚刚突破4560.00美元/盎司关口，最新报4559.69美元/盎司
    market_patterns = {
        "黄金": [
            r"现货黄金[^。；]{0,120}?最新报\s*([0-9]+(?:\.[0-9]+)?)\s*美元/盎司",
            r"伦敦金[^。；]{0,120}?最新报\s*([0-9]+(?:\.[0-9]+)?)\s*美元/盎司",
        ],
        "白银": [
            r"现货白银[^。；]{0,120}?最新报\s*([0-9]+(?:\.[0-9]+)?)\s*美元/盎司",
            r"伦敦银[^。；]{0,120}?最新报\s*([0-9]+(?:\.[0-9]+)?)\s*美元/盎司",
        ],
        "铂金": [
            r"现货铂金[^。；]{0,120}?最新报\s*([0-9]+(?:\.[0-9]+)?)\s*美元/盎司",
        ],
    }

    for metal, patterns in market_patterns.items():
        for pattern in patterns:
            match = re.search(pattern, all_text, re.I)
            if not match:
                continue
            price_value = match.group(1)
            structured_data[metal] = {
                "price": price_value,
                "unit": "美元/盎司",
                "source": "金投网(cngold)"
            }
            break

    # 对未命中“最新报”的品种，再从“行情播报”附近做降级提取
    fallback_patterns = {
        "黄金": r"(?:现货黄金|伦敦金)\s*([0-9]+(?:\.[0-9]+)?)",
        "白银": r"(?:现货白银|伦敦银)\s*([0-9]+(?:\.[0-9]+)?)",
        "铂金": r"现货铂金\s*([0-9]+(?:\.[0-9]+)?)",
    }
    for metal, pattern in fallback_patterns.items():
        if metal in structured_data:
            continue
        match = re.search(pattern, all_text, re.I)
        if not match:
            continue
        structured_data[metal] = {
            "price": match.group(1),
            "unit": "美元/盎司",
            "source": "金投网(cngold)"
        }

    return structured_data

def extract_kitco_data(soup):
    """提取Kitco数据"""
    structured_data = {}
    
    # 判断页面类型（黄金、白银、铂金、钯金）
    title = soup.find('title')
    title_text = title.get_text().lower() if title else ""
    
    metal_type = ""
    if 'gold' in title_text:
        metal_type = "黄金"
    elif 'silver' in title_text:
        metal_type = "白银"
    elif 'platinum' in title_text:
        metal_type = "铂金"
    elif 'palladium' in title_text:
        metal_type = "钯金"
    
    # 提取价格数据
    all_text = soup.get_text()
    price_pattern = r'(\d{1,5}\.\d{2})'
    prices = re.findall(price_pattern, all_text)
    
    if prices and metal_type:
        # Kitco页面包含大量无关数字，按金属类型做合理区间过滤后再取值
        # 这里优先取较大的候选值，避免误取涨跌幅或其他小数
        metal_ranges = {
            "黄金": (1500, 6000),
            "白银": (10, 100),
            "铂金": (500, 2500),
            "钯金": (500, 2500),
        }
        low, high = metal_ranges.get(metal_type, (100, 10000))
        price_candidates = []
        for p in prices:
            try:
                value = float(p)
            except ValueError:
                continue
            if low <= value <= high:
                price_candidates.append(value)

        if price_candidates:
            selected_price = max(price_candidates)
            structured_data[metal_type] = {
                "price": f"{selected_price:.2f}",
                "unit": "美元/盎司",
                "source": "Kitco"
            }
    
    return structured_data

# 百度翻译API配置
BAIDU_TRANSLATE_APP_ID = "20260328002582203"
BAIDU_TRANSLATE_SECRET_KEY = "2yCSbbaFXeAmobuHzRXg"

def baidu_translate(text, from_lang='auto', to_lang='zh'):
    """使用百度翻译API翻译文本"""
    if not text:
        return text
    
    url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    
    # 生成签名
    import random
    salt = str(random.randint(32768, 65536))
    sign_str = BAIDU_TRANSLATE_APP_ID + text + salt + BAIDU_TRANSLATE_SECRET_KEY
    sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()
    
    params = {
        'q': text,
        'from': from_lang,
        'to': to_lang,
        'appid': BAIDU_TRANSLATE_APP_ID,
        'salt': salt,
        'sign': sign
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        result = response.json()
        
        if 'trans_result' in result:
            translated_text = ''
            for item in result['trans_result']:
                translated_text += item['dst']
            return translated_text
        else:
            print(f"百度翻译失败: {result.get('error_msg', '未知错误')}")
            return text
    except Exception as e:
        print(f"百度翻译请求失败: {e}")
        return text

def crawl_price_data():
    """爬取贵金属价格数据（每10分钟）"""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始爬取价格数据...")
    
    # 价格数据源
    price_sources = [
        # 国内数据源
        {
            "type": "web",
            "url": "https://www.sge.com.cn/sjzx/mrhq",
            "source": "上海黄金交易所行情中心"
        },
        {
            "type": "web",
            "url": "https://www.boc.cn/finadata/gold/",
            "source": "中国银行贵金属行情"
        },
        {
            "type": "web",
            "url": "https://gold.hexun.com/",
            "source": "和讯网黄金频道"
        },
        {
            "type": "web",
            "url": "https://www.cngold.org/",
            "source": "中国黄金网"
        },
        # 国外数据源
        {
            "type": "web",
            "url": "https://www.kitco.com/charts/livegold.html",
            "source": "Kitco黄金价格页面"
        },
        {
            "type": "web",
            "url": "https://www.kitco.com/charts/livesilver.html",
            "source": "Kitco白银价格页面"
        },
        {
            "type": "web",
            "url": "https://www.kitco.com/charts/liveplatinum.html",
            "source": "Kitco铂金价格页面"
        },
        {
            "type": "web",
            "url": "https://www.kitco.com/charts/livepalladium.html",
            "source": "Kitco钯金价格页面"
        }
    ]
    
    crawl_sources(price_sources, "price")

def crawl_news_data():
    """爬取新闻资讯数据（每2小时）"""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始爬取新闻资讯...")
    
    # 新闻数据源
    news_sources = [
        # 国内新闻数据源
        {
            "type": "web",
            "url": "https://gold.hexun.com/",
            "source": "和讯网黄金新闻"
        },
        {
            "type": "web",
            "url": "https://www.cngold.org/",
            "source": "中国黄金网新闻"
        },
        # 国外新闻数据源
        {
            "type": "web",
            "url": "https://www.kitco.com/news/",
            "source": "Kitco新闻页面"
        }
    ]
    
    crawl_sources(news_sources, "news")

def crawl_analysis_data():
    """爬取市场分析数据（每6小时）"""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始爬取市场分析...")
    
    # 分析数据源
    analysis_sources = [
        # 国内分析数据源
        {
            "type": "web",
            "url": "https://gold.hexun.com/analysis/",
            "source": "和讯网黄金分析"
        },
        {
            "type": "web",
            "url": "https://www.cngold.org/analysis/",
            "source": "中国黄金网市场分析"
        },
        {
            "type": "web",
            "url": "https://www.sge.com.cn/sjzx/mrgh",
            "source": "上海黄金交易所市场回顾"
        },
        # 国外分析数据源
        {
            "type": "web",
            "url": "https://www.kitco.com/",
            "source": "Kitco首页"
        }
    ]
    
    crawl_sources(analysis_sources, "analysis")

def crawl_sources(sources, data_type):
    """爬取指定数据源"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.baidu.com/",
        "Cache-Control": "max-age=0",
        "TE": "Trailers"
    }
    
    for source_info in sources:
        source_type = source_info["type"]
        url = source_info["url"]
        source = source_info["source"]
        
        print(f"开始从 {source} 获取数据...")
        
        gold_data = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "url": url,
            "data_type": data_type,
            "data": {}
        }
        
        try:
            # 添加随机延迟，避免被封IP
            import random
            delay = random.uniform(1, 3)
            time.sleep(delay)
            
            # 添加重试机制
            max_retries = 3
            retry_count = 0
            
            while retry_count< max_retries:
                try:
                    response = requests.get(url, headers=headers, timeout=30)
                    response.raise_for_status()
                    if "cngold.org" in url:
                        response.encoding = "utf-8"
                    elif not response.encoding:
                        response.encoding = response.apparent_encoding
                    break
                except requests.RequestException as e:
                    retry_count += 1
                    if retry_count < max_retries:
                        print(f"请求失败，{max_retries - retry_count}次重试...")
                        time.sleep(random.uniform(2, 5))
                    else:
                        raise
            
            if source_type == "web":
                soup = BeautifulSoup(response.text, 'html.parser')
                
                title = soup.find('title')
                if title:
                    gold_data["data"]["title"] = title.get_text(strip=True)
                
                # 结构化提取价格数据
                structured_prices = {}
                
                # 根据数据源进行不同的提取策略
                if "sge.com.cn" in url:
                    # 上海黄金交易所数据提取
                    gold_data["data"]["structured_prices"] = extract_sge_data(soup)
                elif "boc.cn" in url:
                    # 中国银行数据提取
                    gold_data["data"]["structured_prices"] = extract_boc_data(soup)
                elif "cngold.org" in url:
                    # 中国黄金网数据提取
                    gold_data["data"]["structured_prices"] = extract_cngold_data(soup)
                elif "kitco.com" in url:
                    # Kitco数据提取
                    gold_data["data"]["structured_prices"] = extract_kitco_data(soup)
                else:
                    # 默认提取策略（保留原有逻辑）
                    all_text = soup.get_text()
                    
                    # 基本价格模式（直接匹配数字）
                    basic_price_pattern = r'(\d{1,5}\.\d{2})'
                    basic_prices = re.findall(basic_price_pattern, all_text)
                    
                    # 带货币单位的价格模式
                    price_patterns = [
                        r'(\d{1,5}\.\d{2})\s*USD',
                        r'(\d{1,5}\.\d{2})\s*US\$',
                        r'(\d{1,5}\.\d{2})\s*/盎司',
                        r'(\d{1,5}\.\d{2})\s*Ounce',
                        r'(\d{1,5}\.\d{2})\s*盎司',
                        r'(\d{1,5}\.\d{2})\s*CNY',
                        r'(\d{1,5}\.\d{2})\s*元',
                        r'(\d{1,5}\.\d{2})\s*/克',
                        r'(\d{1,5}\.\d{2})\s*Gram',
                        r'(\d{1,5}\.\d{2})\s*黄金',
                        r'(\d{1,5}\.\d{2})\s*gold',
                        r'(\d{1,5}\.\d{2})\s*XAU',
                        r'(\d{1,5}\.\d{2})\s*XAG'
                    ]
                    
                    all_prices = []
                    for pattern in price_patterns:
                        matches = re.findall(pattern, all_text)
                        if matches:
                            all_prices.extend(matches)
                    
                    # 如果没有找到带单位的价格，使用基本价格
                    if not all_prices and basic_prices:
                        all_prices = basic_prices
                    
                    if all_prices:
                        gold_data["data"]["prices"] = all_prices
                
                # 提取新闻资讯
                if data_type == "news":
                    news_items = []
                    
                    # 尝试多种方式提取新闻
                    # 1. 查找所有链接元素
                    all_links = soup.find_all('a')
                    for link in all_links:
                        title = link.get_text(strip=True)
                        if title and len(title) > 15:
                            href = link.get('href')
                            if href:
                                if not href.startswith('http'):
                                    href = 'https://www.kitco.com' + href
                                news_items.append({
                                    "title": title,
                                    "url": href
                                })
                    
                    # 2. 查找文章元素
                    articles = soup.find_all(['article', 'div', 'section'], class_=re.compile(r'article|news-item|story|post', re.I))
                    for article in articles:
                        title_elem = article.find(['h1', 'h2', 'h3', 'h4', 'a'])
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            if title and len(title) > 15:
                                link = title_elem.get('href')
                                if link:
                                    if not link.startswith('http'):
                                        link = 'https://www.kitco.com' + link
                                    news_items.append({
                                        "title": title,
                                        "url": link
                                    })
                    
                    # 去重
                    seen_titles = set()
                    unique_news = []
                    for item in news_items:
                        if item["title"] not in seen_titles:
                            seen_titles.add(item["title"])
                            unique_news.append(item)
                    
                    # 爬取新闻详情内容
                    detailed_news = []
                    for news in unique_news[:10]:
                        try:
                            # 请求新闻详情页面
                            detail_response = requests.get(news["url"], headers=headers, timeout=15)
                            detail_response.raise_for_status()
                            detail_soup = BeautifulSoup(detail_response.text, 'html.parser')
                            
                            # 提取新闻内容（去掉图片提取）
                            content = ""
                            
                            # 查找文章内容区域（尝试多种选择器）
                            content_selectors = [
                                {'tag': ['article'], 'class': re.compile(r'content|article-body|post-body|entry-content|article-content', re.I)},
                                {'tag': ['div'], 'class': re.compile(r'article|content|main|body', re.I)},
                                {'tag': ['section'], 'class': re.compile(r'content|article', re.I)},
                                {'tag': ['div'], 'id': re.compile(r'article|content|main', re.I)}
                            ]
                            
                            content_found = False
                            for selector in content_selectors:
                                if 'class' in selector:
                                    content_blocks = detail_soup.find_all(selector['tag'], class_=selector['class'])
                                else:
                                    content_blocks = detail_soup.find_all(selector['tag'], id=selector['id'])
                                
                                if content_blocks:
                                    for block in content_blocks:
                                        # 提取段落文本，确保按段落排版
                                        paragraphs = block.find_all('p')
                                        for p in paragraphs:
                                            text = p.get_text(strip=True)
                                            if text and len(text) > 10 and "We appreciate your feedback" not in text:
                                                content += text + "\n\n"
                                        
                                        # 如果没有找到p标签，尝试其他标签
                                        if not paragraphs:
                                            paragraphs = block.find_all(['div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                                            for p in paragraphs:
                                                text = p.get_text(strip=True)
                                                if text and len(text) > 10 and "We appreciate your feedback" not in text:
                                                    content += text + "\n\n"
                                        
                                        content_found = True
                                    break
                            
                            # 如果还是没有找到内容，尝试提取所有段落
                            if not content_found:
                                all_paragraphs = detail_soup.find_all(['p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                                for p in all_paragraphs:
                                    text = p.get_text(strip=True)
                                    if text and len(text) > 10 and "We appreciate your feedback" not in text:
                                        content += text + "\n\n"
                            
                            # 提取发布时间
                            publish_time = ""
                            time_elements = detail_soup.find_all(['time', 'span', 'div'], class_=re.compile(r'time|date|publish|post-date', re.I))
                            if time_elements:
                                for elem in time_elements:
                                    time_text = elem.get_text(strip=True)
                                    if time_text and any(char.isdigit() for char in time_text):
                                        publish_time = time_text
                                        break
                            
                            # 翻译新闻标题和内容，严格控制翻译额度
                            translated_title = baidu_translate(news["title"])
                            
                            # 限制内容长度，控制翻译额度消耗
                            content_to_translate = content.strip()
                            if len(content_to_translate) > 2000:
                                content_to_translate = content_to_translate[:2000] + "..."
                            
                            translated_content = baidu_translate(content_to_translate)
                            
                            detailed_news.append({
                                "title": translated_title,
                                "url": news["url"],
                                "content": translated_content,
                                "publish_time": publish_time,
                                "original_title": news["title"],
                                "original_content": content.strip(),
                                "translation_status": "translated",
                                "note": "使用百度翻译API翻译"
                            })
                            print(f"已爬取并翻译新闻: {news['title']}")
                            
                        except Exception as e:
                            print(f"爬取新闻详情失败: {e}")
                            detailed_news.append({
                                "title": news["title"],
                                "url": news["url"],
                                "content": "无法获取新闻内容",
                                "publish_time": "",
                                "translation_status": "failed",
                                "note": f"爬取详情失败: {str(e)}"
                            })
                    
                    if detailed_news:
                        gold_data["data"]["news_articles"] = detailed_news
                    else:
                        # 如果没有提取到新闻，保存页面标题作为基本信息
                        gold_data["data"]["note"] = "未找到新闻文章，但页面访问成功"
            
            if gold_data["data"]:
                output_dir = "data"
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{output_dir}/gold_{data_type}_data_{timestamp}.json"
                
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(gold_data, f, ensure_ascii=False, indent=2)
                
                print(f"数据已成功保存到: {filename}")
            
            else:
                print(f"未从 {source} 获取到有效数据")
                
        except requests.RequestException as e:
            print(f"请求 {source} 失败: {e}")

def setup_schedule():
    """设置定时任务"""
    print("设置定时爬取任务...")
    
    # 设置价格数据爬取：每10分钟
    schedule.every(10).minutes.do(crawl_price_data)
    
    # 设置新闻资讯爬取：每2小时
    schedule.every(2).hours.do(crawl_news_data)
    
    # 设置市场分析爬取：每6小时
    schedule.every(6).hours.do(crawl_analysis_data)
    
    print("定时任务设置完成:")
    print("✓ 价格数据：每10分钟")
    print("✓ 新闻资讯：每2小时")
    print("✓ 市场分析：每6小时")
    print("\n任务将在后台运行...")

def run_schedule():
    """运行定时任务"""
    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次

def crawl_gold_data():
    """立即执行一次爬取（用于测试）"""
    print("立即执行一次数据爬取...")
    crawl_price_data()
    crawl_news_data()
    crawl_analysis_data()

def start_scheduled_tasks():
    """启动定时爬取任务"""
    print("\n" + "="*50)
    print("贵金属数据定时爬取任务")
    print("="*50)
    
    # 设置定时任务
    setup_schedule()
    
    # 立即执行一次爬取
    crawl_gold_data()
    
    # 在后台运行定时任务
    schedule_thread = threading.Thread(target=run_schedule, daemon=True)
    schedule_thread.start()
    
    print("\n定时任务已启动，按 Ctrl+C 停止...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n定时任务已停止")

if __name__ == "__main__":
    start_scheduled_tasks()
