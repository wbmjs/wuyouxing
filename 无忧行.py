#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无忧行节点 → 输出完整 Clash YAML
"""
import os
import re
import json
import base64
import requests
import time
from typing import Dict, List, Optional

EXPORT_DIR = "/tmp"


def safe_b64decode(b64_str: str) -> str:
    padding = 4 - len(b64_str) % 4
    if padding != 4:
        b64_str += '=' * padding
    raw_bytes = base64.b64decode(b64_str)
    return raw_bytes.decode('utf-8', errors='ignore')


class AllNodesFetcher:
    def __init__(self):
        self.token = os.getenv("WYH_TOKEN", "")
        self.base_url = os.getenv("WYH_BASE_URL", "")

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'token': self.token,
            'Pac-Encode': 'base64',
        })

    def _refresh_token(self, raw: dict):
        new_token = raw.get('session', {}).get('token')
        if new_token and new_token != self.token:
            print(f"  [token] 刷新 → {new_token[:8]}...")
            self.token = new_token
            self.session.headers['token'] = self.token

    def get_node_list(self) -> List[Dict]:
        print("[1/3] 获取节点列表...")
        url = f"{self.base_url}/chrome/popup"
        params = {'token': self.token, 'lang': 'zh-CN', 'version': '1.3.23'}
        data = {'proxy_mode': '5', 'proxy_id': '8'}

        resp = self.session.post(url, params=params, data=data, timeout=15)
        raw = resp.json()
        self._refresh_token(raw)

        html = raw.get('html', {}).get('body', '')
        nodes = []
        pattern = r'<option\s+value="(\d+)"(?:\s+selected)?>(.*?)</option>'
        matches = re.findall(pattern, html, re.DOTALL)

        for node_id, raw_text in matches:
            text = re.sub(r'<[^>]+>', '', raw_text).strip()
            if '自动选择' in text:
                continue
            clean = re.sub(r'[\U0001f300-\U0001f9ff]', '', text)
            clean = re.sub(r'$$.*?$$', '', clean).strip()
            nodes.append({'id': node_id, 'name': clean})

        print(f"  找到 {len(nodes)} 个节点")
        return nodes

    def get_proxy_for_node(self, node_id: str) -> Optional[str]:
        url = f"{self.base_url}/chrome/popup"
        params = {'token': self.token, 'lang': 'zh-CN', 'version': '1.3.23'}
        data = {'proxy_mode': '5', 'proxy_id': node_id}
        try:
            resp = self.session.post(url, params=params, data=data, timeout=10)
            raw = resp.json()
            self._refresh_token(raw)

            b64_data = (raw.get('session', {})
                        .get('proxy_settings', {})
                        .get('value', {})
                        .get('pacScript', {})
                        .get('data', ''))
            if not b64_data:
                print(f"    [!] node {node_id}: pacScript.data 为空")
                return None

            pac_code = safe_b64decode(b64_data)
            match = re.search(r"var\s+proxy\s*=\s*['\"]([^'\"]+)['\"]", pac_code)
            if match:
                return match.group(1)

            print(f"    [!] node {node_id}: 未匹配到 proxy 变量")
            print(f"        PAC 片段: {pac_code[:300]}")
        except Exception as e:
            print(f"    [!] node {node_id}: 请求异常 {e}")
        return None

    # ── 修复：deduplicate 逻辑 ──
    def deduplicate(self, results):
        seen = set()
        unique = []
        for r in results:
            if r['proxy'] not in seen:
                seen.add(r['proxy'])
                unique.append(r)
        return unique

    # ── 修复：解析单个 host:port ──
    def parse_single_proxy(self, s: str):
        """解析 'HTTPS host:port' 或 'host:port'"""
        s = (s.strip()
                .replace('HTTPS ', '')
                .replace('HTTP ', '')
                .replace('https://', '')
                .replace('http://', ''))
        if ':' in s:
            h, p = s.rsplit(':', 1)
            return h.strip(), int(p)
        return s.strip(), 443

    # ── 修复：处理分号分隔的多服务器 ──
    def parse_proxy(self, s: str):
        """返回第一个可用的 (host, port)"""
        parts = [p.strip() for p in s.split(';') if p.strip()]
        return self.parse_single_proxy(parts[0])

    def generate_full_clash_yaml(self, nodes: List[Dict]) -> str:
        node_names = [f'"{n["name"]}"' for n in nodes]
        node_list_str = ', '.join(node_names)

        yaml = '''mixed-port: 7897
allow-lan: true
mode: rule
log-level: info
unified-delay: true
tcp-concurrent: true
find-process-mode: strict
dns:
  enable: true
  listen: "127.0.0.1:5335"
  use-system-hosts: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  default-nameserver: [180.76.76.76, 182.254.118.118, 8.8.8.8, 180.184.2.2]
  nameserver: [180.76.76.76, 119.29.29.29, 180.184.1.1, 223.5.5.5, 8.8.8.8, "https://223.6.6.6/dns-query#h3=true", "https://dns.alidns.com/dns-query", "https://cloudflare-dns.com/dns-query", "https://doh.pub/dns-query"]
  fallback: ["https://000000.dns.nextdns.io/dns-query#h3=true", "https://dns.alidns.com/dns-query", "https://doh.pub/dns-query", "https://public.dns.iij.jp/dns-query", "https://101.101.101.101/dns-query", "https://208.67.220.220/dns-query", "tls://8.8.4.4", "tls://1.0.0.1:853", "https://cloudflare-dns.com/dns-query", "https://dns.google/dns-query"]
  fallback-filter: {geoip: true, ipcidr: [240.0.0.0/4, 0.0.0.0/32, 127.0.0.1/32], domain: ["+.google.com", "+.facebook.com", "+.twitter.com", "+.youtube.com", "+.xn--ngstr-lra8j.com", "+.google.cn", "+.googleapis.cn", "+.googleapis.com", "+.gvt1.com"]}
  fake-ip-filter: ["*.lan", "stun.*.*.*", "stun.*.*", time.windows.com, time.nist.gov, time.apple.com, time.asia.apple.com, "*.ntp.org.cn", "*.openwrt.pool.ntp.org", time1.cloud.tencent.com, time.ustc.edu.cn, pool.ntp.org, ntp.ubuntu.com, ntp.aliyun.com, ntp1.aliyun.com, ntp2.aliyun.com, ntp3.aliyun.com, ntp4.aliyun.com, ntp5.aliyun.com, ntp6.aliyun.com, ntp7.aliyun.com, time1.aliyun.com, time2.aliyun.com, time3.aliyun.com, time4.aliyun.com, time5.aliyun.com, time6.aliyun.com, time7.aliyun.com, "*.time.edu.cn", time1.apple.com, time2.apple.com, time3.apple.com, time4.apple.com, time5.apple.com, time6.apple.com, time7.apple.com, time1.google.com, time2.google.com, time3.google.com, time4.google.com, music.163.com, "*.music.163.com", "*.126.net", musicapi.taihe.com, music.taihe.com, songsearch.kugou.com, trackercdn.kugou.com, "*.kuwo.cn", api-jooxtt.sanook.com, api.joox.com, joox.com, y.qq.com, "*.y.qq.com", streamoc.music.tc.qq.com, mobileoc.music.tc.qq.com, isure.stream.qqmusic.qq.com, dl.stream.qqmusic.qq.com, aqqmusic.tc.qq.com, amobile.music.tc.qq.com, "*.xiami.com", "*.music.migu.cn", music.migu.cn, "*.msftconnecttest.com", "*.msftncsi.com", localhost.ptlogin2.qq.com, "*.*.*.srv.nintendo.net", "*.*.stun.playstation.net", "xbox.*.*.microsoft.com", "*.ipv6.microsoft.com", "*.*.xboxlive.com", speedtest.cros.wr.pvp.net]
profile:
  store-selected: true
  store-fake-ip: false
sniffer:
  enable: true
  parse-pure-ip: true
  sniff:
    HTTP: {ports: [80, 8080-8880], override-destination: true}
    QUIC: {ports: [443, 8443]}
    TLS: {ports: [443, 8443]}
geodata-mode: true
geo-auto-update: true
geodata-loader: standard
geo-update-interval: 24
geox-url:
  geoip: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geoip.dat
  geosite: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geosite.dat
  mmdb: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/country.mmdb
  asn: https://github.com/xishang0128/geoip/releases/download/latest/GeoLite2-ASN.mmdb

proxies:
'''
        # ── 插入节点 ──
        for node in nodes:
            name = node['name']
            host, port = self.parse_proxy(node['proxy'])
            yaml += f'  - {{name: "{name}", type: http, server: {host}, port: {port}, tls: true}}\n'

        # ── proxy-groups ──
        yaml += f'''
proxy-groups:
  - name: "🚀 节点选择"
    type: select
    proxies: ["⚡ 自动选择", {node_list_str}, DIRECT, REJECT]
  - name: "⚡ 自动选择"
    type: url-test
    proxies: [{node_list_str}]
    url: "https://www.gstatic.com/generate_204"
    interval: 300
    lazy: false
  - name: "🛑 广告拦截"
    type: select
    proxies: [REJECT, DIRECT, "🚀 节点选择"]
  - name: "🤖 AI 服务"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "📹 油管视频"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🔍 谷歌服务"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "Ⓜ️ 微软服务"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🍏 苹果服务"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "📲 电报消息"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🐦 推特/X"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "📘 Meta 系"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🎙️ Discord"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "💬 其他社交"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🎬 奈飞"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🏰 迪士尼+"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "📺 欧美流媒体"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🎌 亚洲流媒体"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🎮 Steam"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🖥️ PC 游戏"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🎯 主机游戏"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🐱 代码托管"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "☁️ 云服务"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🛠️ 开发工具"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "💾 网盘存储"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "💳 支付平台"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "₿ 加密货币"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "📚 教育学术"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "📰 新闻资讯"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🛒 海淘购物"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🏠 私有网络"
    type: select
    proxies: [DIRECT, REJECT, "🚀 节点选择"]
  - name: "🔒 国内服务"
    type: select
    proxies: [DIRECT, REJECT, "🚀 节点选择"]
  - name: "🌍 非中国"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]
  - name: "🐟 漏网之鱼"
    type: select
    proxies: ["🚀 节点选择", "⚡ 自动选择", DIRECT, REJECT]

rule-providers:
  category-ads-all: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/category-ads-all.mrs", path: ./ruleset/category-ads-all.mrs, interval: 86400, format: mrs}
  private: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/private.mrs", path: ./ruleset/private.mrs, interval: 86400, format: mrs}
  private-ip: {type: http, behavior: ipcidr, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geoip/private.mrs", path: ./ruleset/private-ip.mrs, interval: 86400, format: mrs}
  geolocation-cn: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/geolocation-cn.mrs", path: ./ruleset/geolocation-cn.mrs, interval: 86400, format: mrs}
  cn-ip: {type: http, behavior: ipcidr, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geoip/cn.mrs", path: ./ruleset/cn-ip.mrs, interval: 86400, format: mrs}
  geolocation-!cn: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/geolocation-!cn.mrs", path: "./ruleset/geolocation-!cn.mrs", interval: 86400, format: mrs}
  openai: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/openai.mrs", path: ./ruleset/openai.mrs, interval: 86400, format: mrs}
  anthropic: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/anthropic.mrs", path: ./ruleset/anthropic.mrs, interval: 86400, format: mrs}
  category-ai-chat-!cn: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/category-ai-chat-!cn.mrs", path: "./ruleset/category-ai-chat-!cn.mrs", interval: 86400, format: mrs}
  youtube: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/youtube.mrs", path: ./ruleset/youtube.mrs, interval: 86400, format: mrs}
  google: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/google.mrs", path: ./ruleset/google.mrs, interval: 86400, format: mrs}
  google-ip: {type: http, behavior: ipcidr, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geoip/google.mrs", path: ./ruleset/google-ip.mrs, interval: 86400, format: mrs}
  microsoft: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/microsoft.mrs", path: ./ruleset/microsoft.mrs, interval: 86400, format: mrs}
  onedrive: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/onedrive.mrs", path: ./ruleset/onedrive.mrs, interval: 86400, format: mrs}
  apple: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/apple.mrs", path: ./ruleset/apple.mrs, interval: 86400, format: mrs}
  icloud: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/icloud.mrs", path: ./ruleset/icloud.mrs, interval: 86400, format: mrs}
  telegram: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/telegram.mrs", path: ./ruleset/telegram.mrs, interval: 86400, format: mrs}
  telegram-ip: {type: http, behavior: ipcidr, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geoip/telegram.mrs", path: ./ruleset/telegram-ip.mrs, interval: 86400, format: mrs}
  twitter: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/twitter.mrs", path: ./ruleset/twitter.mrs, interval: 86400, format: mrs}
  twitter-ip: {type: http, behavior: ipcidr, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geoip/twitter.mrs", path: ./ruleset/twitter-ip.mrs, interval: 86400, format: mrs}
  facebook: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/facebook.mrs", path: ./ruleset/facebook.mrs, interval: 86400, format: mrs}
  instagram: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/instagram.mrs", path: ./ruleset/instagram.mrs, interval: 86400, format: mrs}
  whatsapp: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/whatsapp.mrs", path: ./ruleset/whatsapp.mrs, interval: 86400, format: mrs}
  facebook-ip: {type: http, behavior: ipcidr, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geoip/facebook.mrs", path: ./ruleset/facebook-ip.mrs, interval: 86400, format: mrs}
  discord: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/discord.mrs", path: ./ruleset/discord.mrs, interval: 86400, format: mrs}
  tiktok: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/tiktok.mrs", path: ./ruleset/tiktok.mrs, interval: 86400, format: mrs}
  line: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/line.mrs", path: ./ruleset/line.mrs, interval: 86400, format: mrs}
  reddit: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/reddit.mrs", path: ./ruleset/reddit.mrs, interval: 86400, format: mrs}
  linkedin: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/linkedin.mrs", path: ./ruleset/linkedin.mrs, interval: 86400, format: mrs}
  snap: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/snap.mrs", path: ./ruleset/snap.mrs, interval: 86400, format: mrs}
  pinterest: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/pinterest.mrs", path: ./ruleset/pinterest.mrs, interval: 86400, format: mrs}
  tumblr: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/tumblr.mrs", path: ./ruleset/tumblr.mrs, interval: 86400, format: mrs}
  netflix: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/netflix.mrs", path: ./ruleset/netflix.mrs, interval: 86400, format: mrs}
  netflix-ip: {type: http, behavior: ipcidr, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geoip/netflix.mrs", path: ./ruleset/netflix-ip.mrs, interval: 86400, format: mrs}
  disney: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/disney.mrs", path: ./ruleset/disney.mrs, interval: 86400, format: mrs}
  hbo: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/hbo.mrs", path: ./ruleset/hbo.mrs, interval: 86400, format: mrs}
  hulu: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/hulu.mrs", path: ./ruleset/hulu.mrs, interval: 86400, format: mrs}
  primevideo: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/primevideo.mrs", path: ./ruleset/primevideo.mrs, interval: 86400, format: mrs}
  apple-tvplus: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/apple-tvplus.mrs", path: ./ruleset/apple-tvplus.mrs, interval: 86400, format: mrs}
  spotify: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/spotify.mrs", path: ./ruleset/spotify.mrs, interval: 86400, format: mrs}
  twitch: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/twitch.mrs", path: ./ruleset/twitch.mrs, interval: 86400, format: mrs}
  dazn: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/dazn.mrs", path: ./ruleset/dazn.mrs, interval: 86400, format: mrs}
  bahamut: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/bahamut.mrs", path: ./ruleset/bahamut.mrs, interval: 86400, format: mrs}
  biliintl: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/biliintl.mrs", path: ./ruleset/biliintl.mrs, interval: 86400, format: mrs}
  niconico: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/niconico.mrs", path: ./ruleset/niconico.mrs, interval: 86400, format: mrs}
  abema: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/abema.mrs", path: ./ruleset/abema.mrs, interval: 86400, format: mrs}
  viu: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/viu.mrs", path: ./ruleset/viu.mrs, interval: 86400, format: mrs}
  kktv: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/kktv.mrs", path: ./ruleset/kktv.mrs, interval: 86400, format: mrs}
  steam: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/steam.mrs", path: ./ruleset/steam.mrs, interval: 86400, format: mrs}
  epicgames: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/epicgames.mrs", path: ./ruleset/epicgames.mrs, interval: 86400, format: mrs}
  ea: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/ea.mrs", path: ./ruleset/ea.mrs, interval: 86400, format: mrs}
  ubisoft: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/ubisoft.mrs", path: ./ruleset/ubisoft.mrs, interval: 86400, format: mrs}
  blizzard: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/blizzard.mrs", path: ./ruleset/blizzard.mrs, interval: 86400, format: mrs}
  gog: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/gog.mrs", path: ./ruleset/gog.mrs, interval: 86400, format: mrs}
  riot: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/riot.mrs", path: ./ruleset/riot.mrs, interval: 86400, format: mrs}
  playstation: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/playstation.mrs", path: ./ruleset/playstation.mrs, interval: 86400, format: mrs}
  xbox: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/xbox.mrs", path: ./ruleset/xbox.mrs, interval: 86400, format: mrs}
  nintendo: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/nintendo.mrs", path: ./ruleset/nintendo.mrs, interval: 86400, format: mrs}
  github: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/github.mrs", path: ./ruleset/github.mrs, interval: 86400, format: mrs}
  gitlab: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/gitlab.mrs", path: ./ruleset/gitlab.mrs, interval: 86400, format: mrs}
  atlassian: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/atlassian.mrs", path: ./ruleset/atlassian.mrs, interval: 86400, format: mrs}
  aws: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/aws.mrs", path: ./ruleset/aws.mrs, interval: 86400, format: mrs}
  azure: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/azure.mrs", path: ./ruleset/azure.mrs, interval: 86400, format: mrs}
  cloudflare: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/cloudflare.mrs", path: ./ruleset/cloudflare.mrs, interval: 86400, format: mrs}
  digitalocean: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/digitalocean.mrs", path: ./ruleset/digitalocean.mrs, interval: 86400, format: mrs}
  vercel: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/vercel.mrs", path: ./ruleset/vercel.mrs, interval: 86400, format: mrs}
  netlify: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/netlify.mrs", path: ./ruleset/netlify.mrs, interval: 86400, format: mrs}
  cloudflare-ip: {type: http, behavior: ipcidr, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geoip/cloudflare.mrs", path: ./ruleset/cloudflare-ip.mrs, interval: 86400, format: mrs}
  docker: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/docker.mrs", path: ./ruleset/docker.mrs, interval: 86400, format: mrs}
  npmjs: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/npmjs.mrs", path: ./ruleset/npmjs.mrs, interval: 86400, format: mrs}
  jetbrains: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/jetbrains.mrs", path: ./ruleset/jetbrains.mrs, interval: 86400, format: mrs}
  stackexchange: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/stackexchange.mrs", path: ./ruleset/stackexchange.mrs, interval: 86400, format: mrs}
  dropbox: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/dropbox.mrs", path: ./ruleset/dropbox.mrs, interval: 86400, format: mrs}
  notion: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/notion.mrs", path: ./ruleset/notion.mrs, interval: 86400, format: mrs}
  paypal: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/paypal.mrs", path: ./ruleset/paypal.mrs, interval: 86400, format: mrs}
  stripe: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/stripe.mrs", path: ./ruleset/stripe.mrs, interval: 86400, format: mrs}
  wise: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/wise.mrs", path: ./ruleset/wise.mrs, interval: 86400, format: mrs}
  binance: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/binance.mrs", path: ./ruleset/binance.mrs, interval: 86400, format: mrs}
  category-scholar-!cn: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/category-scholar-!cn.mrs", path: "./ruleset/category-scholar-!cn.mrs", interval: 86400, format: mrs}
  coursera: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/coursera.mrs", path: ./ruleset/coursera.mrs, interval: 86400, format: mrs}
  udemy: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/udemy.mrs", path: ./ruleset/udemy.mrs, interval: 86400, format: mrs}
  edx: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/edx.mrs", path: ./ruleset/edx.mrs, interval: 86400, format: mrs}
  khanacademy: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/khanacademy.mrs", path: ./ruleset/khanacademy.mrs, interval: 86400, format: mrs}
  wikimedia: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/wikimedia.mrs", path: ./ruleset/wikimedia.mrs, interval: 86400, format: mrs}
  bbc: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/bbc.mrs", path: ./ruleset/bbc.mrs, interval: 86400, format: mrs}
  cnn: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/cnn.mrs", path: ./ruleset/cnn.mrs, interval: 86400, format: mrs}
  nytimes: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/nytimes.mrs", path: ./ruleset/nytimes.mrs, interval: 86400, format: mrs}
  wsj: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/wsj.mrs", path: ./ruleset/wsj.mrs, interval: 86400, format: mrs}
  bloomberg: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/bloomberg.mrs", path: ./ruleset/bloomberg.mrs, interval: 86400, format: mrs}
  amazon: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/amazon.mrs", path: ./ruleset/amazon.mrs, interval: 86400, format: mrs}
  ebay: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/ebay.mrs", path: ./ruleset/ebay.mrs, interval: 86400, format: mrs}
  cn: {type: http, behavior: domain, url: "https://github.com/MetaCubeX/meta-rules-dat/raw/refs/heads/meta/geo/geosite/cn.mrs", path: ./ruleset/cn.mrs, interval: 86400, format: mrs}

rules:
  - RULE-SET,category-ads-all,🛑 广告拦截
  - RULE-SET,private,🏠 私有网络
  - RULE-SET,private-ip,🏠 私有网络,no-resolve
  - RULE-SET,openai,🤖 AI 服务
  - RULE-SET,anthropic,🤖 AI 服务
  - RULE-SET,category-ai-chat-!cn,🤖 AI 服务
  - RULE-SET,geolocation-cn,🔒 国内服务
  - RULE-SET,cn-ip,🔒 国内服务,no-resolve
  - RULE-SET,youtube,📹 油管视频
  - RULE-SET,category-scholar-!cn,📚 教育学术
  - RULE-SET,coursera,📚 教育学术
  - RULE-SET,udemy,📚 教育学术
  - RULE-SET,edx,📚 教育学术
  - RULE-SET,khanacademy,📚 教育学术
  - RULE-SET,wikimedia,📚 教育学术
  - RULE-SET,aws,☁️ 云服务
  - RULE-SET,azure,☁️ 云服务
  - RULE-SET,cloudflare,☁️ 云服务
  - RULE-SET,digitalocean,☁️ 云服务
  - RULE-SET,vercel,☁️ 云服务
  - RULE-SET,netlify,☁️ 云服务
  - RULE-SET,cloudflare-ip,☁️ 云服务,no-resolve
  - RULE-SET,google,🔍 谷歌服务
  - RULE-SET,google-ip,🔍 谷歌服务,no-resolve
  - RULE-SET,telegram,📲 电报消息
  - RULE-SET,telegram-ip,📲 电报消息,no-resolve
  - RULE-SET,github,🐱 代码托管
  - RULE-SET,gitlab,🐱 代码托管
  - RULE-SET,atlassian,🐱 代码托管
  - RULE-SET,microsoft,Ⓜ️ 微软服务
  - RULE-SET,onedrive,Ⓜ️ 微软服务
  - RULE-SET,apple-tvplus,📺 欧美流媒体
  - RULE-SET,apple,🍏 苹果服务
  - RULE-SET,icloud,🍏 苹果服务
  - RULE-SET,twitter,🐦 推特/X
  - RULE-SET,twitter-ip,🐦 推特/X,no-resolve
  - RULE-SET,facebook,📘 Meta 系
  - RULE-SET,instagram,📘 Meta 系
  - RULE-SET,whatsapp,📘 Meta 系
  - RULE-SET,facebook-ip,📘 Meta 系,no-resolve
  - RULE-SET,discord,🎙️ Discord
  - RULE-SET,tiktok,💬 其他社交
  - RULE-SET,line,💬 其他社交
  - RULE-SET,reddit,💬 其他社交
  - RULE-SET,linkedin,💬 其他社交
  - RULE-SET,snap,💬 其他社交
  - RULE-SET,pinterest,💬 其他社交
  - RULE-SET,tumblr,💬 其他社交
  - RULE-SET,netflix,🎬 奈飞
  - RULE-SET,netflix-ip,🎬 奈飞,no-resolve
  - RULE-SET,disney,🏰 迪士尼+
  - RULE-SET,hbo,📺 欧美流媒体
  - RULE-SET,hulu,📺 欧美流媒体
  - RULE-SET,primevideo,📺 欧美流媒体
  - RULE-SET,spotify,📺 欧美流媒体
  - RULE-SET,twitch,📺 欧美流媒体
  - RULE-SET,dazn,📺 欧美流媒体
  - RULE-SET,bahamut,🎌 亚洲流媒体
  - RULE-SET,biliintl,🎌 亚洲流媒体
  - RULE-SET,niconico,🎌 亚洲流媒体
  - RULE-SET,abema,🎌 亚洲流媒体
  - RULE-SET,viu,🎌 亚洲流媒体
  - RULE-SET,kktv,🎌 亚洲流媒体
  - RULE-SET,steam,🎮 Steam
  - RULE-SET,epicgames,🖥️ PC 游戏
  - RULE-SET,ea,🖥️ PC 游戏
  - RULE-SET,ubisoft,🖥️ PC 游戏
  - RULE-SET,blizzard,🖥️ PC 游戏
  - RULE-SET,gog,🖥️ PC 游戏
  - RULE-SET,riot,🖥️ PC 游戏
  - RULE-SET,playstation,🎯 主机游戏
  - RULE-SET,xbox,🎯 主机游戏
  - RULE-SET,nintendo,🎯 主机游戏
  - RULE-SET,docker,🛠️ 开发工具
  - RULE-SET,npmjs,🛠️ 开发工具
  - RULE-SET,jetbrains,🛠️ 开发工具
  - RULE-SET,stackexchange,🛠️ 开发工具
  - RULE-SET,dropbox,💾 网盘存储
  - RULE-SET,notion,💾 网盘存储
  - RULE-SET,paypal,💳 支付平台
  - RULE-SET,stripe,💳 支付平台
  - RULE-SET,wise,💳 支付平台
  - RULE-SET,binance,₿ 加密货币
  - RULE-SET,bbc,📰 新闻资讯
  - RULE-SET,cnn,📰 新闻资讯
  - RULE-SET,nytimes,📰 新闻资讯
  - RULE-SET,wsj,📰 新闻资讯
  - RULE-SET,bloomberg,📰 新闻资讯
  - RULE-SET,amazon,🛒 海淘购物
  - RULE-SET,ebay,🛒 海淘购物
  - RULE-SET,geolocation-!cn,🌍 非中国
  - RULE-SET,cn,🔒 国内服务
  - MATCH,🐟 漏网之鱼
'''
        return yaml

    def run(self):
        nodes = self.get_node_list()

        valid = []
        if nodes:
            results = []
            print("\n[2/3] 获取代理地址...")
            for i, node in enumerate(nodes, 1):
                print(f"  {i}/{len(nodes)} {node['name']}")
                proxy = self.get_proxy_for_node(node['id'])
                if proxy:
                    results.append({"name": node["name"], "proxy": proxy})
                    print(f"    ✓ {proxy[:80]}...")
                else:
                    print(f"    ✗ 失败")
                time.sleep(0.8)
            valid = self.deduplicate(results)
        else:
            print("[!] 未获取到节点列表，将生成空配置")

        print(f"\n[3/3] 生成 YAML（{len(valid)} 个有效节点）...")
        yaml_content = self.generate_full_clash_yaml(valid)

        os.makedirs(EXPORT_DIR, exist_ok=True)
        yaml_path = os.path.join(EXPORT_DIR, "config.yaml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        if valid:
            print(f"\n✅ 生成完成：{yaml_path}")
            print(f"✅ 有效节点：{len(valid)} 个")
        else:
            print(f"\n⚠️ 生成完成但无有效节点：{yaml_path}")
            print("⚠️ 请检查 WYH_TOKEN / WYH_BASE_URL 是否正确")


if __name__ == "__main__":
    AllNodesFetcher().run()
