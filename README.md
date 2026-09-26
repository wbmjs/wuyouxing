# 无忧行 → Clash 订阅

每天自动拉取「无忧行」机场的节点，生成一份完整的 Clash / Mihomo 配置文件并提交回本仓库。

## 订阅地址

```
https://raw.githubusercontent.com/wbmjs/wuyouxing/main/config.yaml
```

在 Clash Verge / ClashX / Mihomo Party 里添加「订阅」即可。注意：`config.yaml` 里的节点接入地址是公开的，任何拿到这个链接的人都能订阅，请自行评估是否适合公开。

## 仓库文件

| 文件 | 说明 |
| --- | --- |
| `无忧行.py` | 抓取脚本：取节点列表 → 逐个取代理地址 → 生成 YAML |
| `config.yaml` | 脚本的产物，由 Actions 每天自动更新，**不要手改** |
| `.github/workflows/auto_run.yml` | 每天 UTC 00:00（北京时间 08:00）自动运行，也可手动触发 |
| `requirements.txt` | Python 依赖 |

## 配置参数

在仓库 **Settings → Secrets and variables → Actions** 中配置两个必填 Secret：

| 名称 | 说明 |
| --- | --- |
| `WYH_TOKEN` | 登录无忧行后拿到的 token |
| `WYH_BASE_URL` | 面板地址，例如 `https://example.com`（不带结尾斜杠） |

缺失或格式不对时脚本会直接报错退出，不会覆盖仓库里已有的 `config.yaml`。

以下可选环境变量用于应对厂商改版，一般不用动：

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `WYH_API_VERSION` | `1.3.23` | 扩展接口版本号 |
| `WYH_PROXY_MODE` | `5` | 代理模式 |
| `WYH_LIST_PROXY_ID` | `8` | 获取节点列表时使用的 `proxy_id` |

## 本地运行

```bash
pip install -r requirements.txt
export WYH_TOKEN='你的 token'
export WYH_BASE_URL='https://你的面板地址'
python3 无忧行.py
```

结果输出到 `/tmp/config.yaml`。

## 更新机制与容错

- 只有在**至少成功抓到一个节点**时才会写文件并提交；抓取失败、token 过期、接口改版等情况会退出码非 0，仓库里的上一份有效配置保持不变。
- Actions 侧还有一层校验：生成结果为空或节点数为 0 时终止，不提交。
- 节点地址相同会自动去重；PAC 里用 `;` 标注的多个备用服务器会全部生成（名称自动加序号）；`HTTPS` / `HTTP` 前缀决定 `tls` 开关。
- 配置内容与上一次完全相同时跳过提交。

## 声明

来源于网络，侵权联系删除
