# 工作簿交付规范

只有用户需要 Excel/Google Sheets 形式时使用本规范。先运行分析脚本并解决关键 QC，再通过可用的电子表格工作流创建工作簿；不要直接修改分析仪原始 CSV。

## 工作表顺序

1. `README`：项目名、生成时间、D0 定义、百分比公式、死亡日边界、输入哈希摘要和状态图例。
2. `Animal Registry`：导入 `animal_registry_normalized.csv`。
3. `Dilutions`：导入 `sample_dilutions_normalized.csv`；即使为空也保留表头，明确本批是否存在稀释校正。
4. `Group Summary`：导入 `group_summary.csv`。
5. `QC`：导入 `qc_issues.csv`；按 severity 筛选，不能只靠颜色传达严重度。
6. `Long Results`：导入 `results_long.csv`，保持单行表头、单元格单一数据类型。
7. 每个指标一个展示表：需要可直接粘贴到既有表格时，依据 `<metric>_copy_ready.csv` 制作且不加入状态列；需要同页审计时，改用 `<metric>_wide.csv`。
8. 每个指标一个 `GraphPad <metric>` 表：导入 `graphpad_<metric>.tsv`，保持可以连续框选复制的矩形，不在数据块中插入说明、空行或汇总公式。

原始测量需随工作簿交付时，将 `source_values.csv` 放入 `Source Values`，并显示 `raw_value / dilution_factor / corrected_value`；如需保存分析仪全部 54 列，则每个时间点单独导入只读的 `Raw <timepoint>` 表，不要在原始表上清洗或写公式。

## 指标展示表

- 前六列保持 `Cage / Tag / Animal ID / Group / Notes / Death date`，冻结标题行和这些标识列。
- 展示表中的测量值使用 `corrected_value`；未稀释样本与原始值相同。README 必须写明稀释校正公式和本批稀释记录数。
- 可以在审计展示表中沿用“日期/时间点 + value/%D0/status”分组表头；复制粘贴版只保留 `value/%D0`。机器可读的 `Long Results` 必须保持单行表头。
- D0 不需要重复显示 `%D0`；后续时间点的 `%D0` 必须是工作簿公式，例如 `=IFERROR(current_value/D0_value*100,"")`，显示为普通数值 `0.00`，与参考截图一致。不要再套 Excel 百分比格式，否则会放大 100 倍。
- 若用户选择真正相对变化率，公式改为 `=(current_value-D0_value)/D0_value*100`，列名必须为 `delta_pct_D0`。
- 审计展示表应在相邻状态列显示 `DEAD`、`MISSING_SAMPLE`、`MISSING_METRIC`、`DUPLICATE_SAMPLE` 和 `POST_DEATH_DATA`。复制粘贴版可以省略状态列，但对应状态必须保留在 `Long Results`、`QC` 或审计宽表中；缺失值仍为空白。可以额外用条件格式把死亡后区域标成灰色，但颜色不能代替审计表中的状态文本。
- 分组配色优先匹配用户参考；没有参考时使用低饱和浅色，并在 `Group` 列保留组名。不要把颜色作为分组的唯一编码。
- 计数使用整数格式，连续测量保留原始有效精度，D0 比例保留两位小数，日期用 `yyyy-mm-dd`。

## GraphPad 工作表

- 按 [graphpad-prism-layout.md](graphpad-prism-layout.md) 保持一个 X 时间列、每组一个数据集、动物为并排重复子列。
- 组块按配置中的 `group_order` 从左到右排列；未配置时沿用初始登记表中组别首次出现的顺序。组内动物顺序不得随时间变化。
- GraphPad 百分比必须来自稀释校正后的值，不得绕过 `sample_dilutions_normalized.csv` 重新从原始读数计算。
- 第一行显示组名并在同一组范围内合并单元格；第二行显示动物 ID。组名合并只用于工作簿显示，底层 TSV 仍以组名后接空单元格表示同一矩形布局。
- 数据区只放数值或空白，不放状态缩写、破折号、`NA`、死亡日期或备注，确保可直接粘贴到 Prism。
- 冻结前两行和 X 列；数值显示两位小数。不要删除较小分组用于对齐的尾部空列。

## 验证

- 检查所有工作表的关键值和公式；扫描 `#REF!`、`#DIV/0!`、`#VALUE!`、`#NAME?`、`#N/A`。
- 手算核对至少两个 `%D0`，其中一个结果大于 100%。
- 渲染并查看所有工作表，修复标题、状态、数字或日期被截断的问题。
- 对照 `run_manifest.json` 核对动物数、时间点数、指标数和 QC 计数；不得因制作工作簿改变原始测量值。
