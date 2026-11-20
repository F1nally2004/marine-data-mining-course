# 整理范围与校验

整理日期：2026-11-20。

## 来源

所有源路径均相对于原课程目录，本次整理采用复制方式。

| 源位置 | 本仓库位置 |
| --- | --- |
| 选题调研/课程作业候选题清单.md | notes/，保留原名 |
| 选题调研/第5讲课时2_水位线乱序容忍与迟到修订_前沿调研_2026-11-19.md | notes/，保留原名 |
| 万子杰_海洋数据挖掘技术报告_1120.docx | reports/，保留原名 |
| AIS窗口提前确认研究/publication/github_repo/ | projects/ais-early-confirmation/ |

研究项目内部目录和纳入文件的字节保持不变，包括原 .gitignore、.gitattributes、固定样本、数据来源许可、历史源码、结果摘要和验证记录。

两份课程调研笔记中指向本机磁盘的课件链接，改为 notes/COURSE_REFERENCES.md 的引用说明。只更改链接目标，原文内容与外部文献链接保留。清单同时保存原文件哈希和整理后哈希。

## 排除范围

- 项目中的 experiments/outputs、reproduced_report 和 artifacts/raw_runs（如存在），它们属于可重建输出。
- Python 缓存、虚拟环境、Matplotlib 缓存、Git 元数据与临时文件。
- 原课程目录中的完整实验 outputs、旧报告和文档改写工作目录。
- 教师课件、教材、视频、他人报告、题库及课件全文索引。
- AI 聊天记录；若后续需要作为作业附件提交，可另行整理后添加。

预实验目录内受保护的 outputs 保留，不能与主实验生成目录一起排除。项目提供小样本与摘要；完整 848 项逐包日志仍按原项目说明单独处理。

## 校验

packaging/copied_files.json 记录每个复制文件的相对来源、目标、字节数和 SHA256。packaging/validation.json 记录本次目录整理的实际校验结果。

验证包括复制前后哈希一致性、57 个受保护原文件的历史哈希、Markdown 本地链接、忽略规则和文件大小。验证过程中使用临时 Git 元数据检查待上传清单，新文件夹本身不初始化 Git、不创建远端仓库。

上游 source_README.md 是受保护的历史原文，其中 12 个相对链接指向上游教程仓库内的笔记本和原始许可文件，没有把完整教程仓库一起复制。本地许可和引用信息使用 source_LICENSE、source_CITATION.cff 文件名保留。阅读完整教程可访问 https://github.com/MAPS-Lab/AISdb-Tutorials/tree/bb978e03a8469184e421cec8927aae49aacfe8c7 。这些继承链接单独记录为历史例外，不计作课程导航链接通过。

项目 artifacts 中的测试和实验记录来自此前的研究发布草案。本次未重跑科学实验，也未修改报告内容或替作者指定许可证。
