# 08-image-recognize

**任务描述：** 双图路径注入 prompt，模型识图并各写一行到 `out/image1_answer.txt`、`out/image2_answer.txt`。**最终结果分以 LLM rubric 为主**（含 `vision_breakdown` 视觉分项）；另有 **oracle** 仅检查答案文件是否存在（不参与最终 `combined` 合成）。


| 文件/目录               | 用途                                           |
| ------------------- | -------------------------------------------- |
| `prompt.txt`        | 题面（`$IMAGE1_ABS_PATH` / `$IMAGE2_ABS_PATH`）  |
| `fixtures/image/`   | 评测用图；细项见 `fixtures/image/README.md`                   |
| `ground_truth.json` | `rubric_reference`、`vision_score_axes`       |
| `oracle_grade.py`   | 检查 `out/image1_answer.txt`、`out/image2_answer.txt` 是否存在且非空；产出 `oracle_outcome_score`（**审计用**） |
| `llm_rubric.py`     | 阅卷提示：`vision_breakdown` + 过程三维；**不要求输出 `total`** |
| `hooks.py`          | 若有：任务级钩子                                  |

## 计分（与评测程序一致）

- **`combined_score`（榜单主分）** = **`reply_appropriateness` × mean(tool_use_appropriate, flow_coherence, error_handling)**。  
  其中 **`reply_appropriateness`** 由程序根据 **`vision_breakdown`** 六项（0 / 0.5 / 1）均值写入，可与阅卷模型初填值对齐覆盖。
- **`oracle_outcome_score`**：仅反映产物文件是否齐；**不参与**上述 `combined`。
- 结果 JSON 中 `scoring.notes`、`rubric.total`（rubric-primary 下为三维过程分）见运行结果说明。

