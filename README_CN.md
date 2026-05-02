# Daisy 金融研究 — 自动化股票/公司研究 Agent Skill

[English](README.md) | [GitHub](https://github.com/Agents365-ai/daisy-financial-research) | [Releases](https://github.com/Agents365-ai/daisy-financial-research/releases)

## 这是什么

一个面向 AI Coding Agent 的金融研究技能 (skill)。给定股票/公司/行业话题，它会先制定研究计划，再用 Tushare 取结构化数据、用 Brave/Bailian MCP 做网络检索、用 Python 做计算与估值，最后产出带来源、可复核的 Markdown + HTML(+ PDF) 报告。

设计参照 `virattt/dexter` 的迭代式 agent 循环 (plan → gather → validate → answer)，但作为一个跨平台 skill 打包，无需独立 CLI。

**关键能力:**
- **Agent-native CLI** (v2.1.0+)：每个脚本在 stdout 不是 TTY 时自动输出稳定的 `{ok, data, meta}` JSON envelope，全部支持 `--schema` 内省、`--dry-run` 预演，退出码 0–5 全部明文文档化。
- **跨会话决策记忆** (v2.2.0+)：追加式 Markdown 决策日志，`pending → resolved` 生命周期，原子化重写，胜率 / 平均超额收益统计。文件格式与 TradingAgents 的 `memory.py` 字节级兼容。
- **AKShare 港股兜底** (v2.3.0+)：填补 daisy 在 CLAUDE.md 已记录的 Tushare gap (`pro.hk_daily_basic` 在本环境返回 `请指定正确的接口名`)，提供 PE/PB/PS 快照与 ROE/EPS/BPS 时间序列，无需 Tushare token，懒加载。
- **Prompt 模板库** (v2.4.0+)：从 TradingAgents 借鉴的 5 份 prompt 文档 — 多空 / 多空辩论 + 综合，激进 / 保守 / 中立风险辩论，反思 prompt，决策 schema，A 股/港股市场分析框架，技术指标 cheatsheet。详见 `references/`。
- **Auto-resolve 工作流** (v2.5.0+)：`dexter_memory_log.py auto-resolve` 自动取价（决策日 + as-of 日）+ 取对应基准（A 股 → CSI 300，港股 → HSI 经 AKShare Sina 兜底，美股 → SPY 经 yfinance），算 raw / alpha / 持仓天数，并一步完成 pending 条目的 resolve。
- 计划先行 + JSONL scratchpad，记录每次工具调用、参数、结果、假设。
- DCF 估值 + 敏感性矩阵 + 合理性校验。
- 银行/金融板块估值替代框架 (RoTE / CET1 / NIM / P/B / 派息率)。
- A 股 + 港股通预设筛选 (股息质量、价值、动量等)。
- 三层报告输出 (md → html → 可选 pdf)，CSS 已内置中英文字体回退。
- Brave MCP + Bailian WebSearch MCP 双通道检索。

## 多平台支持

| 平台 | 状态 | 说明 |
|---|---|---|
| **Claude Code** | ✅ | 原生 SKILL.md 格式 |
| **Opencode** | ✅ | 自动读取 `~/.claude/skills/` |
| **OpenClaw / ClawHub** | ✅ | `metadata.openclaw` 命名空间，依赖检查 |
| **Hermes Agent** | ✅ | `metadata.hermes` 命名空间 |
| **OpenAI Codex** | ✅ | `agents/openai.yaml` sidecar |
| **SkillsMP** | ✅ | GitHub topic 已配置 |

## 前置依赖

```bash
# Python 3.9+
pip install tushare pandas requests
# 可选: AKShare 港股估值/基本面兜底 (无需 Tushare token)
pip install akshare
# 可选: PDF 输出
brew install pandoc
brew install --cask mactex   # 或 brew install --cask basictex (体积小)
```

环境变量:
```bash
export TUSHARE_TOKEN=xxxxxxxx   # 任何 Tushare 调用都需要
```

## 安装

| 平台 | 全局 | 项目级 |
|---|---|---|
| Claude Code | `git clone https://github.com/Agents365-ai/daisy-financial-research.git ~/.claude/skills/daisy-financial-research` | `git clone ... .claude/skills/daisy-financial-research` |
| Opencode | `git clone ... ~/.config/opencode/skills/daisy-financial-research` | `git clone ... .opencode/skills/daisy-financial-research` |
| OpenClaw | `clawhub install daisy-financial-research` 或 `git clone ... ~/.openclaw/skills/daisy-financial-research` | `git clone ... skills/daisy-financial-research` |
| Hermes | `git clone ... ~/.hermes/skills/research/daisy-financial-research` | 通过 `~/.hermes/config.yaml` 的 `external_dirs` |
| OpenAI Codex | `git clone ... ~/.agents/skills/daisy-financial-research` | `git clone ... .agents/skills/daisy-financial-research` |
| SkillsMP | `skills install daisy-financial-research` | — |

## 快速开始

```bash
# A 股股息质量 watchlist + Markdown 报告草稿
python <skill-dir>/scripts/screen_a_share.py --preset a_dividend_quality --top 50 --report

# 把 Markdown 草稿渲染成三层报告
python <skill-dir>/scripts/financial_report.py ./financial-research/reports/<TIMESTAMP>_a-share-a_dividend_quality-screen.md \
    --title "A股股息 watchlist" --slug a-div-quality --pdf
```

默认输出全部落到当前目录下的 `./financial-research/{reports,watchlists,scratchpad,universes,memory}/` 里。

## Agent-native CLI

`scripts/` 下所有脚本遵循统一契约，同时服务终端用户和通过 subprocess 调用的 agent。六个脚本同样的形状：

```bash
# 内省脚本的参数和输出 schema (agent 应优先用这个，而不是解析 --help)
python <skill-dir>/scripts/screen_a_share.py --schema

# 预演请求形状 — 不调用 Tushare、不写文件
python <skill-dir>/scripts/screen_a_share.py --preset a_value --dry-run

# 强制 JSON 输出，不管 stdout 是不是 TTY
DAISY_FORCE_JSON=1 python <skill-dir>/scripts/screen_a_share.py --preset a_value
```

**输出自动检测：** stdout 不是 TTY 时输出单一 JSON envelope；stdout 是 TTY 时输出原来的人类可读表格。可用 `--format json|table` 强制指定。

**成功 envelope：**
```json
{
  "ok": true,
  "data": { "trade_date": "20260430", "candidates": 50, "csv": "...", "preview": [...] },
  "meta": { "schema_version": "1.0.0", "request_id": "req_abc123", "latency_ms": 412 }
}
```

**错误 envelope：**
```json
{
  "ok": false,
  "error": { "code": "no_data", "message": "...", "retryable": true, "context": {...} },
  "meta": { ... }
}
```

**退出码：** `0` 成功 · `1` 运行时错误 · `2` 认证 · `3` 参数验证 · `4` 无数据 · `5` 依赖缺失。

**长时操作**(`screen_hk_connect.py --with-momentum`、`financial_report.py`) 在 stderr 上发 NDJSON 进度事件，每个阶段一行 JSON，agent 不用阻塞在 stdout 上也能监测活性。

## 输出路径

| 脚本 | 默认子目录 | 用途 |
|---|---|---|
| `dexter_scratchpad.py` | `./financial-research/scratchpad/` | 单任务 JSONL，记录工具调用/参数/结果/假设 |
| `dexter_memory_log.py` | `./financial-research/memory/` | 跨会话决策日志，`pending → resolved` 生命周期。v2.5.0+ 新增 `auto-resolve` 子命令，自动取价 + 取基准 + 算超额收益，一步完成解析 |
| `financial_report.py` | `./financial-research/reports/` | Markdown → HTML → 可选 PDF 报告渲染 |
| `screen_a_share.py` | `./financial-research/watchlists/` (`--report` 时 + `reports/`) | A 股多因子筛选 (预设驱动) |
| `screen_hk_connect.py` | `./financial-research/watchlists/` | 港股通筛选 (仅在用户明确要求 港股通 时使用) |
| `hk_connect_universe.py` | `./financial-research/universes/` | 南向港股通 universe 导出 |
| `akshare_hk_valuation.py` | (只读) | 用 AKShare 取港股 PE/PB/PS + ROE/EPS — 填补 `pro.hk_daily_basic` gap |

任何脚本都接受 `--out-dir <root>` 来自定义根目录，子目录会自动追加。

**Hermes 用户**: 想保留旧的 `~/.hermes/reports/financial-research/<subdir>/` 布局，给每个脚本加 `--out-dir ~/.hermes/reports/financial-research` 即可。

## 用 uv 管理依赖

`pyproject.toml` 列出运行时依赖，本地用 uv 复现环境：

```bash
uv sync                  # 核心: tushare / pandas / numpy / requests
uv sync --extra akshare  # 加上: akshare (港股估值 + HSI 基准回退)
uv sync --extra us       # 加上: yfinance (auto-resolve 处理美股 ticker)
uv sync --all-extras     # 全部装上
```

## 自动更新

技能会在每次会话首次调用时检查 `<skill-dir>/.last_update`。超过 24 小时则静默 `git pull --ff-only`。失败 (离线/冲突/非 git checkout) 不会打断流程，也不会通知用户。

手动更新:
```bash
cd <skill-dir> && git pull
```

## 与无 skill 的对比

| 能力 | 原生 agent | 本 skill |
|---|---|---|
| 计划先行 + scratchpad | 否 | 是 (强制 JSONL 记录) |
| 跨会话决策记忆 | 否 | 追加式 Markdown 日志 + 胜率 / 平均超额收益统计 |
| Agent-native CLI (JSON envelope, schema 内省, dry-run) | 需手写 | 每个脚本内置 |
| 数值校验 checklist | 否 | 是 (单位/币种/期间/口径) |
| 银行估值不用 DCF | 看运气 | 默认强制改用 RoTE/CET1/NIM/P/B |
| Tushare 路由 + 已知失败接口规避 | 否 | 是 (内置 gotchas + 港股 AKShare 兜底) |
| 多预设股票筛选 | 否 | 是 (`a_dividend_quality` / `a_value` / 港股通) |
| 三层报告 (md+html+pdf) | 需手写 | 一行命令产出 |
| 港股通 universe 导出 | 否 | 是 (向后回填日期) |
| 软循环上限 + 重复查询检测 | 否 | 是 (避免工具调用失控) |
| 多空 / 风险辩论 prompt 模板 | 否 | `references/debate-prompts.md`, `references/risk-debate-prompts.md` |
| 决策 schema (5 档评级 + Markdown 输出契约) | 否 | `references/decision-schema.md` |
| A 股 / 港股市场分析师 prompt | 否 | `references/cn-market-analyst-prompts.md` |
| 自动 resolve 决策日志 (取价 + 取基准 + 算 alpha) | 否 | `dexter_memory_log.py auto-resolve` |

## 免责声明

本技能仅用于数据分析与研究记录，不构成投资建议。所有结论需结合最新公开信息独立判断。

## 支持作者

如果这个 skill 对你有帮助，欢迎支持作者：

<table>
  <tr>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Agents365-ai/images_payment/main/qrcode/wechat-pay.png" width="180" alt="微信支付">
      <br>
      <b>微信支付</b>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Agents365-ai/images_payment/main/qrcode/alipay.png" width="180" alt="支付宝">
      <br>
      <b>支付宝</b>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Agents365-ai/images_payment/main/qrcode/buymeacoffee.png" width="180" alt="Buy Me a Coffee">
      <br>
      <b>Buy Me a Coffee</b>
    </td>
    <td align="center">
      <img src="https://raw.githubusercontent.com/Agents365-ai/images_payment/main/awarding/award.gif" width="180" alt="打赏">
      <br>
      <b>打赏</b>
    </td>
  </tr>
</table>

## 作者

- Bilibili: https://space.bilibili.com/1107534197
- GitHub: https://github.com/Agents365-ai
