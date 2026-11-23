HDB小组研究交接说明

一、交付内容
01_source_code
  preprocess_hdb_v1.py
    完整、独立的数据预处理程序。输入五份官方CSV，输出本项目使用的10张处理表。
  RUN_PREPROCESSING_zh.md
    Python环境、输入文件命名、运行命令及处理步骤。
  requirements.txt
    Python依赖：pandas、numpy。

02_dataset_links
  dataset_links.txt
    五份官方数据的名称、链接、用途、文件名和本次快照范围。

03_processed_data
  dim_time_month.csv
    月份与季度维度，118行。
  dim_town.csv
    Town维度，28行。
  dim_flat_type.csv
    户型维度，7行。
  dim_block.csv
    Block和楼栋属性维度，13,376行。
  dim_cpi_month.csv
    月度All Items CPI，116行。
  fact_resale_transaction.csv
    转售交易事实表，242,144行。
  fact_rental_approval.csv
    出租批准事实表，212,597行。
  fact_quarterly_median_rent.csv
    官方Town×户型×季度中位租金，13,145行。
  mart_town_flat_quarter.csv
    Town×户型×季度主分析表，4,894行。
  mart_block_flat_year_yield.csv
    Block×户型×年度剩余租期和回报率分析表，50,529行。

04_delivery_notes
  HANDOVER_README_zh.txt
    本交接说明。
  data_dictionary_zh.md
    所有表的粒度、字段、数据类型和派生指标定义。
  processing_quality_report_zh.md
    样本量、匹配率、缺失值、质量检查和研究问题的数据对应关系。

二、建议分析入口
问题1：不同户型的转售价和每平方米价格走势
  默认使用mart_town_flat_quarter.csv。
  需要控制面积、楼层、户型模型和剩余租期时，使用fact_resale_transaction.csv。

问题2：不同Town和户型的租金相对转售价比例
  使用mart_town_flat_quarter.csv。
  筛选yield_usable_flag=True，核心结论集中于3_ROOM、4_ROOM和5_ROOM。

问题3：较短剩余租期是否呈现不同价格和回报率
  价格部分使用fact_resale_transaction.csv。
  回报率部分使用mart_block_flat_year_yield.csv，筛选analysis_eligible_flag=True。
  推荐主比较期为2021至2025完整年度。

问题4：扣除CPI后，转售价格变化是否仍然明显
  使用mart_town_flat_quarter.csv中的real_2024字段。
  季度比较建议截止2026-Q2，完整年度比较建议截止2025。

三、关键指标定义
每平方米名义价格 = 转售价 / 建筑面积平方米
2024年实值金额 = 名义金额 × 100 / 当月CPI
估算毛租金回报率 = 中位月租 × 12 / 中位转售价 × 100%

剩余租期分组：
  LT_60：不足60年
  Y60_TO_79：60年至不足80年
  GE_80：至少80年

四、运行源代码
1. 安装Python 3.10或以上版本。
2. 安装依赖：python -m pip install -r requirements.txt
3. 将五份官方源文件放在raw_data文件夹，并使用以下文件名：
   resale.csv
   property.csv
   rent_micro.csv
   rent_median.csv
   cpi_monthly.csv
4. 运行：
   python preprocess_hdb_v1.py --raw-dir ./raw_data --output-dir ./processed_v1

五、质量检查结果
1. Town、户型和Block外键无缺失。
2. 事实表代理主键唯一，两个分析表的复合粒度唯一。
3. 转售记录与Property Information地址匹配率为99.998761%。
4. 出租记录与Property Information地址匹配率为99.907336%。
5. Block×户型×年度表中，有9,431组同时满足租赁和转售各至少3条、且属于2021至2025完整年度。
6. 疑似重复记录未直接删除；使用potential_duplicate_flag标记，以便后续敏感性分析。
7. 2026-09及以后月份因本次CPI快照尚无数据，实值金额为空，但名义金额仍可使用。

六、研究解释边界
1. 回报率为市场层面或同Block层面的估算值，并非同一套单位的实际投资回报。
2. 毛回报率未扣除空置、维修、税费、管理费和融资成本。
3. HDB出租批准表中的月租由业主申报，不能视为经过HDB核实的精确成交租金。
4. 剩余租期、租金与转售价之间的结果应表述为关联，不应直接表述为因果。
5. 1_ROOM和2_ROOM的有效租金样本较少，不建议作为租金回报率的核心结论。

