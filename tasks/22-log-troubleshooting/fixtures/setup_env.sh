#!/bin/bash
# setup_env.sh

# 初始化 git 仓库
git init

# 第一次提交：正常的基线状态
echo "urllib3==1.26.15" > requirements.txt
# (这里可以 echo 其他正常依赖进去)
git add requirements.txt
git commit -m "chore: initial project setup and dependencies"

# 第二次提交：其他人加了一些无关的代码
echo "print('hello world')" > main.py
git add main.py
git commit -m "feat: add main entry point"

# 第三次提交（案发现场）：引入了冲突的版本，且 Hash 必须固定或者可通过日志查到
# 为了确保证书环境一致，我们可以强行指定 GIT_AUTHOR_DATE 和 GIT_COMMITTER_DATE
echo "urllib3==2.0.2" > requirements.txt
git add requirements.txt
GIT_AUTHOR_NAME="Test User" GIT_AUTHOR_EMAIL="test@example.com" git commit -m "feat: upgrade urllib3 for new webhook agent"

# （可选）获取当前案发现场的 commit hash 存起来供 oracle_grade.py 判分使用
git rev-parse HEAD > .guilty_commit_hash