import sys
import os

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from gold_crawler import crawl_news_data

if __name__ == "__main__":
    print("开始爬取贵金属资讯信息...")
    crawl_news_data()
    print("资讯爬取完成！")
