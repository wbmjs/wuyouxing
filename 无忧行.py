#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无忧行 全节点代理服务器地址获取工具 v9 安全版
导出格式: https://代理地址#节点名称
功能: 自动去重 + HTTPS前缀 + 跳过自动选择 + 精简输出
"""
import os
import re
import json
import base64
import requests
import time
from datetime import datetime
from typing import Dict, List, Optional

# 云环境临时目录（不可写本地路径）
EXPORT_DIR = "/tmp"

def safe_b64decode(b64_str: str) -> str:
    padding = 4 - len(b64_str) % 4
    if padding != 4:
        b64_str += '=' * padding
    raw_bytes = base64.b64decode(b64_str)
    return raw_bytes.decode('utf-8', errors='ignore')

class AllNodesFetcher:
    def __init__(self):
        # 从环境变量读取敏感信息（不上传代码）
        self.token = os.getenv("WYH_TOKEN", "")
        self.base_url = os.getenv("WYH_BASE_URL", "")
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36 EdgA/147.0.0.0',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Content-Type': 'application/x-www-form-urlencoded',
            'token': self.token,
            'Pac-Encode': 'base64',
            'sec-ch-ua': '"Microsoft Edge";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Dest': 'empty',
        })

    def get_node_list(self) -> List[Dict]:
        print("[1/3] 获取节点列表...")
        url = f"{self.base_url}/chrome/popup"
        params = {'token': self.token, 'lang': 'zh-CN', 'version': '1.3.23'}
        data = {'proxy_mode': '5', 'proxy_id': '8'}
        resp = self.session.post(url, params=params, data=data, timeout=15)
        raw = resp.json()
        new_token = raw.get('session', {}).get('token')
        if new_token:
            self.token = new_token
            self.session.headers['token'] = self.token
        html = raw.get('html', {}).get('body', '')
        nodes = []
        pattern = r'<option\s+value="(\d+)"(?:\s+selected)?>(.*?)</option>'
        matches = re.findall(pattern, html, re.DOTALL)
        for node_id, raw_text in matches:
            text = re.sub(r'<[^>]+>', '', raw_text).strip()
            if '自动选择' in text:
                continue
            clean = re.sub(r'[\U0001f300-\U0001f9ff]', '', text)
            clean = re.sub(r'\[.*?\]', '', clean).strip()
            nodes.append({'id': node_id, 'name': clean})
        print(f"     找到 {len(nodes)} 个节点（已跳过自动选择）")
        return nodes

    def get_proxy_for_node(self, node_id: str) -> Optional[str]:
        url = f"{self.base_url}/chrome/popup"
        params = {'token': self.token, 'lang': 'zh-CN', 'version': '1.3.23'}
        data = {'proxy_mode': '5', 'proxy_id': node_id}
        try:
            resp = self.session.post(url, params=params, data=data, timeout=10)
            raw = resp.json()
            b64_data = raw.get('session', {}).get('proxy_settings', {}).get('value', {}).get('pacScript', {}).get('data', '')
            if not b64_data:
                return None
            pac_code = safe_b64decode(b64_data)
            match = re.search(r"var\s+proxy\s*=\s*['\"]([^'\"]+)['\"]", pac_code)
            if match:
                return match.group(1)
        except Exception as e:
            print(f" 获取失败: {e}")
        return None

    def deduplicate(self, results: List[Dict]) -> List[Dict]:
        seen = {}
        unique = []
        for r in results:
            proxy = r.get('proxy')
            if proxy and proxy not in seen:
                seen[proxy] = True
                unique.append(r)
        return unique

    def format_proxy(self, proxy_str: str) -> str:
        for prefix in ['HTTPS ', 'HTTP ', 'https://', 'http://']:
            if proxy_str.startswith(prefix):
                proxy_str = proxy_str[len(prefix):]
                break
        return f"https://{proxy_str}"

    def run(self):
        print("=" * 60)
        print("   全节点代理服务器地址获取工具")
        print("=" * 60)
        nodes = self.get_node_list()
        if not nodes:
            print("[!] 未获取到节点列表")
            return
        print(f"\n[2/3] 开始获取 {len(nodes)} 个节点的代理地址...\n")
        results = []
        for i, node in enumerate(nodes, 1):
            print(f"  [{i}/{len(nodes)}] {node['name']} (ID: {node['id']})...", end="", flush=True)
            proxy = self.get_proxy_for_node(node['id'])
            if proxy:
                print(f" ✓")
                results.append({
                    'id': node['id'],
                    'name': node['name'],
                    'proxy': proxy
                })
            else:
                print(" ✗")
                results.append({
                    'id': node['id'],
                    'name': node['name'],
                    'proxy': None
                })
            if i < len(nodes):
                time.sleep(1)
        print(f"\n[3/3] 保存结果...")
        self.save_results(results)

    def save_results(self, results: List[Dict]):
        os.makedirs(EXPORT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        total = len(results)
        success = len([r for r in results if r['proxy']])
        unique_results = self.deduplicate([r for r in results if r['proxy']])
        unique_count = len(unique_results)

        txt_file = os.path.join(EXPORT_DIR, f"proxy_list_{ts}.txt")
        with open(txt_file, 'w', encoding='utf-8') as f:
            for r in unique_results:
                proxy = self.format_proxy(r['proxy'])
                f.write(f"{proxy}#{r['name']}\n")

        json_file = os.path.join(EXPORT_DIR, f"proxy_list_{ts}.json")
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump({
                'all_nodes': results,
                'unique_nodes': unique_results,
                'stats': {
                    'total': total,
                    'success': success,
                    'failed': total - success,
                    'unique_proxies': unique_count
                }
            }, f, ensure_ascii=False, indent=2)

        print(f"\n{'='*60}")
        print(f"  完成!")
        print(f"{'='*60}")
        print(f"  总节点数: {total}")
        print(f"  成功获取: {success}")
        print(f"  获取失败: {total - success}")
        print(f"  去重后: {unique_count} 个唯一代理地址")
        print(f"{'='*60}")
        print(f"\n[✓] 纯文本: {txt_file}")
        print(f"[✓] JSON:   {json_file}")

# 云函数入口
def handler(event, context):
    fetcher = AllNodesFetcher()
    fetcher.run()
    return {
        "code": 200,
        "msg": "执行完成"
    }

if __name__ == "__main__":
    AllNodesFetcher().run()
