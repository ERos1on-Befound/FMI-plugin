# 贡献指南

感谢你愿意改进 Financial Market Intelligence（FMI）。本项目是一个轻量的金融数据检索与研究工具，通过 MCP 暴露给 AI Agent，因此**正确性、可追溯性与诚实**比功能数量更重要。

## 环境准备

```bash
git clone <repository-url>
cd financial-market-intelligence
uv sync
```

要求 Python ≥ 3.12。运行检查：

```bash
uv run ruff check .                 # 代码检查（CI 会跑，必须 0 问题）
uv run python -m pytest -q          # 单元测试（离线，不依赖外网）
uv run python scripts/smoke_test.py # 冒烟测试：启动 MCP、列工具、离线调用
```

真实数据检查脚本（会联网，不要放进 CI）：

```bash
uv run python scripts/check_real_data.py
uv run python scripts/check_financials.py AAPL annual 3
uv run python scripts/check_phase3_mcp.py
```

## 代码结构约定

| 目录 | 职责 |
| --- | --- |
| `providers/` | 只负责从外部源取数据，返回统一模型；不做计算与编排 |
| `services/` | 编排（provider 回退、缓存、组装结果） |
| `analysis/` | 公式、指标、技术指标、基本面规则，纯计算、不联网 |
| `models/` | Pydantic 模型，跨层共享 |
| `server.py` | 只做工具注册与异常兜底，不放业务逻辑 |
| `visualization/` | 只画图，不重算指标 |

## 新增数据源（Provider）

1. 在 `providers/` 新建模块，继承 `MarketDataProvider` 或 `FinancialDataProvider`。
2. 实现 `name`、`supports_quote` / `supports_history` / `supports_financials`、取数方法。
3. 失败一律抛 `ProviderError(provider, message, kind=...)`，`kind` 用 `rate_limit` / `timeout` / `network` / `http` / `parse` / `not_found` / `empty`。
4. 在 `config.py` 增加开关与顺序，在 `providers/__init__.py` 注册。
5. **不要**把无法验证的数据补 0，也不要为了通过测试伪造返回。
6. 新增来源后更新 `docs/data-sources.md` 与 README 数据源表，标清状态（available / throttled / unavailable）。

## 新增金融指标

1. 若只是新公式：在 `analysis/financial_formulas.py` 用注册器声明 `FormulaSpec`（名称、输入、表达式、单位、不可计算说明），并补 `tests/test_calculations.py`。
2. 若是「从报表直接算的业务指标」：在 `analysis/financial_ratios.py` 增加 `MetricSpec`，声明 `dimension`、`input_fields`、`build`、`requires_quote`、`limitations`，并补 `tests/test_financial_ratios.py`。
3. 更新 `docs/formula-reference.md` 与 `references/formula-reference.md`。

## 新增 MCP 工具

1. 在 `server.py` 的 `build_server()` 内用 `@mcp.tool()` 注册，函数名即工具名。
2. 参数用 `Annotated[..., Field(description="中文说明")]`，返回 Pydantic 模型。
3. 用户输入错误（`InputError`）要转成结构化 `unavailable` 结果，**不要直接抛异常给客户端**；未知异常记录日志后同样返回结构化错误。
4. 同步更新：`tests/test_tools.py` 的 `EXPECTED_TOOLS`、`tests/test_mcp_tools.py`、`skills/**/SKILL.md` 工具表、README 工具清单。

## 新增图表 / 报告类型

- 图表：在 `visualization/figures.py` 加绘图函数（只接受已算好的数据），在 `services/chart_service.py` 注册类型并补 `tests/test_visualization.py`。
- 报告：在 `services/report_service.py` 加 `_TEMPLATES` 条目与正文生成函数，确保包含来源、时间、口径、缺失标注与免责声明，并补 `tests/test_reports.py`。

## 提交规范

- 一个提交只做一件事；提交信息用 `feat:` / `fix:` / `docs:` / `test:` / `chore:` 前缀。
- 提交前跑：`uv run ruff check .`（必须 0 问题）与 `uv run python -m pytest -q`（必须 0 失败）。
- 不要提交 `.env`、密钥、Cookie、本机绝对路径或 `outputs/` 里的生成物。
- 不要为了目录整齐重写 Git 历史或删除他人分支。

## 数据与合规

- 只使用公开、免费、允许自动访问的接口；遵守其频率限制，不做代理池/IP 轮换等绕过手段。
- 已确认被限流的接口（东方财富 push2）保持停用，不要重新开启。
- 报告与图表中的所有数字必须能追溯到某次真实调用或测试 fixture。
