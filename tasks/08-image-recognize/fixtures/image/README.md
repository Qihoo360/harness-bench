# 评测用图（双图）

文件名须与 **`ground_truth.json` 的 `image_files`** 一致（当前为 `target1.png`、`target2.jpg`）。

- **target1.png**：黑色背景上的红色方块（几何/色块类主体）。
- **target2.jpg**：橘白长毛幼猫，坐于白色针织毯上；背景为虚化室内（帘/木作、柔光）。

本任务 **不做字符串 Oracle**；换图后请同步改 `ground_truth.json` 里的 **`rubric_reference`** 与 **`vision_score_axes`**（六项判分说明），以便 `llm_rubric.py` 与阅卷模型对齐。视觉六项每项仅 **0 / 0.5 / 1**；`grade_pipeline` 会对阅卷输出做同等量化后再算均值。

评分方式：跑完 live 后用 `grading/grade_pipeline.py`（带 LLM），`--task-name 08-image-recognize`；本任务无 `oracle_grade.py`，结果分来自 rubric（含 08 专用拆分），合成方式见 `grade_pipeline`。
