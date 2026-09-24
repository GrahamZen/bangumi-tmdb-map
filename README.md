# bangumi-tmdb-map

Bangumi 动画条目 → TMDB 的对应表。每天由 GitHub Actions 自动更新，用 [Izuko TV](https://github.com/GrahamZen/izuko-tv) 自己的 TMDB 匹配器离线跑出来。有了它，客户端查一次表就知道去 TMDB 取哪个条目，不用在设备上逐个别名去搜。

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
| `backdrop_path` | 背景图路径，拼在 `https://image.tmdb.org/t/p/w1280` 之类的尺寸前缀后面 | `/abc.jpg` |
| `stills` | 分集剧照的出处，逗号分隔，都是 TMDB API 路径：`tv/<id>/season/<n>`、`movie/<id>`、`collection/<id>` | `tv/65942/season/0,tv/65942/season/3` |

表里没有的条目，要么在 TMDB 上没匹配到，要么还没查过，调用方照常自己搜。

`map/meta.json` 记着生成日期、用的哪一份 Bangumi 导出、匹配器的提交，以及条目数。

## 怎么来的

- **Bangumi 数据**：来自官方每周的数据导出 [bangumi/Archive](https://github.com/bangumi/Archive)，整个过程不请求 Bangumi 接口。匹配器回溯系列时要查的关联关系，也由导出现场合成。
- **匹配**：直接跑 izuko-tv 的 `TmdbImageService`（与 app 同一份代码，喂进去的条目信息也经 app 自己的转换函数，与 app 逐字段一致），按详情页的口径跑整部背景图与分集剧照两条链。匹配器只给出图片地址，所属的 TMDB 条目从本次收到的 TMDB 响应里反查。
- **调度**（`scripts/prepare.py`）：
  - 新条目、Bangumi 侧信息变了（名字、别名、日期、分集）、上次请求失败的，排在前面；
  - 没匹配到的：近一年半的条目每周重查（新番开播前后 TMDB 常常还没收录），更老的每季度重查；
  - 匹配到的：每 4 到 5 个月重验一次（TMDB 的 API 条款要求缓存不超过 6 个月）。
- 有 TMDB 请求失败的条目不改表，旧结果照用，下一轮重跑。

## 目录

```
map/bgm-tmdb.tsv       对应表
map/meta.json          生成信息
state/state.tsv        每个条目上次检查的日期、结果与输入指纹 (决定下次什么时候查)
scripts/               prepare.py 挑任务, merge.py 合并结果
runner/                匹配器入口; 工作流把它拷进 izuko-tv 的测试源码里运行
matcher.ref            用 izuko-tv 的哪个分支/标签
.github/workflows/     每日更新
```

## 数据来源与署名

- 条目与关联数据来自 [Bangumi 番组计划](https://bgm.tv) 的官方数据导出。
- TMDB 条目 id 与图片路径来自 [TMDB](https://www.themoviedb.org)。This product uses the TMDB API but is not endorsed or certified by TMDB.
