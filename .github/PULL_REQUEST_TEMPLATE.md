## 这个 PR 做了什么

<!-- 一两句话说明改动目的，不要只写"修复问题" -->

## 关联 Issue

<!-- 例如 Closes #12 -->

## 改动范围

- [ ] 数据源 / Provider
- [ ] 公式或指标计算（`analysis/`）
- [ ] 基本面 / 技术面分析
- [ ] 图表（`visualization/`、`chart_service.py`）
- [ ] 报告（`report_service.py`）
- [ ] MCP 工具（`server.py`）
- [ ] Skill / 文档 / 示例
- [ ] 测试 / CI

## 自查清单

- [ ] `uv run pytest -q` 全绿（新增逻辑有对应用例，不只检查"不报错"）
- [ ] `uv run ruff check .` 通过
- [ ] 新增的数据字段有币种、单位、报告期与来源，缺失时不用 0 填充
- [ ] 图表的指标来自 `analysis/`，**没有在绘图代码里另算一套**
- [ ] 文档（README / docs / SKILL.md）与实际行为一致
- [ ] 没有提交 `.env`、Token、Cookie、本机绝对路径或 `outputs/`、`artifacts/` 产物

## 截图或输出片段

<!-- 图表改动请附 PNG；报告改动请附节选；数据改动请附真实返回片段 -->
