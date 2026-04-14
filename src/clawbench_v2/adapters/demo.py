from __future__ import annotations

import json
import subprocess
from pathlib import Path

from clawbench_v2.adapters.base import BaseAdapter
from clawbench_v2.models import AdapterRunContext, AdapterRunResult


class DemoAdapter(BaseAdapter):
    name = "demo"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        task_id = ctx.task.task_id
        out_dir = ctx.workspace / "out"
        if task_id == "01-file":
            out_dir.mkdir(parents=True, exist_ok=True)
            in_file = ctx.workspace / "in" / "input.txt"
            line_count = sum(1 for _ in in_file.open("r", encoding="utf-8"))
            (out_dir / "linecount.txt").write_text(f"{line_count}\n", encoding="utf-8")
        elif task_id == "02-exec":
            out_dir.mkdir(parents=True, exist_ok=True)
            nested = out_dir / "a" / "b"
            nested.mkdir(parents=True, exist_ok=True)
            (nested / "c.txt").write_text("", encoding="utf-8")
            (out_dir / "step1.txt").write_text("42\n", encoding="utf-8")
            (out_dir / "step2.txt").write_text("c.txt\n", encoding="utf-8")
            (out_dir / "step3.txt").write_text("hello\n", encoding="utf-8")
        elif task_id == "03-browser":
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "page_extract.txt").write_text("BENCHMARK_PAGE\n", encoding="utf-8")
        elif task_id == "04-meeting-summary":
            demo_apply = ctx.task.task_dir / "scripts" / "demo_apply.py"
            completed = subprocess.run(["python3", str(demo_apply), str(ctx.workspace)], capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                return AdapterRunResult(ok=False, stderr=completed.stderr, stdout=completed.stdout)
        elif task_id == "05-email-triage":
            replies = out_dir / "replies"
            replies.mkdir(parents=True, exist_ok=True)
            (out_dir / "triage.json").write_text(
                json.dumps(
                    {
                        "001": {"label": "spam", "reason_short": "钓鱼中奖诈骗"},
                        "002": {"label": "needs_reply", "reason_short": "同事询问是否方便"},
                        "003": {"label": "ok", "reason_short": "系统自动发货通知"},
                        "004": {"label": "spam", "reason_short": "仿冒安全通知钓鱼"},
                        "005": {"label": "needs_reply", "reason_short": "索要会议纪要"},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (out_dir / "delete_ids.txt").write_text("001\n004\n", encoding="utf-8")
            (replies / "002.txt").write_text("今天下午有空，可以一起讨论项目进度。\n", encoding="utf-8")
            (replies / "005.txt").write_text("好的，我稍后把上周五周会的会议纪要发到你邮箱。\n", encoding="utf-8")
        elif task_id == "06-access-bilibili":
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "titles.txt").write_text(
                "【练习】访问 bilibili 导航条与分区入口导览\n"
                "Mock 分区：离线 HTML 中的动态列表渲染\n"
                "小贴士：如何把本地端口当成「空间首页」\n",
                encoding="utf-8",
            )
            port = str(ctx.env.get("HTTP_PORT", "")).strip()
            source_url = f"http://127.0.0.1:{port}/" if port else "http://127.0.0.1/"
            (out_dir / "source_url.txt").write_text(source_url + "\n", encoding="utf-8")
        elif task_id == "07-session-memory":
            out_dir.mkdir(parents=True, exist_ok=True)
            prompt_name = ctx.prompt_file.name
            if "round1" in prompt_name:
                (out_dir / "phase1_done.txt").write_text("ready\n", encoding="utf-8")
            else:
                secret = str(ctx.env.get("MEM_SECRET", "")).strip()
                (out_dir / "recalled.txt").write_text(secret + "\n", encoding="utf-8")
        elif task_id == "08-image-recognize":
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "image1_answer.txt").write_text("red square\n", encoding="utf-8")
            (out_dir / "image2_answer.txt").write_text("橘白幼猫，针织毯上\n", encoding="utf-8")
        elif task_id == "09-git-pr-merge":
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "review.txt").write_text("APPROVE: CONTRIBUTING contains BENCH_PR_OK, merge to main.\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(ctx.workspace), "fetch", "origin"], capture_output=True, text=True, check=False)
            subprocess.run(["git", "-C", str(ctx.workspace), "checkout", "main"], capture_output=True, text=True, check=False)
            merge = subprocess.run(
                ["git", "-C", str(ctx.workspace), "merge", "--no-edit", "origin/feature/pr-add-doc"],
                capture_output=True,
                text=True,
                check=False,
            )
            if merge.returncode != 0:
                return AdapterRunResult(ok=False, stderr=merge.stderr, stdout=merge.stdout)
            push = subprocess.run(["git", "-C", str(ctx.workspace), "push", "origin", "main"], capture_output=True, text=True, check=False)
            if push.returncode != 0:
                return AdapterRunResult(ok=False, stderr=push.stderr, stdout=push.stdout)
        elif task_id == "10-office-docs":
            demo_apply = ctx.task.task_dir / "scripts" / "demo_apply.py"
            completed = subprocess.run(["python3", str(demo_apply), str(ctx.workspace)], capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                return AdapterRunResult(ok=False, stderr=completed.stderr, stdout=completed.stdout)
        else:
            return AdapterRunResult(ok=False, stderr=f"demo adapter has no handler for task {task_id}")
        return AdapterRunResult(ok=True, metadata={"demo": True, "task_id": task_id})
