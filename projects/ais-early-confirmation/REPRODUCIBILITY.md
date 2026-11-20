# 复现说明

## 1. 环境

原运行环境为Windows 11、Python 3.13.12。requirements.txt固定numpy 2.4.6、pandas 3.1.1、pyarrow 26.0.1、matplotlib 3.10.9。下面Windows命令按此目录布局编写；Linux/macOS命令需改虚拟环境路径，本机未验证这些操作系统。中文图默认Microsoft YaHei，其他平台需安装中文字体或将字体列表改为本地Noto Sans CJK等字体，不隐藏缺字警告。

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

若下载仓库前需要clone，请先填写真实GitHub地址。此草案不伪造可用远端URL。

## 2. 先复现图表（不重跑协议）

```powershell
.venv\Scripts\python.exe -X utf8 analysis/make_figures.py --summary-dir results --accuracy-input results/figure_tables/accuracy_counts.csv --failure-input results/figure_tables/failure_brier.csv --output reproduced_report
```

输出9图×PNG/PDF/SVG、作图表、图注和哈希。2240行accuracy_counts是原每窗误差核对后导出的计数表，不是模拟生成的新结果。图表复现不能冒充完整848项协议重跑。

## 3. 单元与smoke

```powershell
cd experiments
..\.venv\Scripts\python.exe -X utf8 run_tests.py --label reproduced
..\.venv\Scripts\python.exe -X utf8 run_experiments.py --phase smoke --output outputs/reproduced_smoke
..\.venv\Scripts\python.exe -X utf8 check_results.py outputs/reproduced_smoke --save validation/reproduced_smoke.json
..\.venv\Scripts\python.exe -X utf8 inspect_smoke.py outputs/reproduced_smoke --save validation/reproduced_smoke_details
```

38项测试包含200组随机多源排列的一项属性测试；smoke为8方法×144窗口。初次临时发布延迟均0，检验最终确认、迟到修订及字节计费。不得为让测试通过删除空窗或调Δ、种子、复制率。输出目录不可覆盖。

## 4. 完整848矩阵

完成smoke和校验后，在experiments运行：

```powershell
..\.venv\Scripts\python.exe -X utf8 run_experiments.py --phase all --resume --output outputs/reproduced_smoke
..\.venv\Scripts\python.exe -X utf8 check_results.py outputs/reproduced_smoke --save validation/reproduced_matrix.json
..\.venv\Scripts\python.exe -X utf8 summarize_results.py outputs/reproduced_smoke --save reports/reproduced_matrix
```

矩阵为8 smoke＋560 replay＋160 pressure＋120 faults。--resume只跳过相同源码哈希且已完整保存audit的运行；不接受另一代码版本的结果。遇到FAILED或不完整目录应保留，查明后使用新目录或明确的修复替代清单。不能删除失败后宣布全通过。

冻结实验参数和两份CPU校准文件已提供；不要在复现中重新校准再把不同延迟当同一实验。墙钟耗时和进程峰值可变，协议逻辑时间/计数/网络字节才是主要比较对象。换机新校准应作为另一个运行版本，保留原系数。

## 5. 历史版本与本仓库验证范围

原848项曾全部运行于v1；只把采集封闭界违约故障中的WM/TIMER共6项用v2替代。其余方法和正常场景分支不变，并有修复后8组smoke的72份文件字节等价验证。source_versions/v1、v2和results/run_index.csv记录来源。

此目录源码与实验v2的src/tests相同，打包只规范57文件索引中的路径分隔符，不改变57个文件内容或协议哈希。历史audit中的绝对路径是原执行位置，不是可在本机直接访问的下载地址。完整原逐包文件未包含；其归档链接：[待填写]。

本次打包目录中的实际单元、smoke和等价验证见artifacts/package_validation.json。它不表示完整矩阵已在该目录再次执行。

## 6. 数据获取与检查

固定样本example_data.db、events.parquet及来源许可已在AIS漏计风险预实验/data提供，离线可运行。数据库SHA256：f90b3dc64f02a879177d25e20a8266c803e098fce664971e24ed2b89687fd72e。

原始固定下载地址：https://raw.githubusercontent.com/MAPS-Lab/AISdb-Tutorials/bb978e03a8469184e421cec8927aae49aacfe8c7/example_data.db

需要重新下载/导出时，在单独副本使用保留的fetch_data.py并核查SHA；不要覆盖已审计发布副本。源时间不是经核实的真实接收时间；四接收源、传输、封闭信号与故障为模拟。复盘旧预测模型另需旧目录requirements；核心协议和从摘要重画图不需要训练预测模型。
