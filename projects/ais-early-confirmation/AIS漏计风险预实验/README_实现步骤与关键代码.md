# AIS 未见船舶漏计风险：小规模预实验

本目录已实际完成数据获取、11 次模拟回放、全窗口标签复核及三组预测比较。目标是检查流程与初步信号，不是实现自适应水位线。

## 1. 数据与实验范围

选用 [MAPS Lab / Dalhousie University 的 AISdb Tutorials](https://github.com/MAPS-Lab/AISdb-Tutorials) 随附的 `example_data.db`。下载约 4.7 MiB，不需要下载全国 AIS 或安装 AISdb。

- 原数据：40,000 条动态位置记录、54 个 MMSI，2022-01-01 至 2022-01-20 01:58 UTC。
- 分析范围：2022-01-01 至 01-19，去掉最后一个不完整日历日；共 39,828 条、53 个 MMSI。
- 使用样本提供的全部空间范围，作为一个固定分析海区，不根据结果挑选子区。
- 样本是教程选取子集，数据库的 source 为 `Testing`。未证实完整区域覆盖、未经筛选的原始采集或原始真实发送时刻。
- 重用数据库 `time` 为模拟源端事件时间，再生成模拟交付延迟。它不是船载设备真实发送时间与真实接收时间的差。
- 只删除四字段完全相同的重复记录以及无效 MMSI/坐标，不对每艘船只保留一条。
- 原项目注明 AGPL-3.0；`data/source_LICENSE`、`data/source_CITATION.cff` 和原 README 已保存。引用：Gabriel Spadon (2026), AISdb Tutorials, MAPS Lab, Dalhousie University。

下载固定到提交 `bb978e03a8469184e421cec8927aae49aacfe8c7`，数据库 SHA-256 为 `f90b3dc64f02a879177d25e20a8266c803e098fce664971e24ed2b89687fd72e`。脚本首次下载和复用缓存均校验哈希。

本次与正式方案的差异：历史缩为 5 天，测试只有 4 天，历史档案每日 UTC 00:00 冻结并全天复用；这比逐窗口重新更新更保守。不能把该小样本结果当作 90 天正式实验结论。

## 2. 文件和运行步骤

|文件|作用|
|---|---|
|fetch_data.py|下载固定版本的小样本，以只读 SQLite 查询导出动态位置|
|pilot.py|生成到达时间、标签、无未来信息特征，并比较 M0/M1/M2|
|test_pilot.py|语义、边界、重现性与未来信息注入测试|
|summarize.py|汇总预测、标签、校准和分布变化，不重新调参|
|预实验结果.md|已经生成的实际结果|
|data/source_manifest.json|原始数据 URL、提交、大小与哈希|
|outputs/run_manifest.json|运行参数、版本、源数据与主脚本哈希|

在当前电脑上复现，先在 PowerShell 进入本目录，再执行：

```powershell
.\.venv\Scripts\python.exe fetch_data.py
.\.venv\Scripts\python.exe -m unittest -v test_pilot
.\.venv\Scripts\python.exe pilot.py
.\.venv\Scripts\python.exe summarize.py
```

已有缓存时，获取脚本不会重新下载。换机器首次安装时使用 Python 3.13：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

本机已验证 Python 3.13.12。依赖版本在 requirements.txt 和运行清单中。隔离环境仅供本预实验使用。

## 3. 获取与整理数据

下载器使用 Python 标准库 urllib，单次文件设 12 MiB 上限，并验证 SQLite 文件头和固定哈希。动态数据直接查询：

```python
SELECT mmsi, time, longitude, latitude FROM ais_202201_dynamic
```

不读取静态汇总表生成预测特征，因为这种汇总可能使用整个样本期。源文件在只读 SQLite 连接中查询，不执行数据库中的 pickle 字节等内容。

输出 `data/events.parquet`：

```text
event_id, mmsi, event_time, lon, lat
```

按事件时间、MMSI、纬经度稳定排序后分配 event_id；事件时间统一为 UTC。没有轨迹插值，也没有把未观测点补成实际报文。

## 4. 生成两种到达流

### 独立迟到 iid

核心实现位于 `simulate()`：

```python
slow = rng.random(len(d)) < 0.1
delay = np.where(slow,
                 rng.uniform(240, 1200, len(d)),
                 rng.uniform(0, 20, len(d)))
arrival = event_time + delay
```

90% 的报文延迟 0—20 秒，10% 延迟 4—20 分钟。延迟抽样不依赖 MMSI 的历史频率、当前真实船数或未来是否唯一。因此，没有把“历史特征必然有效”的结论直接写进生成规则。

### 集中补传 burst

用 SHA-256(MMSI) 的前四字节对 4 取模产生人工链路组，避免 Python 内置 hash 的进程随机化。每组每小时以 0.2 的概率暂停 20 分钟；开始位置在该小时前 40 分钟内均匀抽取。正常交付时刻落入暂停区间的报文统一释放：

```python
mask = (groups == group) & (original >= start) & (original < stop)
arrival[mask] = stop + schedule.uniform(0, 10, mask.sum())
```

`original` 是源端时间加 0—20 秒基础延迟。暂停与恢复时段由独立 PCG64 随机数流生成，报文数量变化不会改变故障日程。故障日程、queued 标记不进入预测特征。

最后统一按 `(arrival_s, event_id)` 排序，得到模拟处理顺序。种子固定为 11、22、33、44、55。另用全部延迟不超过 20 秒的 sanity 情景检查零漏计，共 11 次回放。

### 三种时间指标分开计算

- `out_of_order`：本报文事件时间小于此前已见最大事件时间。
- `behind_watermark`：事件时间小于此前最大事件时间减 180 秒，是诊断性事件迟到。
- `after_publish`：晚于该窗口结束后 180 秒的固定发布时刻到达。

本实验采用固定发布时刻计算预测标签，没有按水位线丢弃数据。不能将 after_publish 比例称为 Flink 实际丢弃率。sanity 中可以发生轻微乱序，但不应发生固定首发后的报文或船舶漏计。

## 5. 生成船舶漏计标签

固定 10 分钟窗口 `[s,s+600)`，首发 `T=s+600+180`，参考截止 `H=s+600+3600`。模拟最大交付延迟小于 60 分钟，因此参考截止能覆盖本窗口全部保留记录。

关键在于按“窗口、MMSI”求最早到达：

```python
first = stream.groupby(["window_start", "mmsi"]).arrival_s.min()
```

只要一艘船有一条有效报文在 T 前到达，它就已经计入。其余重复报文再迟到也不增加该窗口 distinct。

```text
seen_T = first_arrival <= T
seen_H = first_arrival <= H
U = count(seen_H) - count(seen_T)
y = int(U > 0)
```

所有时间窗口都保留，包括该教程子集没有记录的窗口。`raw_set_check()` 再用另一种直接集合差实现逐窗核对 `len(S_H - S_T)`，没有只检查一份 groupby 的内部一致性。历史从未出现的新 MMSI 同样计入标签。

## 6. 三组预测的输入

### M0：基线

固定 12 个特征，包括当前已收报文数、当前 distinct、当前单条报文船舶比例、最近一分钟到达数、源时间滞后、日内正余弦和星期、历史海区同类时段总体船数，以及已成熟历史延迟的比例和分位数。

### M1：真实历史

在 M0 上增加 5 个实体历史特征。每日 00:00 用以下前缀冻结档案：

```python
h = stream[(stream.event_s >= cut - 5*86400)
           & (stream.event_s < cut)
           & (stream.arrival_s <= cut)]
```

最后一个条件尤其重要：原文件里虽然存在某条历史事件，但如果模拟中尚未到达，该记录也不能进入档案。

对每艘历史船，计算过去五天同一 UTC 十分钟时段的出现比例 f，以及相邻已观察历史记录间隔的中位数 g。对当前尚未计入、且该时段历史出现过的候选船，增加：候选数、f 之和、log(1+g) 的中位数、g 不可用比例和无 g 指示变量。

### M2：置换历史

在同一天、同一份历史已知名册内，把整份 `(f向量,g)` 随机重新对应到 MMSI，再计算同维度特征。真实报文、到达时间和标签不变。它检查历史身份对应关系与额外模型容量，不能单独视为严格的条件独立性检验。

模型用明确列白名单，而不是“除 y 外所有数值列”：

```python
model.fit(train[BASE_FEATURES + HISTORY_FEATURES], train.y)
```

因此保存表中的 `truth_ships`、`missed_ships` 等未来标签字段不会自动进入模型。模型不接收原始 MMSI 或暂停标记。

## 7. 时间切分与训练

|日期|用途|
|---|---|
|1月1—5日|5 天历史预热|
|1月6—11日|训练，864 窗口|
|1月12日|隔离|
|1月13—14日|验证，288 窗口|
|1月15日|隔离|
|1月16—19日|测试，576 窗口|

代码断言训练标签在验证预测前成熟，验证标签在测试预测前成熟。每日历史前缀允许使用模拟部署时已经到达的前期测试报文，但不使用尚未到达的未来报文或标签。

三组均采用 `StandardScaler + LogisticRegression`，C 只从 `[0.01,0.1,1,10]` 中按验证 Brier 选择；标准化只拟合训练集，不做类别重采样，不在测试集调参。

主指标为 Brier loss，越低越好。结果另给出训练期阳性率常数预测参考、AUC、平均精度、概率校准表及逐日配对损失。常数参考只用于检查预测器是否具有基本价值，不改变三组信息对照。

## 8. 校验、输出和解读

四个语义测试已通过：

1. 已见船重复迟到不增加 distinct；未见船迟到会增加 distinct。
2. 左闭右开窗口，以及恰好等于首发截止的到达边界。
3. 两种模拟可按种子重现，最大延迟界和 sanity 零漏计成立。
4. 加入事件时间在过去、但交付时间在未来的记录，不改变当时的预测特征。

所有回放窗口另外通过直接集合差复核。这些检查覆盖重要语义，但不能证明不存在任何实现错误。

关键输出：

- `simulation_audit.csv`：乱序、水位线后报文、首发后报文、补传比例。
- `label_counts.csv`：每个情景、种子、切分的阳性窗口与漏计船窗口数。
- `arrivals_*.parquet`：模拟到达表，含事件标识，方便复现。
- `features_*.csv`：所有特征及标签；训练代码只读取白名单。
- `metrics.csv`、`test_predictions.csv`：逐种子三组结果和每个测试窗口的概率。
- `calibration.csv`：概率分箱校准。
- `constant_prior_reference.csv`：训练期常数概率参照。
- `distribution_shift_diagnostic.csv`、`empty_window_diagnostic.csv`：分布变化和空记录窗口诊断。
- `paired_by_day.csv`：相同测试日期的配对 Brier 改善。
- `file_hashes.json`：脚本及关键输出哈希。

实际结果见预实验结果.md：技术流程和非零标签已验证，但未观察到历史特征稳定改善。集中补传下模型对空记录窗口严重失准；训练没有此类窗口，测试却有 117 个。空记录不能解释为现实海区没有船。当前证据不足以断言历史信息有效或无效。

后续如扩大数据、改变历史尺度、增加模型或修正分布处理，应作为新一轮实验保留新的测试日期；不能在本次测试集反复试到显著后继续称其为独立测试。五个随机种子也不能当成五份独立的真实交通样本。
