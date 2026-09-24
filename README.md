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
| `stills` | 分集剧照的出处，逗号分隔，都是 TMDB API 路径：`tv/<id>/season/<n>`、`movie/<id>`、`collection/<id>` | `tv/65942/season/0,tv/65942/season/3` |
| `source` | `auto` 自动匹配，`manual` 人工修正 | `auto` |

表里没有的条目，要么在 TMDB 上没匹配到（或人工确认没有），要么还没查过，调用方照常自己搜。

`map/meta.json` 记着生成日期、用的哪一份 Bangumi 导出、匹配器的提交，以及条目数。

## 核对与人工修正

页面上按 id 或名字查条目，能看到：

- Bangumi 侧：原名、中文名、别名、平台、话数、评分排名、标签；
- 自动匹配的结果：背景图预览、所属 TMDB 条目的名字/原名/日期/语言/类型/评分/简介/各季，命中的搜索词，以及搜索中出现过的其他条目（备选）。

这些都取自 Bangumi 导出与匹配时已经收到的 TMDB 响应，没有为页面多发请求。

发现匹配错了，在页面底部的「人工修正」里填对的 TMDB 条目（可以直接粘贴 TMDB 网址）和背景图（可以粘贴图片地址），或者勾选「TMDB 上确实没有对应」。点提交会打开 GitHub 的新建文件页，内容已经填好，确认提交即可（只有仓库所有者能提交）。

修正存成 `overrides/<bgm_id>.json`：

```json
{
  "backdrop": "tv/65942",
  "backdrop_path": "/7ZruEnSnHD6Jx5mF0hBt1E306Vt.jpg",
  "stills": ["tv/65942/season/1"],
  "title": "Re:从零开始的异世界生活",
  "note": "新编集版挂回本传",
  "updated": "2026-09-24",
  "auto_was": "tv/330431 /hJwgKSIb5qNWbb98loQyjJFhwPC.jpg"
}
```

- `{"none": true}` 表示确认 TMDB 上没有对应，这个条目不进对应表；
- 除 `backdrop` / `stills` / `none` 至少有一项外，其他字段都可省；`auto_was` 是修正时的自动结果，留着方便回看。

推送修正后，`apply-overrides` 工作流几分钟内把它并进对应表与页面。**有修正的条目不再自动匹配，自动结果也不会覆盖它**；删掉修正文件（页面上的「撤销人工修正」）就回到自动匹配，下一轮重新查。修正文件格式不对时那一轮会失败并通知，不会提交任何东西。

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
docs/                  核对页面 (GitHub Pages) 与它的数据: data/index.tsv 全部条目一览, data/s/*.json 条目详情
state/state.tsv        每个条目上次检查的日期、结果与输入指纹 (决定下次什么时候查)
scripts/               ci.sh 每日更新的全部步骤 (工作流与本地共用), prepare.py 挑任务, merge.py 合并结果与人工修正
runner/                匹配器入口; 工作流把它拷进 izuko-tv 的测试源码里运行
matcher.ref            用 izuko-tv 的哪个分支/标签
.github/workflows/     update 每日更新, apply-overrides 推送修正后立即应用
```

## 本地验证

改了脚本或工作流，先在 Linux (WSL 的 Ubuntu 24.04 即可, 与 `ubuntu-latest` 同版本) 里用同一份脚本跑通再推：

```
export JAVA_HOME=<带 JCEF 的 JBR 21>  TMDB_API_TOKEN=<TMDB 读取令牌>
ONLY_IDS=237,311,296195 scripts/ci.sh local   # 下载导出 → 挑任务 → 取匹配器 → 匹配 → 合并, 不提交
```

结果写在仓库下的 `.work/` 与 `map/`、`docs/`、`state/` 里, 看完用 `git checkout -- map docs state` 丢掉。

## 数据来源与署名

- 条目与关联数据来自 [Bangumi 番组计划](https://bgm.tv) 的官方数据导出。
- TMDB 条目信息与图片来自 [TMDB](https://www.themoviedb.org)。This product uses the TMDB API but is not endorsed or certified by TMDB.
