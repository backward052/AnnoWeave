"""应用服务层（Application Services）。

承载 ReviewService / InferenceRunService / ExportService / WorkflowService /
ProjectService / SaveService 等面向用例的服务，协调领域模型与仓储/推理/导出，
不写 Qt 逻辑。M2 迭代填充。
"""
