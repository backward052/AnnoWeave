# Windows EXE 构建指南

## 环境

- Windows 10/11 x64
- 64 位 CPython 3.10 – 3.13（推荐 3.12/3.13）
- PowerShell 5.1 或 7
- CPU 版无需 CUDA；GPU 版需与 `onnxruntime-gpu` 匹配的 NVIDIA 驱动和运行库

## 标准构建

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1                 # 创建 .venv 并安装依赖
.\scripts\build.ps1 -Runtime cpu
```

`build.ps1` 会复用 `.venv`、安装 `build` 依赖、运行测试，再用
`packaging/pyinstaller/annoweave.spec` 生成 one-folder 应用。one-folder 比单文件模式
启动更快，也更容易检查 Qt 与 ONNX Runtime 动态库。脚本不再硬编码 Python 小版本：
`.venv` 不存在时会自动转交给 `setup.ps1`。

GPU 构建：

```powershell
.\scripts\build.ps1 -Runtime gpu
```

中间开关：`-SkipInstall` 复用现有环境，`-SkipTests` 跳过测试门槛。

## 校验包元数据

```powershell
python -m build
.\scripts\check-package.ps1
```

该脚本直接读取 wheel 与 sdist，确认许可证表达式、`Requires-Python` 边界、运行时依赖，
以及是否打进了品牌资源文件。

## 验收

在一台未安装 Python 的干净 Windows 电脑上复制整个 `dist\AnnoWeave` 目录，并验证：

1. `AnnoWeave.exe` 能启动且 Logo、中文、英文正常。
2. 打开图片和视频文件夹后能切换素材。
3. 添加一个测试模型后，“试跑选中模型”成功。
4. 工作流运行前检查、当前帧运行、框编辑和裁剪图编辑正常。
5. 批量任务可取消，已完成清单仍可读取。
6. 输出路径含中文、空格和较长目录时可写入。

发布 EXE 时打包整个 `dist\AnnoWeave`，不要只复制 EXE。不要把测试模型、测试素材或
本机配置放进该目录。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 双击 EXE 报 `Failed to load Python DLL ... build\pyinstaller\...\pythonXXX.dll` | **你点错了文件。** 构建目录 `build\` 里还有一个同名 `AnnoWeave.exe`，那是 PyInstaller 的中间产物，旁边没有配套的 `pythonXXX.dll`。请运行 `dist\AnnoWeave\AnnoWeave.exe`。`build.ps1` 现在会在构建后自动删除中间副本，`check-package.ps1` 也会校验只剩一个可运行 EXE。 |
| 构建中断，报 `PermissionError: [WinError 5] 拒绝访问` | 上一个 `AnnoWeave.exe` 还在运行，锁住了旧输出。关闭程序窗口，或执行 `Stop-Process -Name AnnoWeave`，再重新构建。`build.ps1` 现在会在构建前检测并直接提示。 |
| 缺少 Qt DLL | 确认复制了整个输出目录，然后重新构建（`build.ps1` 默认带 `--clean`）。多数情况是 `build\pyinstaller` 缓存过期。 |
| ONNX Runtime 加载失败 | 检查 CPU/GPU 包是否混装，以及目标机运行库是否满足要求。 |
| Windows Defender 提示未知发布者 | 正式发布前用组织的代码签名证书签署 EXE 和安装包。 |
| `py -3.11` / `py -3.12` 提示 *No suitable Python runtime found* | `build.ps1` 已不再固定版本。执行 `.\scripts\setup.ps1` 自动选择可用解释器，或用 `setup.ps1 -Python` 显式指定。 |
| 路径过长报错 | 仓库和虚拟环境尽量放在较短路径，例如 `D:\AnnoWeave`。Qt 固定在 6.8 系列以降低路径风险。 |
| EXE 能启动但界面为空 | 属于正常现象：仓库不附带权重。先到“模型库”添加模型。 |
| 构建过程中 `pip install` 失败 | 执行 `.\scripts\setup.ps1 -Recreate` 并按输出提示处理代理或 TLS 问题。 |
