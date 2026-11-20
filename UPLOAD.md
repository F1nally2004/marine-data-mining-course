# 上传 GitHub

## 1. 新建 Repository

打开 https://github.com/new ，仓库名可填写 marine-data-mining-course，整理阶段可以先选择 Private。

这里要创建的是 Repository（文件仓库）。Create project 页面中的 Table、Board、Roadmap 是任务管理看板，与上传文件的仓库不同。

创建空仓库，不自动添加 README、.gitignore 或许可证；本文件夹已准备首页和忽略规则，许可证留待作者选择。

## 2. 本地提交

在本文件夹打开 PowerShell。以下命令需要本机可用的 Git；将姓名、邮箱替换为自己的信息。

~~~powershell
git init -b main
git config user.name "你的名字"
git config user.email "你的邮箱"
git add .
git diff --cached --stat
git status --short
~~~

确认暂存清单包含预期材料后提交：

~~~powershell
git commit -m "整理海洋数据挖掘技术课程材料"
~~~

## 3. 连接并上传

复制 GitHub 新仓库页面的 HTTPS 地址，将下方示例地址替换为实际地址。按 Git 客户端提示完成 GitHub 登录。

~~~powershell
git remote add origin https://github.com/你的用户名/marine-data-mining-course.git
git push -u origin main
~~~

上传后确认首页能打开、课程报告可下载、项目图表和复现说明可访问，再把实际仓库链接填入研究项目 README 中的对应字段。

研究项目保留了原发布草案中的作者、许可和完整日志归档地址占位说明；按真实情况补充，尚未提供的内容不要填成已完成。

## 4. 后续更新

~~~powershell
git add .
git diff --cached --stat
git commit -m "更新课程材料"
git push
~~~

官方参考：https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github?platform=windows
