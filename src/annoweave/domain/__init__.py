"""领域模型层（Domain Models）。

分层边界：UI → Application Services → Domain Models → Repository / Inference / Export。
本包承载核心领域实体（Project/Media/FrameRef/ObjectAnnotation/Association/
ReviewDecision/WorkflowSnapshot/Run/Artifact），只含数据与纯逻辑，不依赖 Qt。

M1 迭代将填充具体实体与字段。
"""
