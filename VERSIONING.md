## 这是什么

本目录用于从 V12 开始维护一套“可上传 Git 的干净源码工作区”。

约定：

- 日常开发在本目录进行（长期维护）
- 里程碑版本用 Git 的 tag/branch 标记
- 同时在 `_versioning/source/AI-DCP-src-vN/` 保留一份“源码快照”（用于回滚/对照/交付说明）

## 第一次初始化（你手动执行）

在本目录打开终端后执行：

```bash
git init
git add .
git commit -m "chore: baseline v12"
git tag v12
```

如果你有远端仓库地址（例如 GitHub/GitLab），再执行：

```bash
git remote add origin <你的远端仓库地址>
git push -u origin main
git push origin v12
```

## 后续版本怎么做（推荐流程）

### 1) 开发与提交

- 日常改动：正常 `git add/commit`
- 大版本发布前：确保能运行/能构建（至少能启动前后端）

### 2) 打 tag（例如 v13）

```bash
git tag v13
git push origin v13
```

### 3) 同步生成“源码快照目录”（双保险）

把当前这份源码复制一份到：

- `_versioning/source/AI-DCP-src-v13/`

复制时不要带上这些内容（本仓库的 `.gitignore` 已经覆盖）：

- `node_modules/`、`dist/`、`venv/.venv/`
- `data/`、`logs/`、`.run/`
- `.env`、`*.key`、`*.enc`、`*.db/*.sqlite` 等敏感或运行态文件

### 4) 写版本说明

在快照目录中更新/补充：

- `VERSION.md`：写清楚这是 v13，对应的 Git tag 是什么，主要变更点是什么

