#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无忧行节点 → 输出完整 Clash YAML
"""
import os
import re
import json
import base64
import sys
import requests
import time
from typing import Dict, List, Optional

EXPORT_DIR = "/tmp"

# 接口参数：厂商改版时可用同名环境变量覆盖，无需改代码
API_VERSION = os.getenv("WYH_API_VERSION", "1.3.23")
PROXY_MODE = os.getenv("WYH_PROXY_MODE", "5")
LIST_PROXY_ID = os.getenv("WYH_LIST_PROXY_ID", "8")
RETRY_DELAY = 1.5


def yaml_escape(text: str) -> str:
    """节点名会同时出现在流式映射和策略组列表里，必须转义成安全的双引号标量。"""
    cleaned = re.sub(r'[\x00-\x1f\x7f]', ' ', str(text))
    cleaned = cleaned.replace('\\', '\\\\').replace('"', '\\"')
    return cleaned.strip()


def safe_b64decode(b64_str: str) -> str:
    padding = 4 - len(b64_str) % 4
    if padding != 4:
        b64_str += '=' * padding
    raw_bytes = base64.b64decode(b64_str)
    return raw_bytes.decode('utf-8', errors='ignore')


class AllNodesFetcher:
    def __init__(self):
        self.token = (os.getenv("WYH_TOKEN") or "").strip()
        self.base_url = (os.getenv("WYH_BASE_URL") or "").strip().rstrip('/')
        self._validate_env()

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'token': self.token,
            'Pac-Encode': 'base64',
        })

    # ── 友好退出：统一打印可读原因并以非 0 退出码终止 ──
    @staticmethod
    def _fail(message: str):
        print(f"\n❌ {message}")
        sys.exit(1)

    # ── 启动即校验环境变量，避免用空 URL 发出无意义的请求 ──
    def _validate_env(self):
        missing = [name for name, value in (("WYH_TOKEN", self.token),
                                            ("WYH_BASE_URL", self.base_url))
                   if not value]
        if missing:
            self._fail(
                "缺少环境变量：" + "、".join(missing) + "\n"
                "  · GitHub Actions：在仓库 Settings → Secrets and variables → "
                "Actions 中添加同名 Secret\n"
                "  · 本地运行：export WYH_TOKEN=… WYH_BASE_URL=https://…"
            )
        if not self.base_url.startswith(('http://', 'https://')):
            self._fail(
                f"WYH_BASE_URL 格式不正确：{self.base_url!r}，"
                "应以 http:// 或 https:// 开头，例如 https://example.com"
            )

    # ── 清理上一次运行可能残留的输出，避免失败时被误当成新配置提交 ──
    @staticmethod
    def _clear_stale_output():
        path = os.path.join(EXPORT_DIR, "config.yaml")
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"[i] 已清理残留文件 {path}")
            except OSError as e:
                print(f"[!] 清理残留文件失败（忽略）：{e}")

    def _refresh_token(self, raw: dict):
        new_token = raw.get('session', {}).get('token')
        if new_token and new_token != self.token:
            print(f"  [token] 刷新 → {new_token[:8]}...")
            self.token = new_token
            self.session.headers['token'] = self.token

    def _post(self, url, params, data, timeout, label, retries=0):
        """带重试的 POST；HTTP 错误直接抛出（重试也不会变好）。"""
        last = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(url, params=params, data=data, timeout=timeout)
                resp.raise_for_status()
                return resp
            except requests.HTTPError:
                raise
            except requests.RequestException as e:
                last = e
                if attempt < retries:
                    wait = RETRY_DELAY * (attempt + 1)
                    print(f"    [!] {label} 请求失败（{type(e).__name__}），"
                          f"{wait:.1f}s 后重试 {attempt + 1}/{retries}")
                    time.sleep(wait)
        raise last

    def get_node_list(self) -> List[Dict]:
        print("[1/3] 获取节点列表...")
        url = f"{self.base_url}/chrome/popup"
        params = {'token': self.token, 'lang': 'zh-CN', 'version': API_VERSION}
        data = {'proxy_mode': PROXY_MODE, 'proxy_id': LIST_PROXY_ID}

        try:
            resp = self._post(url, params, data, 15, "节点列表", retries=2)
            raw = resp.json()
        except requests.HTTPError as e:
            status = getattr(getattr(e, 'response', None), 'status_code', '未知')
            self._fail(
                f"接口返回 HTTP {status}，请检查 WYH_TOKEN 是否有效、"
                "WYH_BASE_URL 是否正确（token 过期是最常见原因）"
            )
        except requests.RequestException as e:
            self._fail(
                f"请求节点列表失败：{e}\n"
                "  · 请检查网络连通性，以及 WYH_BASE_URL 是否可访问"
            )
        except ValueError:
            body = getattr(resp, 'text', '') or ''
            self._fail(
                "接口返回的不是合法 JSON（token 失效时通常被重定向到登录页）\n"
                f"  · 响应片段：{body[:200]}"
            )

        if not isinstance(raw, dict):
            self._fail(f"接口返回了意外的 JSON 类型：{type(raw).__name__}")
        self._refresh_token(raw)

        html = (raw.get('html') or {}).get('body', '')
        if not html:
            self._fail(
                "接口未返回节点列表 HTML（html.body 为空）\n"
                "  · 请检查 WYH_TOKEN 是否有效，或机场是否改动了接口"
            )
        nodes = []
        # 属性顺序、单双引号都交给正则容忍，避免厂商改一下 HTML 就全军覆没
        option_re = re.compile(r'<option\b([^>]*)>(.*?)</option>', re.DOTALL | re.IGNORECASE)
        value_re = re.compile(r'value\s*=\s*["\']?(\d+)', re.IGNORECASE)

        for attrs, raw_text in option_re.findall(html):
            value_match = value_re.search(attrs)
            if not value_match:
                continue
            text = re.sub(r'<[^>]+>', '', raw_text).strip()
            if '自动选择' in text:
                continue
            # 国旗(U+1F1E6 起)不在 U+1F300 区间内，机场节点名里很常见，一并清掉
            clean = re.sub(r'[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF'
                           r'☀-➿⬀-⯿️‍]', '', text)
            # 去掉 $$倍率$$ 与 $倍率$ 两种标记（长写法优先，否则只会剩四个美元符号）
            clean = re.sub(r'\$\$.*?\$\$|\$.*?\$', '', clean)
            clean = re.sub(r'\s+', ' ', clean).strip()
            if not clean:
                continue
            nodes.append({'id': value_match.group(1), 'name': clean})

        print(f"  找到 {len(nodes)} 个节点")
        return nodes

    def get_proxy_for_node(self, node_id: str) -> Optional[str]:
        url = f"{self.base_url}/chrome/popup"
        params = {'token': self.token, 'lang': 'zh-CN', 'version': API_VERSION}
        data = {'proxy_mode': PROXY_MODE, 'proxy_id': node_id}
        try:
            resp = self._post(url, params, data, 10, f"node {node_id}", retries=1)
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

    # ── 拆分 'HTTPS host:port' → (host:port, 是否 TLS) ──
    @staticmethod
    def _split_scheme(s: str):
        s = s.strip()
        low = s.lower()
        for scheme in ('https://', 'http://'):
            if low.startswith(scheme):
                return s[len(scheme):], scheme == 'https://'
        for scheme in ('HTTPS ', 'HTTP '):
            if s.upper().startswith(scheme):
                return s[len(scheme):], scheme == 'HTTPS '
        # PAC 里没标协议时按 HTTPS 处理（与历史行为一致）
        return s, True

    @staticmethod
    def _parse_host_port(body: str):
        if ':' in body:
            host, _, port = body.rpartition(':')
            host, port = host.strip(), port.strip()
            if port.isdigit():
                return host, int(port)
        return body.strip(), 443

    def parse_proxy_entries(self, name: str, raw: str) -> List[Dict]:
        """把 PAC 里的 'HTTPS a:1;b:2' 拆成多个节点，协议决定 tls。"""
        entries, seen = [], set()
        for part in raw.split(';'):
            if not part.strip():
                continue
            body, tls = self._split_scheme(part)
            host, port = self._parse_host_port(body)
            if not host or (host, port) in seen:
                continue
            seen.add((host, port))
            entries.append({'name': name, 'server': host, 'port': port, 'tls': tls})
        return entries

    @staticmethod
    def assign_unique_names(entries: List[Dict]) -> List[Dict]:
        """Clash 要求节点名唯一，重名（含同节点的多服务器）自动加序号。"""
        used = set()
        for entry in entries:
            candidate = entry['name']
            if candidate in used:
                n = 2
                while f"{candidate} {n}" in used:
                    n += 1
                candidate = f"{candidate} {n}"
            entry['name'] = candidate
            used.add(candidate)
        return entries

    def generate_full_clash_yaml(self, nodes: List[Dict]) -> str:
        node_names = [f'"{yaml_escape(n["name"])}"' for n in nodes]
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
        # ── 插入节点（用普通字符串拼接，不含花括号冲突）──
        for node in nodes:
            yaml += ('  - {name: "%s", type: http, server: %s, port: %d, tls: %s}\n'
                     % (yaml_escape(node['name']), node['server'], node['port'],
                        'true' if node['tls'] else 'false'))

        # ── proxy-groups（用 %s 替代 f-string）──
        yaml += '''
proxy-groups:
  - name: "🚀 节点选择"
    type: select
    proxies: ["⚡ 自动选择", %s, DIRECT, REJECT]
  - name: "⚡ 自动选择"
    type: url-test
    proxies: [%s]
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
''' % (node_list_str, node_list_str)

        # ── rule-providers（纯普通字符串，零花括号冲突）──
        yaml += """rule-providers:
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
  - RULE-SET,apple-tvplus,🍏 苹果服务
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
"""
        return yaml

    def run(self):
        self._clear_stale_output()
        nodes = self.get_node_list()

        if not nodes:
            self._fail("未解析到任何节点，已终止。"
                       "仓库中上一个有效的 config.yaml 保持不变。")

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
        if not valid:
            self._fail("没有任何节点取得有效代理地址，已终止。"
                       "仓库中上一个有效的 config.yaml 保持不变。")

        entries = []
        for node in valid:
            parsed = self.parse_proxy_entries(node['name'], node['proxy'])
            if not parsed:
                print(f"    [!] {node['name']}: 无法解析代理地址，已跳过")
                continue
            entries.extend(parsed)
        entries = self.assign_unique_names(entries)
        if not entries:
            self._fail("代理地址全部无法解析，已终止。"
                       "仓库中上一个有效的 config.yaml 保持不变。")

        print(f"\n[3/3] 生成 YAML（{len(entries)} 个有效节点）...")
        yaml_content = self.generate_full_clash_yaml(entries)

        os.makedirs(EXPORT_DIR, exist_ok=True)
        yaml_path = os.path.join(EXPORT_DIR, "config.yaml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        print(f"\n✅ 生成完成：{yaml_path}")
        print(f"✅ 有效节点：{len(entries)} 个")


if __name__ == "__main__":
    AllNodesFetcher().run()
