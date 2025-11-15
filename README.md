# plaso-downloader

一个面向终端的工具，可以批量拉取 plaso 云课堂课程的 Day 结构，为每一节课下载 PDF 和视频（自动处理 m3u8→ts→mp4），并生成整齐的本地目录层级。

## 环境要求

- Python 3.10 及以上版本
- 无需 FFmpeg；程序直接按顺序合并 TS 片段。

## 目录结构

```
plaso-downloader/
  README.md
  Makefile
  pyproject.toml
  .env.example
  .cache/
  plaso_downloader/
    api/
    downloader/
    models/
    utils/
    main.py
```

所有源码都在项目根目录下的 `plaso_downloader/` 包内，可直接通过 `python -m plaso_downloader.main` 运行，无需 `pip install -e .`。

## 使用 uv 管理虚拟环境与依赖

```bash
# （首次使用）安装 uv
pip install uv

# 创建虚拟环境并激活
uv venv
source .venv/bin/activate

# 安装项目（包含依赖）
uv sync
```

（可选）使用 [pre-commit](https://pre-commit.com/) 来自动执行基础检查：

```bash
pre-commit install
```

## 借助 Makefile 简化命令

常用操作已经封装在 `Makefile` 中：

```bash
# 安装依赖（自动创建/复用 .venv 并执行 uv pip install -e .）
make install

# 运行下载任务：可以直接提供 access-token，或改用自动登录
make run GROUP_ID=3173947 PACKAGE_ID=67a22138a0ce258eb09d8124 ACCESS_TOKEN=xxx
make run GROUP_ID=3173947 ALL_PACKAGES=1 LOGIN_PHONE=176xxxx LOGIN_PASSWORD=秘密

# 查看账号下的全部分组/课程包
make run LIST_GROUPS=1 ACCESS_TOKEN=xxx
make run GROUP_ID=3173947 LIST_PACKAGES=1 ACCESS_TOKEN=xxx

# 打开已激活的虚拟环境 shell
make shell
```

`make help` 会列出所有可用目标与当前配置。若只想临时覆盖参数，例如限制下载 5 个 Day，可在 `make run` 末尾追加 `MAX_TASKS=5`。

## 获取 access-token

1. 在浏览器或客户端中打开 plaso 课堂，开启开发者工具的 Network 选项卡。
2. 触发进入课堂或刷新页面，找到 `https://www.plaso.cn/yxt/servlet/bigDir/getXfgTask` 请求。
3. 复制请求头中的 `access-token`，并记录返回 JSON 里的 `id`（课程 id）、`groupId`、`xFileId` 等参数。

## 自动登录（可选）

如果不想手动抓取 `access-token`，也可以直接在 CLI 中提供登录账号和密码，内部会调用 `https://www.plaso.cn/custom/usr/doLogin` 接口，并使用真实的 macOS Electron 设备信息：

```bash
plaso-downloader \
  --login-phone 176xxxxxx \
  --login-password 你的明文密码 \
  --course-id <课程 id> \
  --group-id <group id> \
  --xfile-id <xfile id>
```

- 如果你已经准备好了 MD5 口令，可在命令后追加 `--login-password-md5`。
- 登录请求与抓包中的 UA、deviceId、clientVersion 完全一致，不需要额外配置。
- 出于安全考虑，建议用环境变量或 `make run LOGIN_PASSWORD=...` 的方式传递密码，避免留在 shell 历史里。
- 登录成功后，工具会将 access-token 缓存在 `项目目录/.cache/token.json`（可用 `TOKEN_CACHE` 覆盖），下次运行会优先复用缓存，避免频繁登录触发风控。

## 运行示例

安装完成后会自动注册命令 `plaso-downloader`，也可以使用 `python -m plaso_downloader.main`。

```bash
# 查看当前账号的所有课程组
plaso-downloader --access-token <你的 token> --list-groups

# 查看某个 group 下的课程包
plaso-downloader --access-token <你的 token> --group-id 3173947 --list-packages
plaso-downloader --access-token <你的 token> --group-id 3173947 --list-packages --package-search 江苏

# 查看某个 package 下的 Task（Day）
plaso-downloader --access-token <你的 token> --group-id 3173947 --package-id 67a22138a0ce258eb09d8124 --list-tasks

# 下载指定 group + package（xFileId 可由上一步获取）
plaso-downloader \
  --access-token <你的 token> \
  --group-id 3173947 \
  --package-id 67a22138a0ce258eb09d8124 \
  --task-ids 67a359147a935d2e6027652b,67a4a135633f5cf0385b9fcf \
  --download \
  --output-dir downloads \
  --workers 16 \
  --max-tasks 5

# 由工具自动登录换取 token，并下载整个 group 所有 package
plaso-downloader \
  --login-phone 176xxxxxx \
  --login-password 你的密码 \
  --group-id 3173947 \
  --download \
  --all-packages

# 下载历史课堂视频回放

除了常规课程包外，现在也可以直接拉取“历史课堂”里已经生成的回放视频。只需提供时间范围即可，工具会调用 `/liveclassgo/api/v1/history/listRecord` 并逐个下载返回的文件：

```bash
plaso-downloader \
  --access-token <你的 token> \
  --history-from 2025-01-01 \
  --history-to 2025-01-31 \
  --download \
  --history-output history_recordings
```

- `--history-from` / `--history-to` 接受 `YYYY-MM-DD` 格式的日期，闭区间内的所有回放都会列出。
- 如果不加 `--download`，命令只会打印记录列表；确认无误后再次附带 `--download` 即可真正下载。
- 默认会把视频保存到 `<OUTPUT_DIR>/history_records/`，也可以通过 `--history-output` 指定单独的根目录。目录下同样会维护 `.download_manifest.json`，避免重复下载。
- 进入历史模式后无需再传 `--group-id`/`--package-id`，命令会直接退出常规课程流程。

- `--workers` 控制 TS 下载并发数（默认 16）。
- `--max-tasks` 可选，只下载前 N 个 Day。
- 默认以预览模式展示匹配到的课程包，只有显式加上 `--download`（或在 `.env` 中设置 `DOWNLOAD=1`）才会真正下载。
- `--list-tasks` 会列出每个匹配 package 内的 Day 列表；`--task-ids id1,id2` 可只下载/列出特定 Day。
- `--package-search` 可用于模糊匹配课程包标题；`--package-limit` 可限制下载包数量。
- 仍然支持旧版 `--course-id + --xfile-id` 组合，方便手工指定某个目录。
- 若系统安装了 `ffmpeg`，视频会自动 remux 成 `.mp4`；否则保留为 `.ts` 并给出提示。

## 下载目录结构

```
downloads/
  Day1 Intro/
    video_1.mp4
    pdf_1.pdf
  Day2 Advanced/
    video_1.mp4
    video_2.mp4
    pdf_1.pdf
```

每个 Day 会新建独立文件夹，名称沿用课堂标题并自动移除非法字符。视频在合并成功后会删除 `tmp_ts/<视频名>/` 临时 TS 目录，方便重复运行和断点续传。

整体目录结构如下：

```
downloads/
  <GroupName>/
    <PackageTitle>/
      Day1 .../
      Day2 .../
```

借助 group -> package -> day 的层级，可以方便地管理多条课程线以及长期课程包。

每个 `pdf_X.pdf` 会附带一个 `pdf_X_pages/` 目录，包含服务端转换出的逐页 JPG，便于离线阅读或打印；成功合成 PDF 后会自动清理临时图片。每个 package 目录下还会维护 `.download_manifest.json`，用于记录已下载的资源，下次运行时会自动跳过。
## 使用 .env 管理参数（可选）

为了避免每次在命令行里填写长串参数，可以复制 `.env.example` 为 `.env`，并写入常用配置：

```bash
cp .env.example .env
vim .env  # 或使用你熟悉的编辑器

# 编辑完成后即可直接运行 make / python 命令，程序会自动读取 .env
make run ALL_PACKAGES=1
```

如果你习惯在 shell 中 `source .env` 也没问题；Makefile 会在执行时自动加载 `.env`，Python 程序内部也使用 `python-dotenv` 读取该文件。

常用变量包括 `LOGIN_PHONE`、`LOGIN_PASSWORD`、`GROUP_ID`、`PACKAGE_ID`、`PACKAGE_SEARCH`、`TASK_IDS`、`LIST_TASKS`、`DOWNLOAD` 等；`TOKEN_CACHE` 可以自定义缓存 access-token 的存储路径。

> `.env` 中可能包含 access-token 或登录密码等敏感信息，请妥善保管、避免提交到版本控制中。
