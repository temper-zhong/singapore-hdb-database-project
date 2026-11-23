# HDB 数据预处理代码运行说明

## 1. 输入文件

建立一个 `raw_data` 文件夹，并将五份源数据改成以下文件名：

| 文件名 | 内容 |
|---|---|
| `resale.csv` | 2017 年以来 HDB 转售交易 |
| `property.csv` | HDB Property Information |
| `rent_micro.csv` | Renting Out of Flats，从 2021 年开始 |
| `rent_median.csv` | HDB 各 Town、户型的季度中位租金 |
| `cpi_monthly.csv` | SingStat 月度 All Items CPI，2024=100 |

输入文件必须保留官方数据下载时的英文列名。

## 2. 运行命令

将 `preprocess_hdb_v1.py` 与 `raw_data` 放在同一文件夹，在该文件夹打开终端：

```bash
python preprocess_hdb_v1.py --raw-dir ./raw_data --output-dir ./processed_v1
```

Windows 如果没有 `python` 命令，可以使用：

```powershell
py preprocess_hdb_v1.py --raw-dir .\raw_data --output-dir .\processed_v1
```

## 3. 脚本执行的处理

1. 统一 Town、户型、Block 和 Street 的文本格式。
2. 建立 Town、户型、Block、月份和 CPI 维度表。
3. 把剩余租期转换成月，并划分为不足 60 年、60–79 年和至少 80 年。
4. 计算每平方米转售价。
5. 使用月度 CPI 将价格和租金转换成 2024 年实值。
6. 保留原始明细，不自动删除完全相同的记录，而是建立疑似重复标记。
7. 生成 Town×户型×季度分析表。
8. 在同一 Block、户型和年度内连接租金与转售数据，生成剩余租期及估算毛回报率分析表。
9. 检查主键唯一性、必要外键缺失和分析表重复粒度；验证失败时脚本会停止。

