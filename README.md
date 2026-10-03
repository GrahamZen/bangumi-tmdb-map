# bangumi-tmdb-map

Bangumi 动画条目 → TMDB 的对应表。每天由 GitHub Actions 自动更新，用 [Izuko TV](https://github.com/GrahamZen/izuko-tv) 自己的 TMDB 匹配器离线跑出来。有了它，客户端查一次表就知道去 TMDB 取哪个条目，不用在设备上逐个别名去搜。

核对与人工修正的页面：<https://grahamzen.github.io/bangumi-tmdb-map/>

## 取用

```
https://cdn.jsdelivr.net/gh/GrahamZen/bangumi-tmdb-map@main/map/bgm-tmdb.tsv
https://raw.githubusercontent.com/GrahamZen/bangumi-tmdb-map/main/map/bgm-tmdb.tsv
```

制表符分隔，`#` 开头的行是注释。每行一个条目：

| 列 | 含义 | 例 |
|---|---|---|
| `bgm_id` | Bangumi 条目 id | `425998` |
| `backdrop` | 整部背景图所属的 TMDB 条目：`tv/<id>`、`movie/<id>` 或 `collection/<id>`；空表示只有分集剧照 | `tv/65942` |
| `backdrop_path` | 背景图路径，拼在 `https://image.tmdb.org/t/p/w1280` 之类的尺寸前缀后面；人工修正可能只给条目不给图，此时为空 | `/abc.jpg` |
| `stills` | 分集数据 (剧照、时长、分集简介) 的出处，是 TMDB API 路径：`tv/<id>` 整部剧，按同样的规则全量索引；`tv/<id>/season/0` 只取 S0 (衍生作挂在本篇特别篇下的情形)；`movie/<id>` 单集电影；`collection/<id>` 合集 (客户端照旧自己搜) | `tv/65942` |
| `source` | `auto` 自动匹配，`manual` 人工修正 | `auto` |
| `episodes` | 每一集对应 TMDB 第几季第几集 (见下)；空表示没算出来，照 `stills` 全量索引、自己对集 | `S3E1` |
| `logos` | 标题 logo，按语言 (见下)；空表示还没查 | `o=ja ja=/a.png:2.383 zh=- en=/b.png:4.159` |

`episodes` 是空格分隔的几段，按离线用客户端同一套规则对出来的结果写：

- `S3E1`：本篇按集号从小到大，第 k 集对第 3 季第 1+k 集 (最常见，只写起点，之后新播的集照此往下接)；
- `1-12:S3E1` / `7:S3E8`：本篇集号 1–12 对 S3E1–E12；单集只写一个集号；
- `SP1-2:S0E5` / `SP12.1:S0E7`：其他类型带前缀 (SP / OP / ED / PV / MAD，没有类型的写 O)；Bangumi 夹在两集之间的特别篇集号 (如 12.1) 照原文写；
- 没写到的集 = 没对上。有了它，客户端只需取这几季的数据，不用逐季全拉、再按日期和集名对。

`logos` 是空格分隔的几段：`o=ja` 是 `backdrop` 那个 TMDB 条目的原语言；`ja=/a.png:2.383` 是这种语言用的 logo (路径拼在 `https://image.tmdb.org/t/p/w500` 之类的尺寸前缀后面) 与它的宽高比 (宽 / 高)，`-` 是这种语言没有合适的 logo、显示文字标题。每个条目都有日、中、英与原语言这几种；某种语言整段没写就是还没查。

TMDB 的 logo 只挂在整部剧上、不标属于哪一季，多季的剧自动挑的 (评分最高的那张) 可能是别的季的；这类错由人工修正纠正 (见下)。

人工确认 TMDB 上没有对应的条目也在表里，除 `source` 外各列为空，调用方不必再搜。表里没有的条目，要么自动匹配没找到，要么还没查过，调用方照常自己搜。

`map/meta.json` 记着生成日期、用的哪一份 Bangumi 导出、匹配器的提交，以及条目数。

## 核对与人工修正

页面上按 id 或名字查条目，能看到：

- Bangumi 侧：原名、中文名、别名、平台、话数、评分排名、标签；
- 自动匹配的结果：背景图预览、所属 TMDB 条目的名字/原名/日期/语言/类型/评分/简介/各季，命中的搜索词，以及搜索中出现过的其他条目（备选）。

这些都取自 Bangumi 导出与匹配时已经收到的 TMDB 响应，没有为页面多发请求。

发现匹配错了，在页面底部的「人工修正」里填对的 TMDB 条目（可以直接粘贴 TMDB 网址）和背景图（可以粘贴图片地址），或者勾选「TMDB 上确实没有对应」，点「提交修正请求」。任何人都可以提交（要登录 GitHub）：

1. 页面打开一个内容已经填好的 issue（表单在 `.github/ISSUE_TEMPLATE/correction.yml`，也可以直接在仓库里新建 issue 手填），确认后提交；
2. `correction` 工作流检查格式、向 TMDB 核实条目存在、背景图属于这个条目，生成一个只改 `overrides/<bgm_id>.json` 的 PR，把现在与改后的结果（含背景图预览）列在 PR 说明里，并在 issue 里回复；格式不对时在 issue 里说明哪里不对，改了 issue 会重新检查；
3. 维护者审核：合并后几分钟内写进对应表，issue 随之关闭；不采纳就关掉 PR，issue 一起关掉。提交者关掉 issue 也会撤回 PR。
   待审的修正请求 (条目与标题 logo) 可以在[审核页](https://grahamzen.github.io/bangumi-tmdb-map/review.html)一页看完：现在与改成的图并排，勾选后批量合并或不采纳，标题 logo 还能直接点另一张候选改掉 (改的是 issue，PR 跟着重新生成)。页面读 PR 说明末尾 HTML 注释里的数据 (`correction.py` 的 `review_comment`)；合并等操作要填一个只给本仓库 Contents / Pull requests / Issues 读写权限的 fine-grained token，只存在浏览器里。

仓库维护者也可以不经请求，点页面上的「新建修正文件」直接提交到 `main`。

修正存成 `overrides/<bgm_id>.json`：

```json
{
  "backdrop": "tv/65942",
  "backdrop_path": "/7ZruEnSnHD6Jx5mF0hBt1E306Vt.jpg",
  "stills": ["tv/65942"],
  "title": "Re:从零开始的异世界生活",
  "note": "新编集版挂回本传",
  "updated": "2026-09-24",
  "auto_was": "tv/330431 /hJwgKSIb5qNWbb98loQyjJFhwPC.jpg"
}
```

- `{"none": true}` 表示确认 TMDB 上没有对应；
- `stills` 只能给一个出处，省略就跟着 `backdrop` 那个条目；
- 除 `backdrop` / `stills` / `none` 至少有一项外，其他字段都可省；`auto_was` 是修正时的自动结果，留着方便回看。

### 标题 logo 的修正

标题 logo 按条目、按语言修正，一种语言一个文件 `logo-overrides/<bgm_id>.<语言>.json` (同一条目不同语言的修正各改各的文件，同时开着的 PR 不冲突)，例如 `135275.ja.json`：

```json
{
  "tmdb": "tv/65844",
  "logo": "/8sW8IRMpZNvPHtTZIBxbCjyYEfX.png",
  "aspect": 2.112,
  "auto_was": "/pJEQ2jW2BKsHOLqWqqLO83muRm2.png:2.383",
  "title": "为美好的世界献上祝福！",
  "note": "自动挑的是第三季的 (修正请求 #12)",
  "updated": "2026-10-03"
}
```

- `tmdb` 是这条修正针对的 TMDB 条目，要和对应表里这个条目现在的 `backdrop` 一致才生效 (条目被改过，旧的 logo 修正就作废)；
- 要么给 `logo` 与 `aspect` (宽 / 高)，要么 `"none": true` (这种语言不用 logo，显示文字)；没有文件的语言照自动挑的。

修正请求用 `.github/ISSUE_TEMPLATE/logo.yml` 这张表单 (Izuko TV 详情页「反馈」里的「标题 logo 不对」会自动提交它)，`correction` 工作流向 TMDB 核实这张图属于这个条目、取宽高比，生成只改 `logo-overrides/<bgm_id>.<语言>.json` 的 PR，说明里列出现在与改后的 logo、这个条目各种语言的全部 logo 和条目对应 TMDB 的第几季。合并后同样由 `apply-overrides` 应用。

自动挑的 logo 由 `logos` 工作流每天查一轮 (`scripts/logos.py`，按 TMDB 条目存在 `state/logos.tsv`)：没查过的条目先查，查到过 logo 的 60 天、一种也没有的 14 天后再查。

推送修正后，`apply-overrides` 工作流几分钟内把它并进对应表与页面；客户端经 jsDelivr 取表，它的部分节点不认主动刷新、最长缓存 12 小时，加上客户端每天查一次表，一般一天内用上。**有修正的条目不再自动匹配，自动结果也不会覆盖它**；删掉修正文件（页面上的「撤销人工修正」）就回到自动匹配，下一轮重新查。修正文件格式不对时那一轮会失败并通知，不会提交任何东西。

## 怎么来的

- **Bangumi 数据**：来自官方每周的数据导出 [bangumi/Archive](https://github.com/bangumi/Archive)，整个过程不请求 Bangumi 接口。匹配器回溯系列时要查的关联关系，也由导出现场合成。
- **匹配**：直接跑 izuko-tv 的 `TmdbImageService`（与 app 同一份代码，喂进去的条目信息也经 app 自己的转换函数，与 app 逐字段一致），按详情页的口径跑整部背景图与分集剧照两条链。匹配器只给出图片地址，所属的 TMDB 条目从本次收到的 TMDB 响应里反查。
- **调度**（`scripts/prepare.py`）：
  - 新条目、Bangumi 侧信息变了（名字、别名、日期、分集）、上次请求失败的，排在前面；
  - 没匹配到的：近一年半的条目每周重查（新番开播前后 TMDB 常常还没收录），更老的每季度重查；
  - 匹配到的：每 4 到 5 个月重验一次（TMDB 的 API 条款要求缓存不超过 6 个月）；
  - 有人工修正的：不查。
- 有 TMDB 请求失败的条目不改表，旧结果照用，下一轮重跑。

## 目录

```
map/bgm-tmdb.tsv       对应表
map/meta.json          生成信息
overrides/             人工修正, 每个条目一个 <bgm_id>.json
logo-overrides/        标题 logo 的人工修正, 每个条目每种语言一个 <bgm_id>.<语言>.json
docs/                  核对页面 (GitHub Pages) 与它的数据: data/index.tsv 全部条目一览, data/s/*.json 条目详情
state/state.tsv        每个条目上次检查的日期、结果与输入指纹 (决定下次什么时候查)
state/logos.tsv        每个 TMDB 条目自动挑的标题 logo 与查的日期
scripts/               ci.sh 每日更新的全部步骤 (工作流与本地共用), prepare.py 挑任务, merge.py 合并结果与人工修正,
                       correction.py / logo_correction.py / correction_pr.sh 处理修正请求, logos.py 挑标题 logo
runner/                匹配器入口; 工作流把它拷进 izuko-tv 的测试源码里运行
worker/                标题 logo 报告的中转 (Cloudflare Worker), 部署见 worker/README.md
report-endpoints.json  中转地址清单 (Izuko TV 每天拉一次; 部署时 worker 工作流补上)
matcher.ref            用 izuko-tv 的哪个分支/标签
.github/workflows/     update 每日更新, logos 每日查标题 logo, apply-overrides 推送修正后立即应用, correction 修正请求 → PR,
                       worker 部署报告中转
.github/ISSUE_TEMPLATE/ 修正请求的表单 (条目 correction.yml, 标题 logo logo.yml); 审核页是 docs/review.html
```

## 本地验证

改了脚本或工作流，先在 Linux (WSL 的 Ubuntu 24.04 即可, 与 `ubuntu-latest` 同版本) 里用同一份脚本跑通再推：

```
export JAVA_HOME=<带 JCEF 的 JBR 21>  TMDB_API_TOKEN=<TMDB 读取令牌>
ONLY_IDS=237,311,296195 scripts/ci.sh local   # 下载导出 → 挑任务 → 取匹配器 → 匹配 → 合并, 不提交
```

结果写在仓库下的 `.work/` 与 `map/`、`docs/`、`state/` 里, 看完用 `git checkout -- map docs state` 丢掉。

修正请求的解析与校验有单测：`python3 -m unittest discover -s scripts`。

## 数据来源与署名

- 条目与关联数据来自 [Bangumi 番组计划](https://bgm.tv) 的官方数据导出。
- TMDB 条目信息与图片来自 [TMDB](https://www.themoviedb.org)。This product uses the TMDB API but is not endorsed or certified by TMDB.
