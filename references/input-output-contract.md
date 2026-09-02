# 输入、状态与输出契约

## 1. 初始动物表

使用 CSV；推荐 UTF-8 with BOM，便于 Excel 直接打开。列名可用英文或脚本支持的中文同义词。

| 规范列 | 必填 | 含义 |
|---|---:|---|
| `cage` | 是 | 笼号，作为文本 |
| `tag` | 是 | 笼内耳标/尾标，作为文本；默认 0–5 |
| `animal_id` | 条件 | 默认可留空，由 `cage + tag` 生成；`animal_id_mode=registry` 时必填 |
| `group` | 是 | 初始随机分组 |
| `notes` | 否 | 注射、混样或其他备注 |
| `death_date` | 否 | ISO 日期 `YYYY-MM-DD`；空白表示未记录死亡 |

默认 `animal_id_mode` 为 `cage_tag_concat`。此模式下，若用户同时填写 `animal_id`，它必须与拼接值相同。ID 必须唯一；脚本遇到重复 ID、重复笼号+耳标或空分组时停止。

## 2. 项目配置

`analysis_config.json` 的最小可用示例：

```json
{
  "project_name": "example-study",
  "animal_registry": "animal_registry.csv",
  "sample_dilutions": "sample_dilutions.csv",
  "baseline_timepoint": "D0",
  "animal_id_mode": "cage_tag_concat",
  "max_tag_number": 5,
  "group_order": ["PBS", "Drug"],
  "death_date_inclusive": true,
  "percent_mode": "percent_of_baseline",
  "sample_id_column": "样本编号",
  "date_column": "日期",
  "excluded_sample_ids": ["Background"],
  "metrics": ["PLT", "WBC"],
  "timepoints": [
    {
      "label": "D0",
      "day": 0,
      "collection_date": "2026-04-23",
      "sources": ["raw/D0.csv"]
    },
    {
      "label": "D1",
      "day": 1,
      "collection_date": "2026-04-24",
      "sources": ["raw/D1.csv"]
    }
  ]
}
```

相对路径以配置文件所在目录为基准。每个时间点可以用 `source` 指定一个文件，或用 `sources` 指定多个文件。`collection_date` 可留空；只有当该时间点原始行的 `日期` 能唯一解析为同一天时，脚本才会自动推断。

`group_order` 可省略或设为空数组，此时按初始动物表中组别首次出现的顺序输出。若设置，必须完整且不重复地列出登记表中的所有组；它只改变 `group_summary.csv` 和 GraphPad 组块顺序，不改变组内动物顺序。

`sample_dilutions` 可省略或留空，表示本次没有需要校正的稀释记录；若指定文件，按 [dilution-correction.md](dilution-correction.md) 读取。新建项目模板会同时创建只有表头的 `sample_dilutions.csv`，无稀释时保持其为空即可。

支持的默认短别名包括：`WBC`、`Neu#`、`Lym#`、`Mon#`、`Eos#`、`Bas#`、`Neu%`、`Lym%`、`Mon%`、`Eos%`、`Bas%`、`RBC`、`HGB`、`HCT`、`MCV`、`MCH`、`MCHC`、`RDW-CV`、`RDW-SD`、`PLT`、`MPV`、`PDW`、`PCT`、`P-LCC`、`P-LCR`。精确表头优先；找不到或匹配不唯一时停止。

## 3. 编码与原始数据

脚本按 `UTF-8 BOM → UTF-8 → GB18030` 顺序解码，能够读取示例分析仪的 GBK/GB18030 CSV。多行消息字段由标准 CSV 解析器处理。空表头和重复表头会被安全重命名用于内部读取，但原始文件不会被改写。

每个输入文件的绝对路径、SHA-256、编码、记录数和采集日期写入 `run_manifest.json`。`source_values.csv` 保留选中指标的原始单元格文本、稀释倍数、校正后值、来源文件和逻辑记录号，用于追溯。分析使用的 `value` 是校正后值；未稀释时倍数为 1，因此与原始数值相同。

## 4. 状态定义

| 状态 | 含义 | 是否计算 `%D0` |
|---|---|---:|
| `OBSERVED` | 唯一匹配、数值有效、且不在死亡后 | 是，D0 有效且非 0 时 |
| `DEAD` | 采集日已达到/超过死亡日，且无原始测量 | 否 |
| `MISSING_SAMPLE` | 按死亡记录仍应存活，但原始文件无此样本 | 否 |
| `MISSING_METRIC` | 找到样本行，但该指标单元格为空 | 否 |
| `NON_NUMERIC` | 指标单元格非空但无法严格解析为数值 | 否 |
| `DUPLICATE_SAMPLE` | 同一时间点同一动物有多条候选行 | 否；不得自动取首条或平均 |
| `POST_DEATH_DATA` | 死亡日或之后仍出现原始测量 | 否；保留数值和来源供人工裁决 |

缺失不等于 0。百分比统一保留两位小数并按常规四舍五入。默认先做 `corrected = raw × dilution_factor`，再做 `%D0 = corrected_current / corrected_baseline × 100`；没有稀释时 `dilution_factor=1`。

## 5. 产物

| 文件 | 用途 |
|---|---|
| `animal_registry_normalized.csv` | 规范化后的动物主表 |
| `sample_dilutions_normalized.csv` | 经验证的稀释记录、适用指标和直接校正倍数 |
| `group_summary.csv` | 每组初始动物数与动物编号 |
| `source_values.csv` | 选中指标的原始值和来源索引 |
| `results_long.csv` | 一行一个动物×时间点×指标，适合统计分析 |
| `<metric>_wide.csv` | 每个指标一个按动物和时间点展开的审计表，保留状态列 |
| `<metric>_copy_ready.csv` | 与宽表相同的值和百分比，但省略状态列，便于粘贴到现有表格 |
| `graphpad_<metric>.tsv` | 每个指标一个按时间、组别和动物重复子列排列的 Prism XY 百分比表 |
| `qc_issues.csv` | 错配、缺失、重复、基线和日期问题 |
| `run_manifest.json` | 配置快照、公式、输入哈希和计数 |

`results_long.csv` 同时包含 `raw_value`、`dilution_factor`、`corrected_value` 和兼容字段 `value`；其中 `value` 等于 `corrected_value`。宽表的固定列为笼号、耳标、动物 ID、分组、备注和死亡日期。D0 有校正后的 `value/status`；后续时间点有校正后的 `value/pct_D0/status`。审计产物中的状态列不可省略或仅用颜色代替；`<metric>_copy_ready.csv` 是专门的无状态复制版，状态仍可在 `results_long.csv`、`<metric>_wide.csv` 和 `qc_issues.csv` 中追溯。

GraphPad TSV 使用 UTF-8 BOM 和制表符分隔。首列为数值型时间 X；组别按 `group_order`（若提供）或初始登记表首次出现顺序排列，每组使用相同数量的重复子列，数量取最大组初始动物数。动物按登记表顺序固定在同一子列，较小分组的多余子列以及死亡、缺样、无有效 D0 的值均留空。具体粘贴结构见 [graphpad-prism-layout.md](graphpad-prism-layout.md)。
