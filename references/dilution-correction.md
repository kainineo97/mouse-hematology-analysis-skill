# PBS 稀释校正

当尾静脉血样本量不足、检测前用 PBS 稀释时使用本规范。稀释信息只能来自用户或实验记录，不能根据血常规读数反推。

## 分析前确认

必须询问本批是否存在稀释样本。若存在，逐条取得：

- `animal_id`：必须能唯一对应初始动物登记表。
- `timepoint`：必须是配置中的时间点标签，例如 `D1`。
- `dilution_factor`：直接乘到仪器读数上的数值校正乘数，必须是大于或等于 1 的数字。
- `metrics`：可选；留空表示本次所选全部指标，部分校正时用分号或逗号列出指标别名。
- `notes`：可选，记录 PBS 体积、血样体积或操作情况，但不从备注自动计算倍数。

使用 `sample_dilutions.csv`：

```csv
animal_id,timepoint,dilution_factor,metrics,notes
33,D7,2,,20 uL blood + 20 uL PBS
42,D14,5,WBC;Neu#;Lym#;Mon#,user-confirmed multiplier
```

`dilution_factor` 的定义是最终校正乘数。例如等体积血样与 PBS 混合时读数乘 2，应填写 `2`；不能填写 `1:1`，因为比例写法容易产生不同解释。若只提供 PBS 与血样体积而未确认乘数，先请用户确认，不自行换算后直接分析。

## 计算顺序

1. 保留分析仪原始文本为 `raw_value`。
2. 对稀释记录指定的指标计算 `corrected_value = raw_value × dilution_factor`；未指定稀释时倍数为 1。
3. 兼容字段 `value` 等于 `corrected_value`，宽表和展示表使用该值。
4. D0 与后续时间点均先校正，再计算百分比。默认 `%D0 = corrected_current / corrected_D0 × 100`。
5. GraphPad TSV 使用上述校正后百分比。

例如 D0 原始值 58、未稀释；D1 原始值 24、稀释倍数 2，则 D1 校正值为 48，`%D0 = 48 / 58 × 100 = 82.76`。

## 校验和停止条件

- 同一动物同一时间点只能有一条稀释记录；重复记录停止分析。
- 动物编号、时间点或指标不在本次配置中时停止分析，不能近似匹配。
- 倍数为空、非数值、小于 1，或使用 `1:1` 等比例字符串时停止并请用户给出直接校正乘数。
- 已登记稀释但没有唯一数值型原始结果时，不产生校正值，并写入 `DILUTION_NOT_APPLIED` QC。
- 有效稀释记录写入 `sample_dilutions_normalized.csv`，并在 QC 中记录 `DILUTION_CONFIGURED`；受影响的结果行添加 `DILUTION_APPLIED` 标记。
- 不修改分析仪 CSV，也不覆盖 `raw_value`。工作簿和报告必须同时展示原始值、倍数和校正后值，避免无法追溯。
