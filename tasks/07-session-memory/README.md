# 07-session-memory

**任务描述：** **同一 session 两轮**：第一轮只记住口令不落盘；第二轮仅凭记忆写 `out/recalled.txt`；第一轮结束后由 hooks 做防泄露检查。


| 文件/目录                                     | 用途                             |
| ----------------------------------------- | ------------------------------ |
| `run.sh`                                  | 两轮 `openclaw agent`、轮间校验       |
| `prompt_round1.txt` / `prompt_round2.txt` | 各轮用户任务                         |
| `ground_truth.json`                       | `memory_secret` 等              |
| `check_round1_no_leak.py`                 | 第一轮后扫描 **`out/`** 目录是否含口令明文（**允许列表**，与 `hooks.after_round` 一致） |
| `hooks.py`                                | `after_round`：同上，仅检查 `out/` 下文件 |
| `oracle_grade.py`                         | `phase1_done` + `recalled.txt` |
| `verify_oracle.py`                        | 自检                             |
| `llm_rubric.py`                           | LLM 过程分                        |

