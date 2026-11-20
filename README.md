# 海洋数据挖掘技术课程材料

本仓库收录课程选题调研、课程报告和 AIS 窗口提前确认研究的可复现材料。整理日期：2026-11-20。

## 材料导航

| 内容 | 入口 |
| --- | --- |
| 课程笔记与选题调研 | [notes](notes/README.md) |
| 课程报告 | [reports](reports/README.md) |
| AIS 窗口提前确认研究 | [项目说明](projects/ais-early-confirmation/README.md) |
| 完整研究报告（Markdown） | [研究报告](projects/ais-early-confirmation/docs/完整研究报告.md) |
| 环境配置、单元测试与实验复现 | [复现说明](projects/ais-early-confirmation/REPRODUCIBILITY.md) |
| 数据来源与许可状态 | [项目数据与许可](projects/ais-early-confirmation/DATA_AND_LICENSES.md) |
| 上传 GitHub 的步骤 | [UPLOAD.md](UPLOAD.md) |
| 整理范围、来源与文件校验 | [PACKAGING.md](PACKAGING.md) |

## 目录

~~~text
marine-data-mining-course/
├── README.md
├── .gitignore
├── .gitattributes
├── UPLOAD.md
├── PACKAGING.md
├── notes/
│   ├── README.md
│   ├── 课程作业候选题清单.md
│   └── 第5讲课时2_水位线乱序容忍与迟到修订_前沿调研_2026-11-19.md
├── reports/
│   ├── README.md
│   └── 万子杰_海洋数据挖掘技术报告_1120.docx
├── projects/
│   └── ais-early-confirmation/
│       ├── README.md
│       ├── REPRODUCIBILITY.md
│       ├── DATA_AND_LICENSES.md
│       ├── requirements.txt
│       ├── analysis/
│       ├── docs/
│       ├── experiments/
│       ├── AIS漏计风险预实验/
│       ├── results/
│       ├── figures/
│       └── artifacts/
└── packaging/
    ├── copied_files.json
    └── validation.json
~~~

## 研究项目

项目研究多源 AIS 事件流中 COUNT(DISTINCT mmsi) 的提前确认，区分“计数数值已确定”与“身份集合已恢复”。具体实验设置、结果、失败场景和适用边界见项目说明与完整研究报告。

复现时先进入 projects/ais-early-confirmation，再执行项目复现说明中的命令。项目内部相对路径保留原布局。

## 材料与结果状态

课程调研材料按原文保留，文中“尚未开展实验”等表述属于调研时点的历史状态。当前研究进展以项目报告为准。

项目保留历史 848 项运行的结果摘要、固定样本和既有验证记录。此次整理只复制和核验文件，没有重新运行完整实验；既有验证记录中的本地路径属于历史元数据。

课程材料没有自动附加新的开源许可证。项目上游数据来源和许可原文随项目保留；代码和报告的许可尚待作者指定。
