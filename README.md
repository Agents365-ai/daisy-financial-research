# Daisy 金融研究 — 自动化股票/公司研究 Agent Skill

[English](README_EN.md) | [GitHub](https://github.com/Agents365-ai/daisy-financial-research)

## 这是什么

一个面向 AI Coding Agent 的金融研究技能 (skill)。给定股票/公司/行业话题，它会先制定研究计划，再用 Tushare 取结构化数据、用 Brave/Bailian MCP 做网络检索、用 Python 做计算与估值，最后产出带来源、可复核的 Markdown + HTML(+ PDF) 报告。

设计参照 `virattt/dexter` 的迭代式 agent 循环 (plan → gather → validate → answer)，但作为一个跨平台 skill 打包，无需独立 CLI。

**关键能力:**
- 计划先行 + JSONL scratchpad，记录每次工具调用、参数、结果、假设
- DCF 估值 + 敏感性矩阵 + 合理性校验
- 银行/金融板块估值替代框架 (RoTE / CET1 / NIM / P/B / 派息率)
- A 股 + 港股通预设筛选 (股息质量、价值、动量等)
- 三层报告输出 (md → html → 可选 pdf)，CSS 已内置中英文字体回退
- Brave MCP + Bailian WebSearch MCP 双通道检索

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

默认输出全部落到当前目录下的 `./financial-research/{reports,watchlists,scratchpad,universes}/` 里。

## 输出路径

| 脚本 | 默认子目录 |
|---|---|
| `dexter_scratchpad.py` | `./financial-research/scratchpad/` |
| `financial_report.py` | `./financial-research/reports/` |
| `screen_a_share.py` | `./financial-research/watchlists/` (+ `reports/` 如果 `--report`) |
| `screen_hk_connect.py` | `./financial-research/watchlists/` |
| `hk_connect_universe.py` | `./financial-research/universes/` |

任何脚本都接受 `--out-dir <root>` 来自定义根目录，子目录会自动追加。

**Hermes 用户**: 想保留旧的 `~/.hermes/reports/financial-research/<subdir>/` 布局，给每个脚本加 `--out-dir ~/.hermes/reports/financial-research` 即可。

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
| 数值校验 checklist | 否 | 是 (单位/币种/期间/口径) |
| 银行估值不用 DCF | 看运气 | 默认强制改用 RoTE/CET1/NIM/P/B |
| Tushare 路由 + 已知失败接口规避 | 否 | 是 (内置 gotchas) |
| 多预设股票筛选 | 否 | 是 (`a_dividend_quality` / `a_value` / 港股通) |
| 三层报告 (md+html+pdf) | 需手写 | 一行命令产出 |
| 港股通 universe 导出 | 否 | 是 (向后回填日期) |
| 软循环上限 + 重复查询检测 | 否 | 是 (避免工具调用失控) |

## 免责声明

本技能仅用于数据分析与研究记录，不构成投资建议。所有结论需结合最新公开信息独立判断。
