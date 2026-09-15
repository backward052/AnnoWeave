# Getting started

## 1. Install

```powershell
git clone https://github.com/backward052/AnnoWeave.git
cd AnnoWeave
.\scripts\setup.ps1
```

`setup.ps1` finds a compatible 64-bit Python (3.10 – 3.13; 3.12/3.13 recommended), creates `.venv`, installs the CPU runtime and dev extras, verifies the imports, and runs the test suite.

Manual equivalent:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[cpu]"
```

Use `.[gpu]` instead of `.[cpu]` for the NVIDIA CUDA ONNX Runtime package. Never install both Runtime packages in one environment.

Start the app with `.\scripts\run.ps1` or `annoweave` inside the activated environment.

If a step fails, see the troubleshooting table in the [main README](../README.md#troubleshooting-the-install).

On first launch the model library and workflow catalog are empty. This is intentional: the public build ships no weights, class tables, or production pipelines.

## 2. Add your first model

Open **Model Library** and click **+ Add model**:

- **Name**: the identifier workflows reference, for example `primary_detector`. Avoid renaming it later.
- **Type**: detection, classification, or pose, matching your ONNX export.
- **Model path**: your local weights. The path is stored only in local configuration.
- **Input size and letterbox**: must match training and export settings.
- **Class list**: fill it in completely, in output-index order. A wrong order shows the wrong labels.
- **Thresholds**: start from your validation-set values, then tune on real footage.

With the model selected, run **Test selected model**. "Ready" only means the dependency and file exist; a successful smoke test proves the weights load and one full pre/post-processing pass completes.

## 3. Create a workflow

Open **Workflows** and click **+ Create workflow**. If you are new to the tool, start with the **multi-model full frame** template to validate each model, then move to **detect → crop → downstream inference** or **two-model association → crop → downstream**.

Run the preflight check before saving and resolve anything it reports:

- a node references a model that is not in the model library;
- an association node references a result key that is never produced;
- a crop node has no subject or association result before it;
- downstream inference has no crop node before it.

You can also import the bundled example: on the Workflows page use **Import workflow JSON** and select `examples/workflows/associate-crop-infer.json`. It is a generic teaching structure, not a runnable pipeline — register models with the names it uses first.

## 4. Open media and review

In **Review**, click **Open media** and choose a single file, several files, or a folder. Videos produce sampled frames based on the sampling FPS; each image is one review item.

**Run current frame** re-executes the current workflow. If the frame already has manual edits, the model result replaces them only after inference succeeds, so a model failure cannot destroy your edits. After you change a full-frame box, the association, crop, and downstream nodes are recomputed from the current annotation.

Box editing shortcuts:

- `V`: select and move; `R`: draw a rectangle; `H`: pan.
- Mouse wheel: zoom; middle-drag: pan.
- `Delete`: remove the selected object; `Ctrl+Z` / `Ctrl+Shift+Z`: undo / redo.
- `Tab`: cycle through overlapping objects.

## 5. Save and export

Ordinary browsing never adds a frame to the training set. Only frames you edit, or explicitly add to the save set, are exported as training data. Review artifacts are for inspection; a training export writes original and cropped images plus machine-readable labels. Check the class mapping and output directory before exporting, and keep the generated manifest for traceability.

## 6. Where your data lives

Everything defaults to `%LOCALAPPDATA%\AnnoWeave` and never enters the repository. To keep it all in one custom directory instead, set `ANNOWEAVE_CONFIG_DIR` before launching:

```powershell
$env:ANNOWEAVE_CONFIG_DIR = "D:\AnnoWeaveData"
.\scripts\run.ps1
```
