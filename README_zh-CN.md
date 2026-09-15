# AnnoWeave

**本地视觉 AI 推理、标注、关联裁剪与人工复核工作台。**

![AnnoWeave](src/annoweave/assets/annoweave-logo.png)

[English](README.md) · [快速开始](docs/zh-CN/getting-started.md) · [工作流入门](docs/zh-CN/workflow-tutorial.md) · [构建 EXE](docs/zh-CN/building-windows.md) · [发布检查](docs/zh-CN/release-checklist.md) · [发布到 GitHub](docs/zh-CN/publishing-github.md)

AnnoWeave 将图片与视频采样、多模型推理、对象空间关联、证据裁剪、框编辑、人工复核和训练数据导出组织成可配置节点。素材、模型权重、项目数据库和工作流配置都保存在本机。

仓库不附带任何模型权重。首次启动时模型库与工作流都是空的，这是有意设计的：你指向自己的本地 ONNX 模型。

## 工作方式

![AnnoWeave 工作流：检测 → 关联 → 裁剪 → 下游](docs/assets/workflow-associate-crop.gif)

两个整图模型在同一帧上推理，空间规则判定每个标记属于哪个容器，只裁剪命中的容器，再由分类模型对每张裁剪图打分。每一步都是一个节点、每个节点都可替换；第三步的归属判定来自**真实的关联节点**，第四步会明显跳过没有命中任何标记的容器。

该动画由 [`tools/make_demo_gifs.py`](tools/make_demo_gifs.py) 以程序化几何生成 —— 不含真实素材、不含模型权重 —— 因此可复现，而不是一个无法维护的手工二进制文件。

## 主要能力

- 打开单张图片、多个文件或文件夹，统一复核图片与视频采样帧。
- 在画布上新增、移动、缩放、改类和删除框，并对裁剪图重新标注。
- 把多个 ONNX 模型组成整图、级联、空间关联、ROI 与计数流程。
- 修改上游框后重新计算关联、裁剪和下游结果。
- 批量处理素材，记录工作流与模型快照，导出复核产物或训练数据。
- 插件节点注册表允许扩展推理、规则和输出能力。
- 中文与英文界面；Windows 10/11 可从源码运行或构建独立 EXE。

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 x64 |
| Python | 64 位 CPython 3.10 – 3.13（**推荐 3.12 或 3.13**） |
| 磁盘 | 约 1.5 GB（PySide6 与 ONNX Runtime 体积较大） |
| 显卡（可选） | 与 `onnxruntime-gpu` 版本匹配的 NVIDIA 驱动 |

## 安装

### 一条命令（推荐）

```powershell
git clone https://github.com/backward052/AnnoWeave.git
cd AnnoWeave
.\scripts\setup.ps1
```

`setup.ps1` 会自动寻找可用的 64 位 Python、创建 `.venv`、安装依赖、校验导入，并运行测试。失败时会直接打印下一步该执行的命令。

如果 PowerShell 拒绝执行脚本，只为当前窗口放开限制：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### 手动安装

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[cpu]"
annoweave
```

使用 NVIDIA CUDA 版 ONNX Runtime 时把 `cpu` 改为 `gpu`。不要在同一环境同时安装 `onnxruntime` 与 `onnxruntime-gpu`。

### 启动

```powershell
.\scripts\run.ps1
```

## 安装常见问题

| 现象 | 处理方法 |
|---|---|
| 提示 *No suitable Python runtime found*（`py -3.11` / `py -3.12`） | 说明本机没装该版本。用 `py -0p` 查看已装版本，或直接用 setup.ps1 自动选择：`winget install Python.Python.3.12` |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | 执行 `Set-ExecutionPolicy -Scope Process Bypass` 后重试；也可以不激活，直接调用解释器：`.\.venv\Scripts\python.exe -m annoweave` |
| 启动时报 `No module named 'PySide6'` | 当前解释器里没装应用。执行 `.\scripts\setup.ps1`，或 `.\.venv\Scripts\python.exe -m pip install -e ".[cpu]"` |
| pip 报找不到 `onnxruntime` 可用版本 | 你用的是 32 位 Python，或 Python 3.14+。请安装 64 位 Python 3.12 |
| 公司代理或 TLS 拦截导致 pip 失败 | `.\.venv\Scripts\python.exe -m pip install -e ".[cpu]" --proxy http://host:port`，或追加 `--trusted-host pypi.org --trusted-host files.pythonhosted.org` |
| ONNX Runtime 能加载但模型跑不动 | 检查是否同时装了 `onnxruntime` 和 `onnxruntime-gpu`；用 `.\scripts\setup.ps1 -Recreate` 重建环境 |
| 界面能打开但什么都没有 | 全新安装时属于正常现象。先到“模型库”添加模型，见[快速开始](docs/zh-CN/getting-started.md) |
| 路径过长导致 Qt 或构建异常 | 把仓库和 `.venv` 放在较短路径，例如 `D:\AnnoWeave` |

## 构建 Windows EXE

```powershell
.\scripts\setup.ps1            # 首次执行，创建 .venv
.\scripts\build.ps1 -Runtime cpu
```

产物位于 `dist\AnnoWeave\AnnoWeave.exe`。发布时要打包整个 `dist\AnnoWeave` 目录，不能只复制 EXE。详细说明见 [Windows 构建指南](docs/zh-CN/building-windows.md)。

## 工作流快速路径

1. 在“模型库”添加本机 ONNX 权重：唯一名称、模型类型、输入尺寸和完整类别表（按输出索引顺序）。
2. 在“工作流”选择通用模板，并把模型映射到模板的职责槽位。
3. 在节点画布检查数据键：推理节点的输出键必须与关联、裁剪或规则节点引用的键一致。
4. 执行“运行前检查”，再用一张无敏感内容的图片跑通整条流水线。
5. 保存后切到“视觉复核”，运行当前帧，检查全图、裁剪图与下游结果。

可以导入仓库自带的示例工作流 `examples/workflows/associate-crop-infer.json`（工作流页菜单 →“导入工作流 JSON”），再按其中的名称登记模型。插件示例见 [examples/plugins](examples/plugins)。

## 目录结构

```text
AnnoWeave/
├─ .github/                 # CI 与问题模板
├─ docs/                    # 中英文使用与构建文档
│  └─ assets/               # README 演示动画（由脚本生成）
├─ examples/
│  ├─ plugins/              # 无业务含义的插件示例
│  └─ workflows/            # 通用教学工作流
├─ packaging/pyinstaller/   # PyInstaller 配置
├─ scripts/                 # 环境安装、运行、构建、发布前检查
├─ src/annoweave/           # 应用源码
├─ tests/                   # 公共行为与发布边界检查
├─ tools/                   # 维护者工具（演示动画生成器）
├─ pyproject.toml           # 依赖、入口和工具配置
├─ README.md
└─ README_zh-CN.md
```

## 本机数据位置

运行时数据默认写入 `%LOCALAPPDATA%\AnnoWeave`，不会写进 Git 仓库。

设置 `ANNOWEAVE_CONFIG_DIR` 可以把**全部**数据集中到一个自定义目录：模型库、工作流集合、各项目的复核数据库、标签目录、插件、复核会话和预计算缓存。

```powershell
$env:ANNOWEAVE_CONFIG_DIR = "D:\AnnoWeaveData"
.\scripts\run.ps1
```

## 开发

```powershell
.\scripts\setup.ps1              # 同时安装开发依赖
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

设置 `QT_QPA_PLATFORM=offscreen` 可以在没有可见窗口的情况下运行界面测试。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 发布边界

仓库不会提交模型权重、素材、SQLite 数据库、用户会话、日志、导出产物或生产工作流。提交或发布前运行：

```powershell
.\scripts\check-release.ps1
git status --short
```

该脚本以 Git 索引为准，只检查真正会被推送的文件，因此不会被本机 `.venv`、`build`、`dist` 干扰。详细清单见[发布检查](docs/zh-CN/release-checklist.md)。

## 致谢与引用

AnnoWeave 不是下列项目的分支，也没有与之共享任何代码或素材。列出它们是因为它们影响了本项目的设计，或与本项目互补；其中**没有复制任何内容** —— 尤其 X-AnyLabeling 是 GPL-3.0，所以本项目不内联它的任何东西。

- **[VideoPipe](https://github.com/sherlockchou86/VideoPipe)**（Apache-2.0）—— 基于离散推理节点构建的跨平台视频结构化框架。“每个模块只做一件事、节点之间通过明确的数据键连接”这一思路正是 AnnoWeave 节点图的来源，通用三模型教程也沿用这套表述。
- **[X-AnyLabeling](https://github.com/CVHub520/X-AnyLabeling)**（GPL-3.0）—— 桌面标注工具，模型库丰富、导出格式多。需要**生产**数据集时用它；需要**运行并复核**多模型流水线、追溯每个结果来源时用 AnnoWeave。

## 状态与许可

项目当前处于 Alpha 阶段，次要版本之间可能出现破坏性变更。变更记录见 [CHANGELOG.md](CHANGELOG.md)。

本项目以 [MIT 许可证](LICENSE)发布。
