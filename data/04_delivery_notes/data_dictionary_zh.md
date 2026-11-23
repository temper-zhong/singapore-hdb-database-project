# HDB 处理后数据字典（v1）

## 数据模型

本版采用星型结构：`dim_*` 是维度表，`fact_*` 保留可追溯的明细或官方汇总记录，`mart_*` 是可直接回答研究问题的分析表。CSV 中日期统一为 ISO `YYYY-MM-DD`，缺失值为空白，布尔值为 `True/False`。

## 推荐数据库类型

| 逻辑类型 | CSV 表现 | MySQL / PostgreSQL 建议 |
|---|---|---|
| 主键、外键、计数 | 整数 | `BIGINT` / `INTEGER` |
| 年、月、楼层、剩余租期（月） | 整数 | `SMALLINT` / `INTEGER` |
| 月份 | `YYYY-MM-01` | `DATE` |
| 季度 | `YYYY-Qn` | `CHAR(7)` |
| 金额、面积、CPI、回报率 | 小数 | `DECIMAL(14,4)`；不要使用浮点数存最终财务值 |
| 类别、名称 | 文本 | `VARCHAR` |
| 标记 | `True/False` | `BOOLEAN` |

## 各表粒度与主要字段

| 表 | 一行代表 | 主键 | 主要字段与用途 |
|---|---|---|---|
| `dim_time_month` | 一个月 | `month_id` | 年、月、季度、是否完整年度/季度 |
| `dim_town` | 一个标准化 Town | `town_id` | `town_name`；`CENTRAL` 已统一为 `CENTRAL AREA` |
| `dim_flat_type` | 一个标准化户型 | `flat_type_id` | `flat_type`、房间数、核心租金分析标记 |
| `dim_block` | 一个标准化 block + street | `block_id` | 地址及 HDB Property Information 的楼龄、楼层、单位数等属性 |
| `dim_cpi_month` | 一个月 CPI | `month_id` | `cpi_all_items_2024_base`；2024=100 |
| `fact_resale_transaction` | 一条转售记录 | `resale_id` | 名义/2024 实值价格、每平方米价、面积、楼层、剩余租期、疑似重复标记 |
| `fact_rental_approval` | 一条出租批准记录 | `rental_approval_id` | 名义/2024 实值月租、地址维度、疑似重复标记 |
| `fact_quarterly_median_rent` | Town×户型×季度 | `median_rent_id` | 官方季度中位租金；`median_rent_available_flag=False` 表示原表为 `na` |
| `mart_town_flat_quarter` | Town×户型×季度 | `town_flat_quarter_id` | 转售中位数、每平方米价、官方中位租金、估算毛租金回报率 |
| `mart_block_flat_year_yield` | Block×户型×年 | `block_flat_year_id` | 同楼栋租金与转售匹配、剩余租期组、估算回报率及样本门槛标记 |

## 关键派生字段定义

- `price_per_sqm_nominal_sgd = resale_price_nominal_sgd / floor_area_sqm`。
- `real_2024_sgd = nominal_sgd × 100 / CPI`，表示按 2024 年价格水平调整。
- `estimated_gross_yield_pct = 中位月租 × 12 / 中位转售价 × 100`。这是估算毛回报率，未扣除空置、维修、税费和贷款成本。
- `remaining_lease_group`：`LT_60` 为不足 60 年，`Y60_TO_79` 为 60–不足 80 年，`GE_80` 为至少 80 年。
- `potential_duplicate_flag` 只标记原始字段完全相同的记录，不直接删除；原始数据没有单位号/交易 ID，相同记录可能对应不同真实单位。
- `analysis_eligible_flag=True` 表示同一 Block×户型×年同时至少有 3 条租赁和 3 条转售记录，并且属于完整年度 2021–2025。

## 完整字段类型

### `dim_time_month`

| 字段 | 类型 | 说明 |
|---|---|---|
| `month_id` | `DATE` | 月份主键，固定为每月 1 日 |
| `year` | `SMALLINT` | 年 |
| `month_no`、`quarter_no` | `SMALLINT` | 月序号、季度序号 |
| `quarter_id` | `CHAR(7)` | 例如 `2024-Q1` |
| `complete_year_flag`、`complete_quarter_flag` | `BOOLEAN` | 是否属于建议的完整比较期 |

### `dim_town`、`dim_flat_type`

| 字段 | 类型 | 说明 |
|---|---|---|
| `town_id`、`flat_type_id` | `SMALLINT` | 维度代理主键 |
| `town_name`、`flat_type` | `VARCHAR(40)` | 标准化类别名称 |
| `room_count` | `SMALLINT NULL` | 普通房型的房间数；Executive 等为空 |
| `core_rent_analysis_flag` | `BOOLEAN` | 3、4、5 ROOM 为真 |

### `dim_block`

| 字段组 | 类型 | 说明 |
|---|---|---|
| `block_id` | `BIGINT` | 维度主键 |
| `block`、`street_name`、`address_key` | `VARCHAR` | 标准化地址及连接键 |
| `max_floor_lvl`、`year_completed`、`total_dwelling_units` | `INTEGER NULL` | 最高层、建成年、总单位数 |
| `residential` 至 `precinct_pavilion` | `CHAR(1) NULL` | 原 HDB 属性的 Y/N 标记 |
| `bldg_contract_town` | `VARCHAR(10) NULL` | HDB 建筑合同 Town 代码 |
| `1room_sold` 至 `other_room_rental` | `INTEGER NULL` | 各户型出售/出租单位数 |
| `property_info_available_flag` | `BOOLEAN` | 是否匹配到 Property Information |

### `fact_resale_transaction`

| 字段组 | 类型 | 说明 |
|---|---|---|
| `resale_id` | `BIGINT` | 一条源记录的代理主键 |
| `month_id`、`quarter_id`、`year` | `DATE` / `CHAR(7)` / `SMALLINT` | 时间键 |
| `town_id`、`flat_type_id`、`block_id` | `BIGINT` | 外键 |
| `storey_range` | `VARCHAR(20)` | 原始楼层区间 |
| `storey_low`、`storey_high` | `SMALLINT NULL` | 楼层区间上下限 |
| `storey_mid`、`floor_area_sqm` | `DECIMAL(10,2)` | 中间楼层、面积 |
| `flat_model` | `VARCHAR(80)` | 户型模型 |
| `lease_commence_year`、`remaining_lease_months` | `SMALLINT` | 租约起始年、剩余月数 |
| `remaining_lease_group` | `VARCHAR(10)` | 三档剩余租期组 |
| 五个价格/CPI字段 | `DECIMAL(14,4)` | 名义价、每平方米价、CPI、2024 实值价格 |
| `property_match_flag`、`potential_duplicate_flag` | `BOOLEAN` | 地址匹配和疑似重复标记 |

### `fact_rental_approval`

| 字段组 | 类型 | 说明 |
|---|---|---|
| `rental_approval_id` | `BIGINT` | 源记录代理主键 |
| 时间键、三个维度外键 | `DATE` / `CHAR` / `INTEGER` | 月、季度、年、Town、户型、Block |
| `monthly_rent_nominal_sgd`、`cpi_all_items_2024_base`、`monthly_rent_real_2024_sgd` | `DECIMAL(14,4)` | 名义租金、CPI、2024 实值租金 |
| 两个标记字段 | `BOOLEAN` | 地址匹配与疑似重复 |

### `fact_quarterly_median_rent`

| 字段组 | 类型 | 说明 |
|---|---|---|
| `median_rent_id` | `BIGINT` | 主键 |
| `quarter_id`、`town_id`、`flat_type_id` | `CHAR(7)` / `INTEGER` | 复合分析粒度 |
| `median_rent_nominal_sgd` | `DECIMAL(14,4) NULL` | 官方中位租金；原值 `na` 时为空 |
| `source_rows_aggregated` | `INTEGER` | 标准化后合并的源行数 |
| `median_rent_available_flag` | `BOOLEAN` | 租金是否有效 |

### 两个分析 Mart

| 表/字段组 | 类型 | 说明 |
|---|---|---|
| 两个 `*_id` | `BIGINT` | 分析表代理主键 |
| `resale_n`、`rental_n` | `INTEGER` | 聚合所用样本数 |
| 所有 `median_*`、`quarter_cpi_*`、`estimated_gross_yield_pct` | `DECIMAL(14,4)` | 中位数、CPI、估算毛回报率 |
| `remaining_lease_group` | `VARCHAR(10)` | 年度 Block 表的租期分组 |
| `rent_core_scope_flag`、`yield_usable_flag`、`low_sample_flag`、`complete_*_flag`、`analysis_eligible_flag` | `BOOLEAN` | 分析筛选和质量标记 |
