# HDB 数据处理 v1：质量报告与使用说明

## 处理结果

- 转售事实表：242,144 行；地址属性匹配率 99.999%。
- 出租批准事实表：212,597 行；地址属性匹配率 99.907%。
- 官方季度中位租金表：13,145 行。
- Town×户型×季度分析表：4,894 行，其中核心户型且有有效租金的回报率行 2,578 行。
- Block×户型×年匹配表：50,529 行，其中达到双边各 3 条且为完整年度的可分析行 9,431 行。

## 质量处理

1. Town、户型、Block、Street 全部去首尾空格、统一大写和类别编码。
2. 转售剩余租期全部转换为月，并分为 `<60`、`60–79`、`≥80` 三组。
3. 原始完全相同记录没有直接删除：转售标记 635 行，租赁标记 3,200 行。后续可做“包含/排除疑似重复”的敏感性分析。
4. CPI 只更新至 2026-08，因此 2026-09 以后记录没有实值金额：转售缺 CPI 2,684 行，租赁缺 CPI 2,673 行。这些记录仍可做名义价格分析。
5. 所有维度主键、事实表主键、两个分析表复合粒度均通过唯一性检查；金额均为正数；事实表的 Town、户型、Block 外键均无缺失。

## 四个研究问题分别用什么数据

### 1. 不同户型的转售价和每平方米价格走势

- 首选：`mart_town_flat_quarter.csv`。
- 分组：`quarter_id`、`flat_type_id`，需要地区细分时再加入 `town_id`。
- 指标：`median_resale_price_nominal_sgd`、`median_price_per_sqm_nominal_sgd`；通胀调整后改用两个 `real_2024` 字段。
- 若需控制面积、楼层、模型、剩余租期或做回归，使用 `fact_resale_transaction.csv`。

### 2. 不同 Town 与户型的租金相对转售价比例

- 首选：`mart_town_flat_quarter.csv` 的 `estimated_gross_yield_pct`。
- 仅筛选 `yield_usable_flag=True`，核心结论集中在 3、4、5 ROOM；Executive 可补充，1、2 ROOM 租金样本太少。
- 该表采用官方 Town×户型季度中位租金，适合稳定的市场层面比较，不表示同一套房的真实投资回报。

### 3. 剩余租期较短组别的价格和估算回报率是否不同

- 价格部分：使用 `fact_resale_transaction.csv`，按 `remaining_lease_group` 分组，并控制 Town、户型、面积、楼层、季度。
- 回报率部分：使用 `mart_block_flat_year_yield.csv`，筛选 `analysis_eligible_flag=True`，比较三个 `remaining_lease_group`。
- 主分析期为 2021–2025；这是租赁明细与转售记录都完整覆盖的共同年度。

### 4. 扣除 CPI 后价格变化是否仍明显

- 趋势图：`mart_town_flat_quarter.csv`，对比名义价格字段和 `real_2024` 字段。
- 正式模型：`fact_resale_transaction.csv` 的 `resale_price_real_2024_sgd` 或 `price_per_sqm_real_2024_sgd`。
- 季度分析建议截止 2026-Q2；完整年度比较建议截止 2025。

## 解释边界

- 租赁明细是批准出租记录，月租由业主申报，不能视为经 HDB 核验的精确成交租金。
- 数据无法把租赁和转售匹配到同一套单位，因此回报率是同地区/户型/时期或同 Block/户型/年度的估算值。
- 剩余租期、租金与价格之间应报告为关联，不应表述为因果。
