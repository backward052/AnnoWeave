# GitHub 发布检查

## 可以提交

- `src/annoweave` 源码与品牌资源
- `docs`、`examples`、`tests`
- `pyproject.toml`、两个 README、`CHANGELOG.md`、`LICENSE`、CI、脚本和 PyInstaller spec

## 不可提交

- 生产或测试模型权重及其哈希清单
- 真实图片、视频、裁剪图、标注集和导出结果
- 现有工作流 JSON、运行快照、用户会话或项目数据库
- 本机绝对路径、用户名、网络地址、令牌、证书和 `.env`
- `.venv`、`build`、`dist`、缓存、日志和设计评审临时文件
- 能推断生产流程的模型名、类别组合、阈值、路由或专用关联代码

## 每次推送前

```powershell
.\scripts\check-release.ps1
python -m pytest
python -m ruff check src tests
git status --short
git ls-files
```

`check-release.ps1` 的规则定义在 `scripts/release_policy.ps1`，并且只检查 **Git 索引**：
本机 `.venv`、`build`、`dist` 会被忽略，而所有会被推送的文件都会被检查。新增文件需要
先 `git add`。同一份规则也被 `tests/test_release_policy.py` 使用，所以发布门禁和测试
套件不会出现分歧。

之后仍需人工阅读 `git diff --cached`，确认没有大文件或本机路径。

## 发布新版本

1. 把 `CHANGELOG.md` 中 `Unreleased` 段落的内容移到新版本号与日期之下。
2. 同步修改 `pyproject.toml` 的 `version` 和 `src/annoweave/__init__.py` 的 `__version__`。
3. 用干净环境重建可执行文件：

   ```powershell
   .\scripts\setup.ps1 -Recreate
   .\scripts\build.ps1 -Runtime cpu
   ```

4. 校验 wheel 与 sdist 元数据：

   ```powershell
   python -m build
   .\scripts\check-package.ps1
   ```

5. 按 [Windows 构建指南](building-windows.md) 在一台未安装 Python 的干净电脑上验收。
6. 打标签并推送（`git tag v0.3.0`、`git push --tags`）。
7. 发布 GitHub Release，附件为压缩后的 `dist\AnnoWeave` 目录与校验值：

   ```powershell
   Get-FileHash .\AnnoWeave-0.3.0-cpu.zip -Algorithm SHA256
   ```

   不要上传构建环境或任何测试数据。

## 仓库设置

- 开启 Secret Scanning、Push Protection 和 Dependabot alerts。
- 保护 `main`：要求 Pull Request 且 CI 通过后才能合并。
- 模型权重、素材和工作流 JSON 既不能进仓库，也不能用 Git LFS 绕过。
