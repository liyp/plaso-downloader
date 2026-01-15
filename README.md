# plaso-downloader

> 免责声明：本项目仅供个人自我学习与技术实践之用，所有代码示例和接口调用均用于研究客户端协议与自动化脚本编写。请勿将本项目用于任何违反法律法规、服务条款或侵犯他人权益的用途，由此产生的风险与责任由使用者自行承担。

一个面向终端的工具，可以批量拉取 plaso 云课堂课程的 Day 结构，为每一节课下载 PDF 和视频（自动处理 m3u8→ts→mp4），并生成整齐的本地目录层级。

## ✨ 新特性

- **多分片视频支持** - 自动识别并合并分片录播（a2, a3, a4...）
- **双存储类型支持** - 兼容 `liveclass`（OSS STS 签名）和 `ossvideo`（直链 auth_key）
- **时长校验** - 下载完成后自动验证视频时长，显示 ✓/⚠/✗ 状态
- **下载报告** - `--report` 生成详细的下载状态报告，按课程分类统计
- **高并发下载** - 默认使用 CPU 核心数 × 4 个并发 worker
- **错误容忍** - ffmpeg 自动跳过损坏的 TS 片段

## 环境要求

- Python 3.10 及以上版本
- FFmpeg（可选，推荐安装以获得更好的视频兼容性）
- ffprobe（时长校验需要，通常随 ffmpeg 一起安装）

## 目录结构

```
plaso-downloader/
  README.md
  Agents.md           # 架构文档
  Makefile
  pyproject.toml
  .env.example
  .cache/
  plaso_downloader/
    api/              # API 封装
    downloader/       # 下载器
    models/           # 数据模型
    utils/            # 工具类
    main.py           # 入口
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

### 查看课程结构

```bash
# 查看当前账号的所有课程组
plaso-downloader --access-token <你的 token> --list-groups

# 查看某个 group 下的课程包
plaso-downloader --access-token <你的 token> --group-id 3173947 --list-packages
plaso-downloader --access-token <你的 token> --group-id 3173947 --list-packages --package-search 江苏

# 查看某个 package 下的 Task（Day）
plaso-downloader --access-token <你的 token> --group-id 3173947 --package-id 67a22138a0ce258eb09d8124 --list-tasks
```

### 下载课程

```bash
# 下载指定 group + package
plaso-downloader \
  --access-token <你的 token> \
  --group-id 3173947 \
  --package-id 67a22138a0ce258eb09d8124 \
  --task-ids 67a359147a935d2e6027652b,67a4a135633f5cf0385b9fcf \
  --download \
  --output-dir downloads \
  --workers 64 \
  --max-tasks 5

# 由工具自动登录换取 token，并下载整个 group 所有 package
plaso-downloader \
  --login-phone 176xxxxxx \
  --login-password 你的密码 \
  --group-id 3173947 \
  --download \
  --all-packages
```

### 下载历史课堂回放

除了常规课程包外，现在也可以直接拉取"历史课堂"里已经生成的回放视频。只需提供时间范围即可：

```bash
# 预览历史记录（带人性化时长显示）
plaso-downloader \
  --access-token <你的 token> \
  --history-from 2025-01-01 \
  --history-to 2025-12-31

# 输出示例：
#   - 20250715_1350 数量关系第八课-容斥问题 | duration=2h06m51s (7611s)
#   - 20250628_1434 资料分析第一课-变化 | duration=2h57m10s (10630s)

# 下载
plaso-downloader \
  --access-token <你的 token> \
  --history-from 2025-01-01 \
  --history-to 2025-12-31 \
  --download \
  --workers 64
```

下载完成后会自动验证时长：
```
✓ Duration OK: 2h06m51s (7612s) | expected 2h06m51s (7611s) | diff +0s (0.0%)
```

### 生成下载报告

使用 `--report` 参数生成详细的下载状态报告：

```bash
plaso-downloader \
  --access-token <你的 token> \
  --history-from 2025-02-01 \
  --history-to 2025-12-31 \
  --report
```

报告内容包括：
- **📈 总体统计** - 预期/已下载/缺失数量、完成率、总时长
- **📚 分类统计** - 按课程类型分类（数量关系、资料分析、判断推理、申论/真题）
- **❌ 缺失视频** - 未下载的视频列表
- **⚠️ 时长异常** - 实际时长与预期差异 >5% 的视频

示例输出：
```
================================================================================
📊 下载报告 (Download Report)
================================================================================

📈 总体统计
----------------------------------------
| 指标               | 数值          |
|-------------------|--------------|
| 预期视频数         |          132 |
| 已下载数           |          132 |
| 缺失数             |            0 |
| 完成率             |       100.0% |
| 预期总时长         |   93h25m10s |
| 实际总时长         |   93h25m12s |

📚 分类统计
----------------------------------------
  判断推理: 16个视频, 总时长 46h30m00s
  数量关系: 19个视频, 总时长 49h15m30s
  资料分析: 13个视频, 总时长 28h45m00s
  申论/真题: 84个视频, 总时长 72h30m00s
```

## CLI 参数速查

| 参数 | 说明 |
|------|------|
| `--download` | 执行实际下载（默认仅预览） |
| `--report` | 生成下载状态报告 |
| `--workers N` | 并发下载数（默认：CPU 核心数 × 4） |
| `--keep-ts` | 保留 TS 分片文件用于调试 |
| `--history-from/--history-to` | 历史模式日期范围 (YYYY-MM-DD) |
| `--history-output` | 历史模式输出目录 |
| `--max-tasks N` | 最多下载 N 个 Day |

## 下载目录结构

```
downloads/
  <GroupName>/
    <PackageTitle>/
      Day1 .../
        video_1.mp4
        pdf_1.pdf
      Day2 .../
        video_1.mp4
        video_2.mp4
        pdf_1.pdf
  history_records/
    .download_manifest.json
    20250715_1350 数量关系第八课-容斥问题 xxx.mp4
    20250628_1434 资料分析第一课-变化 xxx.mp4
    ...
```

每个 Day 会新建独立文件夹，名称沿用课堂标题并自动移除非法字符。视频在合并成功后会删除 `tmp_ts/<视频名>/` 临时 TS 目录（除非指定 `--keep-ts`），方便重复运行和断点续传。

每个 package 目录下还会维护 `.download_manifest.json`，用于记录已下载的资源，下次运行时会自动跳过。

## 使用 .env 管理参数（可选）

为了避免每次在命令行里填写长串参数，可以复制 `.env.example` 为 `.env`，并写入常用配置：

```bash
cp .env.example .env
vim .env  # 或使用你熟悉的编辑器

# 编辑完成后即可直接运行 make / python 命令，程序会自动读取 .env
make run ALL_PACKAGES=1
```

常用变量：

| 变量 | 说明 |
|------|------|
| `LOGIN_PHONE` | 登录手机号 |
| `LOGIN_PASSWORD` | 登录密码 |
| `GROUP_ID` | 课程组 ID |
| `PACKAGE_ID` | 课程包 ID |
| `DOWNLOAD` | 设为 1 启用下载 |
| `WORKERS` | 并发数 |
| `TOKEN_CACHE` | Token 缓存路径 |

> `.env` 中可能包含 access-token 或登录密码等敏感信息，请妥善保管、避免提交到版本控制中。

## 技术架构

详见 [Agents.md](./Agents.md)，包含：
- API Agent 职责划分
- 视频存储类型检测逻辑
- OSS STS 认证流程
- 多分片下载合并策略
