"""导出服务（Export Service）：统一的大图/单裁剪/全裁剪/元数据导出。

四种模板共用同一导出实现；所有图片写盘检查返回值，统计成功/失败/跳过。
统一命名 project/media/run/frame/object/revision，默认不覆盖。M2 迭代填充。
"""
