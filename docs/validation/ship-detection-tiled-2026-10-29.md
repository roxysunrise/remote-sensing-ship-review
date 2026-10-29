# 船舶检测大图增强：2026-10-29 实际验证

日期Asia/Shanghai。同一ship-detection模块，不训练、不改网络；在隔离分支codex/ship-tiled-inference上测试，基于PR #4的4db0ca0，依赖PR #4和#2，尚未合并/部署。源码SHA和轻量真实输出摘要见[JSON记录](ship-detection-tiled-2026-10-29.json)。

## 环境与执行

Windows；复用原项目`.venv/Scripts/python.exe`（本工作区.venv为本地Junction），Python3.10.20、torch2.9.1+cu128、Ultralytics8.4.189、OpenCV4.13.0、NumPy2.2.6、Pillow12.2.0；RTX5060 Laptop 8GB。best.pt哈希6508d583f538e858d498335c0fb6efd24c9e5116823eb4152f6d6979193cd26f；本地硬链接只读复用权重，不上传。不是新机器全新安装验证。

从仓库根目录执行（外部样例目录为原工作区datasets/smoke；新机器按algorithm/README准备）：

```powershell
& .venv/Scripts/python.exe -m unittest discover -s tests -v
& .venv/Scripts/python.exe -m pip check
& .venv/Scripts/python.exe scripts/verify_tiled_smoke.py --samples 'C:/Users/28028/OneDrive/图片/文档/ChatGPT/遥感应用/datasets/smoke' --weights models/yolov8l-obb-dota-v1-80e/best.pt --device 0
& .venv/Scripts/python.exe -m algorithm.ship_detector 'C:/Users/28028/OneDrive/图片/文档/ChatGPT/遥感应用/datasets/smoke/dense_nonsquare.jpg' --tiled --tile-size 640 --stride 320 --imgsz 640 --device cpu --output outputs/tiled-smoke/cpu
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run-detection.ps1 -Image 'C:/Users/28028/OneDrive/图片/文档/ChatGPT/遥感应用/datasets/smoke/empty.png' -Output outputs/tiled-smoke/wrapper -Device 0 -Tiled
git diff --check
```

## 结果

- 17项unittest通过：4项旧契约检查，13项大图测试；其中几何和编排测试使用显式标识的脚本检测器，不作为模型证据。
- 覆盖非整倍数右/下边缘、单片/非方形、无重叠、跨片偏移去重、真实多边形而非外接矩形抑制、类别隔离/分数并列、大小/片数/候选上限、输入变化、逐片失败、CLI子进程错误。
- `pip check`：No broken requirements found。
- CLI非法stride、缺失输入均实测退出2，stderr为INVALID_ARGUMENT/INPUT_NOT_FOUND JSON；错误样例已保存。首次错误检查曾暴露python -m导致异常类双重定义；共享errors.py修复后，独立进程回归从失败转通过。
- CPU非方形JPEG：3片，69候选→32保留；推理阶段约2411ms，合并约1.7ms。PowerShell包装器真实GPU空图成功退出0，count=0。

| 真实GPU运行样例 | 尺寸 | 片数 | 合并前→后 | 推理/合并耗时 |
|---|---|---:|---:|---|
| 组合测试图 | 2305×1537 | 12 | 174→37 | 约620ms / 4.5ms |
| DOTA empty | 1024×1024 | 9 | 0→0 | 约229ms / <1ms |
| 非方形JPEG | 1024×512 | 3 | 69→32 | 约87ms / 1.8ms |
| 合成黑色空图 | 4097×4097 | 9 | 0→0 | 约326ms / <1ms |

最后一项超过旧16,777,216像素上限，使用tile_size2048/stride2048/imgsz320，仅证明较大图读入/执行/保存与空结果；不证明大图船舶效果。耗时是本机单次观测，不含模型构造，不能比较为吞吐SLA。逐项参数见JSON。

单片等价检查：dense使用merge_iou=1，旧predict和predict_large均32框，对应框最小多边形IoU约0.99999992。保证关闭额外合并时模型路径一致，不代表默认合并阈值不会改变结果。

组合图由dense.png粘贴到(600,300)，其余区域为常量背景，不是真实独立卫星大图。切片后23个预测与平移标注IoU≥0.5；整图推理37个预测，其中22个与标注IoU≥0.5。这不是一对一AP评估，不能从23比22或相同count推出精度/召回提升。原图有43个船标注，未做训练重叠排查，漏检/误检仍在。

## 产物及未验证项

完整本地产物位于outputs/tiled-smoke（Git忽略）：原始组合/空图、整图/切片/CPU JSON、preview.png、错误与verification.json。contracts/examples只上传少量JSON，不上传影像、权重或环境。

未验证：独立合法大图精度、默认0.1阈值的适合性、最大64Mi像素峰值内存/最坏NMS耗时、跨机器、并发、Web/Worker/权限/业务联调。切片可能截断大船、保留边界残片、增加误检；没有残片拼接、整图融合、取消/恢复或正式部署。
