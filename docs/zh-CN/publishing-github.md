# 发布到 GitHub

发布目录已经初始化为 `main` 分支的独立 Git 仓库。许可证为 MIT（见根目录 `LICENSE`），
仓库地址统一指向 `github.com/backward052/AnnoWeave`。

## 如果仓库地址变了

转移所有权、改仓库名或换组织之后，需要同步更新文档和元数据里的 URL：

```powershell
# 列出所有仍指向旧地址的位置
Get-ChildItem -Recurse -File -Include *.md,*.toml,*.ps1 |
    Where-Object { $_.FullName -notmatch '\\\.venv\\|\\build\\|\\dist\\|\\\.git\\' } |
    Select-String -Pattern 'github\.com/backward052' |
    ForEach-Object { "$($_.Path.Replace((Get-Location).Path + '\','')):$($_.LineNumber)" } |
    Sort-Object -Unique
```

需要覆盖的文件：`README.md`、`README_zh-CN.md`、`CONTRIBUTING.md`、`CHANGELOG.md`、
`pyproject.toml`、`docs/*/getting-started.md` 以及本文件。

不要动 `scripts/release_policy.ps1` 里那个 `github.com/OWNER/`：它是发布检查用来发现未替换占位符的
检测规则本身，改了门禁就失效了。

## 推送前本地复核

```powershell
cd <path-to-AnnoWeave>
.\scripts\check-release.ps1
.\scripts\setup.ps1
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
git status --short
git add .
git diff --cached --stat
git diff --cached
```

逐项阅读暂存差异。确认没有权重、素材、工作流、数据库、本机路径或业务参数后再提交：

```powershell
# 如果从未配置过 Git 身份，先执行这两行，否则 commit 会直接失败
git config user.name "你的名字"
git config user.email "你的邮箱"

git commit -m "Prepare AnnoWeave public source release"
```

`check-release.ps1` 以 Git 索引为准，因此本机 `.venv`、`build`、`dist` 不会造成误报；新增文件要先 `git add` 才会被检查。

## 连接空 GitHub 仓库

在 GitHub 创建一个**不自动生成 README、许可证或 `.gitignore`** 的空仓库，然后执行：

```powershell
git remote add origin https://github.com/backward052/AnnoWeave.git
git push -u origin main
```

若使用 SSH，把远程地址改为 `git@github.com:backward052/AnnoWeave.git`。推送后检查 GitHub
文件列表和 Actions，确认 3 个 Python 版本的测试矩阵与打包检查全部通过。

## 建议的仓库设置

- 开启 Secret Scanning、Push Protection 和 Dependabot alerts。
- 保护 `main`，要求 Pull Request 和 CI 通过后合并。
- 在仓库 About 区域填写描述、主题标签，并选择 MIT 许可证。
- 禁止直接上传模型、素材与工作流；大文件也不应改用 Git LFS 绕过发布边界。
- GitHub Release 只上传验收后的 `AnnoWeave` EXE 完整目录压缩包及校验值，不上传构建环境或测试数据。
