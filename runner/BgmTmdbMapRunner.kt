/*
 * bangumi-tmdb-map 的批量匹配器. 由 bangumi-tmdb-map 仓库的工作流拷进 izuko-tv 的
 * app/shared/app-data/src/desktopTest/kotlin/data/network/ 再运行, 不属于 app 仓库.
 */

package me.him188.ani.app.data.network

import io.ktor.client.HttpClient
import io.ktor.client.HttpClientConfig
import io.ktor.client.call.HttpClientCall
import io.ktor.client.call.save
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.ClientRequestException
import io.ktor.client.plugins.HttpSend
import io.ktor.client.plugins.ResponseException
import io.ktor.client.plugins.Sender
import io.ktor.client.plugins.ServerResponseException
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.plugins.plugin
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.request
import io.ktor.client.request.takeFrom
import io.ktor.client.request.url
import io.ktor.client.statement.bodyAsText
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.Url
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.todayIn
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put
import me.him188.ani.app.data.models.episode.EpisodeCollectionInfo
import me.him188.ani.app.data.models.episode.EpisodeInfo
import me.him188.ani.app.data.models.subject.SubjectInfo
import me.him188.ani.app.data.models.subject.toTmdbMatchHints
import me.him188.ani.app.data.network.mapper.toEntity
import me.him188.ani.app.data.network.mapper.toEpisodeTypeOrNull
import me.him188.ani.app.data.persistent.MemoryDataStore
import me.him188.ani.app.domain.foundation.DefaultHttpClientProvider
import me.him188.ani.app.domain.foundation.ScopedHttpClientFeatureHandler
import me.him188.ani.app.domain.foundation.ScopedHttpClientUserAgent
import me.him188.ani.app.domain.foundation.ServerListFeatureHandler
import me.him188.ani.app.domain.foundation.UserAgentFeature
import me.him188.ani.app.domain.foundation.UserAgentFeatureHandler
import me.him188.ani.app.domain.settings.NoProxyProvider
import me.him188.ani.app.platform.currentAniBuildConfig
import me.him188.ani.datasources.api.EpisodeSort
import me.him188.ani.datasources.api.EpisodeType
import me.him188.ani.datasources.api.PackedDate
import me.him188.ani.datasources.api.topic.UnifiedCollectionType
import me.him188.ani.datasources.api.toLocalDateOrNull
import me.him188.ani.datasources.bangumi.next.models.BangumiNextInfoboxItem
import me.him188.ani.datasources.bangumi.next.models.BangumiNextInfoboxValue
import me.him188.ani.datasources.bangumi.next.models.BangumiNextSubject
import me.him188.ani.datasources.bangumi.next.models.BangumiNextSubjectAirtime
import me.him188.ani.datasources.bangumi.next.models.BangumiNextSubjectPlatform
import me.him188.ani.datasources.bangumi.next.models.BangumiNextSubjectRating
import me.him188.ani.datasources.bangumi.next.models.BangumiNextSubjectType
import me.him188.ani.utils.serialization.BigNum
import java.io.File
import java.io.IOException
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicInteger
import kotlin.test.Test
import kotlin.time.Clock

/**
 * 对每个任务条目跑真实的 [TmdbImageService] —— 详情页口径的整部背景图 + 分集剧照两条链 ——
 * 记下结果落在 TMDB 的哪个条目上.
 *
 * - **不向 Bangumi 发请求**: 匹配器回溯系列时查的两个关联接口 (`/v0/subjects/{id}/subjects`、
 *   `/p1/subjects/{id}/relations`) 由 Bangumi 官方数据导出现场合成回答, 排序与接口相同 (order, id).
 *   条目本身的输入 (原名/中文名/信息框/开播日/分集) 也来自导出, 经 app 自己的
 *   [BangumiNextSubject.toEntity] 与 [SubjectInfo.toTmdbMatchHints] 变成匹配提示, 与 app 逐字段一致.
 * - **TMDB 是真实请求**: 全局限速, 429/5xx/网络错误退避重试. 仍有请求失败的任务整条记为失败,
 *   下一轮重跑 —— 匹配器有几处子步骤会把失败吞成空结果, 不能把"没查完"记成"没有".
 * - 匹配器只返回图片地址, 出处 (tv/movie/collection id 与季) 从本任务收到的 TMDB 响应里反查.
 *
 * 环境变量:
 * - `BGM_TMDB_WORK`: prepare.py 的输出目录 (jobs.jsonl / anime.jsonl / relations.jsonl); 不设就跳过
 * - `BGM_TMDB_OUT`: 结果文件 (jsonl, 逐条追加, 进程中途被杀也不丢已完成的)
 * - `BGM_TMDB_DEADLINE`: epoch 秒, 过了就不再开新任务
 * - `BGM_TMDB_CONCURRENCY` (默认 6) / `BGM_TMDB_RPS` (默认 30)
 */
class BgmTmdbMapRunner {
    @Test
    fun run() {
        val workDir = System.getenv("BGM_TMDB_WORK") ?: run {
            println("跳过: 设 BGM_TMDB_WORK 才跑")
            return
        }
        check(currentAniBuildConfig.tmdbApiToken.isNotBlank()) { "没有 TMDB token (local.properties 的 ani.tmdb.api.token)" }
        val out = File(System.getenv("BGM_TMDB_OUT") ?: "$workDir/results.jsonl")
        val deadline = System.getenv("BGM_TMDB_DEADLINE")?.toLongOrNull() ?: Long.MAX_VALUE
        val concurrency = System.getenv("BGM_TMDB_CONCURRENCY")?.toIntOrNull() ?: 6
        val limiter = RateLimiter(System.getenv("BGM_TMDB_RPS")?.toDoubleOrNull() ?: 30.0)

        val archive = BgmArchive.load(File(workDir))
        val jobsFile = File(workDir, "jobs.jsonl")
        val done = if (out.isFile) {
            out.useLines { lines -> lines.mapNotNull { runCatching { json.decodeFromString(Out.serializer(), it).id }.getOrNull() }.toSet() }
        } else {
            emptySet()
        }
        // 任务逐行读: 测试进程默认只有 512M 堆, 整批装进来放不下
        val total = jobsFile.useLines { lines -> lines.count { it.isNotBlank() } }
        println("任务 $total 个, 已完成 ${done.size}; 并发 $concurrency")

        val started = System.currentTimeMillis()
        val finished = AtomicInteger()
        val okCount = AtomicInteger()
        val hitCount = AtomicInteger()
        val outLock = Mutex()
        runBlocking(Dispatchers.IO) {
            val permits = Semaphore(concurrency)
            jobsFile.useLines { lines ->
                for (line in lines) {
                    if (line.isBlank()) continue
                    val job = json.decodeFromString(Job.serializer(), line)
                    if (job.id in done) continue
                    permits.acquire()
                    if (System.currentTimeMillis() / 1000 >= deadline) {
                        permits.release()
                        println("到截止时间, 不再开新任务")
                        break
                    }
                    launch {
                        try {
                            val result = runJob(job, archive, limiter)
                            outLock.withLock { out.appendText(json.encodeToString(Out.serializer(), result) + "\n") }
                            if (result.ok) okCount.incrementAndGet()
                            if (result.backdrop != null || result.stills.isNotEmpty()) hitCount.incrementAndGet()
                            val n = finished.incrementAndGet()
                            if (n % 50 == 0) {
                                val secs = (System.currentTimeMillis() - started) / 1000.0
                                println(
                                    "[$n] ok=${okCount.get()} hit=${hitCount.get()} " +
                                            "tmdb=${limiter.issued.get()} (${"%.1f".format(limiter.issued.get() / secs)}/s) " +
                                            "${"%.0f".format(secs)}s",
                                )
                            }
                        } finally {
                            permits.release()
                        }
                    }
                }
            }
        }
        println("完成 ${finished.get()} 个: ok=${okCount.get()} hit=${hitCount.get()}")
    }

    private suspend fun runJob(job: Job, archive: BgmArchive, limiter: RateLimiter): Out {
        val begin = System.currentTimeMillis()
        val handler = MapFeatureHandler(archive, limiter)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val provider = DefaultHttpClientProvider(
            NoProxyProvider, scope,
            featureHandlers = listOf(
                handler,
                ServerListFeatureHandler(flowOf(listOf(Url("https://auth.myani.org/")))),
            ),
        )
        try {
            val subjectInfo = job.toSubjectInfo()
            val hints = subjectInfo.toTmdbMatchHints()
            val airDate = subjectInfo.airDate.toLocalDateOrNull()?.toString()
            val service = TmdbImageService(
                httpClientProvider = provider,
                dataStore = MemoryDataStore(TmdbImageCache()),
                ioDispatcher = Dispatchers.IO,
            )
            val backdropUrl = service.getBackdropUrl(job.id, subjectInfo.name, activeAsOfDate = airDate, hints = hints)
            // 背景图的出处只从这一步的响应里找: 剧照链会另外取合集/电影详情, 同一张图可能也挂在那些条目上
            val backdropIndex = OriginIndex(handler.tmdbBodies.toList())
            val today = Clock.System.todayIn(TimeZone.of("Asia/Shanghai")).toString()
            val stills = service.getEpisodeStills(
                subjectId = job.id,
                originalName = subjectInfo.name,
                language = STILLS_LANGUAGE,
                newestWantedAirDate = job.episodes.mapNotNull { it.airDateOrNull() }.filter { it <= today }.maxOrNull(),
                subjectAirDate = airDate,
                subjectEpisodeCount = job.episodes.size,
                subjectEpisodeNames = job.episodes.map { it.name },
                hints = hints,
            )
            val stillsIndex = OriginIndex(handler.tmdbBodies.toList())
            val backdropPath = backdropUrl?.let { imagePath(it) }
            val stillPaths = stills?.let { s ->
                (s.byAirDate.values.flatten() + s.byEpisodeNumber.values + s.byEpisodeName.values)
                    .mapNotNull { it.stillUrl?.let(::imagePath) }
                    .distinct()
            }.orEmpty()
            val unresolved = listOfNotNull(backdropPath).count { backdropIndex.refsOf(it).isEmpty() } +
                    stillPaths.count { stillsIndex.refsOf(it).isEmpty() }
            val backdropRef = backdropPath?.let { backdropIndex.refsOf(it).firstOrNull() }
            val stillRefs = stillsIndex.stillRefsOf(stillPaths)
            val stillsSource = stillsSourceOf(handler.tmdbBodies.toList(), stillRefs, stills)
            val episodeInfos = job.episodeInfos()
            val episodeMap = stillsSource?.let { src ->
                stills?.let { episodeMapOf(src, it, episodeInfos, airDate, handler.tmdbBodies.toList()) }
            }
            val chosen = (backdropRef ?: stillRefs.firstOrNull())?.substringBefore("/season/")
            return Out(
                id = job.id,
                ok = handler.failures.isEmpty() && handler.blocked.isEmpty(),
                backdrop = backdropRef,
                backdropPath = backdropPath,
                stills = stillRefs,
                stillsSource = stillsSource,
                episodes = episodeMap,
                stillCount = stillPaths.size,
                tmdb = chosen?.let { stillsIndex.entityOf(it) },
                hitQuery = chosen?.let { stillsIndex.firstHitOf(it) },
                candidates = stillsIndex.candidates(exclude = chosen, limit = 6),
                requests = handler.tmdbRequests.get(),
                bgmCalls = handler.bgmCalls.get(),
                ms = System.currentTimeMillis() - begin,
                unresolvedPaths = unresolved,
                error = (handler.failures + handler.blocked.map { "blocked $it" }).take(3).joinToString("; ").ifEmpty { null },
            )
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return Out(
                id = job.id, ok = false,
                requests = handler.tmdbRequests.get(), bgmCalls = handler.bgmCalls.get(),
                ms = System.currentTimeMillis() - begin,
                error = "${e::class.simpleName}: ${e.message?.take(200)}",
            )
        } finally {
            withContext(NonCancellable) { provider.forceReleaseAll() }
            scope.cancel()
        }
    }

    /**
     * 剧照链最后用的是哪部剧/电影. 写进对应表, 客户端据此跳过搜索, 照同样的规则重建出一模一样的分集数据.
     *
     * - 剧集: `tv/<id>`, 客户端全量索引; 只索引了 S0 (衍生作挂在本篇的 S0 下) 时是 `tv/<id>/season/0`.
     *   是哪部: 结果里的剧照出自哪部就是哪部; 一张剧照都没有 (只有时长/简介) 时是第一部建过索引的
     *   —— 匹配器对空壳剧的回退也是它. 建过索引 = 以剧照语言请求过它的季 (按原语言取集名的请求不算).
     * - 电影或合集: 原样给出 (客户端对合集照旧搜).
     * - 什么数据都没有: null (客户端照旧搜, 结果同样是空).
     *
     * 只记"有剧照的季"不够: 没剧照的季也提供时长与分集简介, 按有剧照的季过滤会把它们丢掉
     * (锚点实测 252 条里 36 条对不上, 改成这样之后是 0).
     */
    private fun stillsSourceOf(bodies: List<MapTmdbBody>, stillRefs: List<String>, stills: TmdbEpisodeStills?): String? {
        if (stills == null || stills.isEmpty()) return null
        val indexed = linkedMapOf<Int, MutableSet<Int>>()
        val allSeasons = mutableMapOf<Int, Set<Int>>()
        for (b in bodies) {
            val seg = b.path.trim('/').split('/').let { if (it.firstOrNull() == "3") it.drop(1) else it }
            if (seg.firstOrNull() != "tv") continue
            val id = seg.getOrNull(1)?.toIntOrNull() ?: continue
            if (seg.size == 4 && seg[2] == "season" && b.language == STILLS_LANGUAGE) {
                val season = seg[3].toIntOrNull() ?: continue
                indexed.getOrPut(id) { linkedSetOf() }.add(season)
            } else if (seg.size == 2) {
                val element = runCatching { json.parseToJsonElement(b.body) }.getOrNull() as? JsonObject ?: continue
                allSeasons[id] = (element["seasons"] as? JsonArray).orEmpty()
                    .mapNotNull { ((it as? JsonObject)?.get("season_number") as? JsonPrimitive)?.intOrNull }
                    .toSet()
            }
        }
        val tvId = stillRefs.firstOrNull { it.startsWith("tv/") }?.split('/')?.getOrNull(1)?.toIntOrNull()
            ?: if (stillRefs.isEmpty()) indexed.keys.firstOrNull() else null
        if (tvId == null) return stillRefs.singleOrNull()
        val specialsOnly = indexed[tvId].orEmpty() == setOf(0) && allSeasons[tvId].orEmpty().any { it != 0 }
        return if (specialsOnly) "tv/$tvId/season/0" else "tv/$tvId"
    }

    private fun Job.toSubjectInfo(): SubjectInfo {
        val entity = BangumiNextSubject(
            airtime = BangumiNextSubjectAirtime(date = date, month = 0, weekday = 0, year = 0),
            collection = emptyMap(),
            eps = 0,
            id = id,
            infobox = infobox.map { item ->
                BangumiNextInfoboxItem(key = item.key, propertyValues = item.values.map { BangumiNextInfoboxValue(v = it.v, k = it.k) })
            },
            info = "",
            metaTags = emptyList(),
            locked = false,
            name = name,
            nameCN = nameCN,
            nsfw = nsfw,
            platform = BangumiNextSubjectPlatform(id = 0, type = "", typeCN = "", alias = ""),
            rating = BangumiNextSubjectRating(rank = 0, count = emptyList(), score = BigNum(0), total = 0),
            redirect = 0,
            series = false,
            seriesEntry = 0,
            summary = "",
            type = BangumiNextSubjectType.Anime,
            volumes = 0,
            tags = emptyList(),
        ).toEntity(lastFetched = 0)
        return SubjectInfo.Empty.copy(
            subjectId = entity.subjectId,
            name = entity.name,
            nameCn = entity.nameCn,
            airDate = entity.airDate,
            aliases = entity.aliases,
            screeningYear = entity.screeningYear,
            theatrical = entity.theatrical,
        )
    }

    /** 与 app 的 `EpisodeCollectionInfo.airDateStringOrNull` 同口径. */
    private fun Ep.airDateOrNull(): String? {
        val date = PackedDate.parseFromDate(airdate)
        if (date.isInvalid) return null
        return runCatching { LocalDate(date.year, date.month, date.day).toString() }.getOrNull()
    }

    private fun imagePath(url: String): String = "/" + url.substringAfterLast('/')

    @Serializable
    private data class Job(
        val id: Int,
        val name: String,
        val nameCN: String = "",
        val date: String = "",
        val nsfw: Boolean = false,
        val infobox: List<InfoboxItem> = emptyList(),
        val episodes: List<Ep> = emptyList(),
    )

    @Serializable
    private data class InfoboxItem(val key: String, val values: List<InfoboxValue> = emptyList())

    @Serializable
    private data class InfoboxValue(val v: String = "", val k: String? = null)

    @Serializable
    private data class Ep(
        val id: Int = 0,
        val type: Int = 0,
        val sort: Double = 0.0,
        val name: String = "",
        val nameCN: String = "",
        val airdate: String = "",
    )

    /**
     * 与 app 看到的分集列表逐字段一致: 字段照 `BangumiEpisode.toEntity` → `toEpisodeInfo` 的转换
     * (集号经数据库存取一次: 存的是 `EpisodeSort.toString()`, 读回来再解析), 顺序照
     * `EpisodeCollectionDao.filterBySubjectId` 的 `ORDER BY sortNumber ASC, sort ASC`.
     */
    private fun Job.episodeInfos(): List<EpisodeCollectionInfo> = episodes
        .map { e ->
            val type = e.type.toEpisodeTypeOrNull()
            val storedSort = EpisodeSort(BigNum(e.sort), type).toString()
            Triple(e.sort.toFloat(), storedSort, e to type)
        }
        .sortedWith(compareBy({ it.first }, { it.second }))
        .map { (_, storedSort, pair) ->
            val (e, type) = pair
            EpisodeCollectionInfo(
                episodeInfo = EpisodeInfo(
                    episodeId = e.id,
                    type = type,
                    name = e.name,
                    nameCn = e.nameCN,
                    airDate = PackedDate.parseFromDate(e.airdate),
                    sort = EpisodeSort(storedSort),
                ),
                collectionType = UnifiedCollectionType.NOT_COLLECTED,
            )
        }

    /**
     * 每一集对应 TMDB 第几季第几集: 用 app 自己的 [matchToEpisodes] 对出结果 (与设备上自己搜再对的一致),
     * 再从剧照链收到的各季响应里找出每一集是第几季第几集 (按 app 构造分集数据的同一规则比对内容),
     * 最后压成编码 (见 [encodeEpisodeMap]). 只对剧集出处做; 有一集找不出来就整条不给, 客户端照旧全量索引.
     *
     * 内容完全相同的占位集 (没图没简介、时长一样) 可能出现在好几个位置, 取哪个显示都一样:
     * 优先取"上一集的下一集", 编码才压得成区间. 什么都没有的空数据与"没对上"显示相同, 不记.
     */
    private fun episodeMapOf(
        stillsSource: String,
        stills: TmdbEpisodeStills,
        episodes: List<EpisodeCollectionInfo>,
        subjectAirDate: String?,
        bodies: List<MapTmdbBody>,
    ): String? {
        if (!stillsSource.startsWith("tv/")) return null
        val tvId = stillsSource.split('/')[1].toIntOrNull() ?: return null
        val assigned = stills.matchToEpisodes(episodes, subjectAirDate)
            .filterValues { it.stillUrl != null || it.runtimeMinutes != null || it.overview != null }
        if (assigned.isEmpty()) return null
        val positions = mutableMapOf<TmdbEpisodeMedia, MutableList<Pair<Int, Int>>>()
        for (b in bodies) {
            val seg = b.path.trim('/').split('/').let { if (it.firstOrNull() == "3") it.drop(1) else it }
            if (seg.size != 4 || seg[0] != "tv" || seg[1] != tvId.toString() || seg[2] != "season") continue
            if (b.language != STILLS_LANGUAGE) continue
            val season = seg[3].toIntOrNull() ?: continue
            val element = runCatching { json.parseToJsonElement(b.body) }.getOrNull() as? JsonObject ?: continue
            for (ep in (element["episodes"] as? JsonArray).orEmpty()) {
                val o = ep as? JsonObject ?: continue
                val number = (o["episode_number"] as? JsonPrimitive)?.intOrNull ?: continue
                val media = TmdbEpisodeMedia(
                    stillUrl = (o["still_path"] as? JsonPrimitive)?.contentOrNull?.let { "$STILL_IMAGE_BASE_URL$it" },
                    runtimeMinutes = (o["runtime"] as? JsonPrimitive)?.intOrNull?.takeIf { it > 0 },
                    overview = (o["overview"] as? JsonPrimitive)?.contentOrNull?.trim()?.takeIf { it.isNotBlank() },
                )
                val list = positions.getOrPut(media) { mutableListOf() }
                if (season to number !in list) list += season to number
            }
        }
        val entries = mutableListOf<EncodedEpisode>()
        var last: Pair<Int, Int>? = null
        for (e in episodes) {
            val media = assigned[e.episodeInfo.episodeId] ?: continue
            val candidates = positions[media] ?: return null
            val chosen = last?.let { (s, n) -> candidates.firstOrNull { it == s to n + 1 } }
                ?: candidates.firstOrNull { it.first == last?.first }
                ?: candidates.first()
            last = chosen
            val text = sortText(e.episodeInfo) ?: return null
            entries += EncodedEpisode(episodePrefix(e.episodeInfo.type), text, chosen.first, chosen.second)
        }
        val spec = encodeEpisodeMap(entries, episodes)
        // 自检: 按编码还原出来的必须与对出来的逐集相同
        val expected = episodes.mapNotNull { e ->
            val media = assigned[e.episodeInfo.episodeId] ?: return@mapNotNull null
            e.episodeInfo.episodeId to media
        }.toMap()
        val decoded = decodeEpisodeMap(spec, episodes).mapValues { (_, se) ->
            positions.entries.firstOrNull { se in it.value }?.key
        }
        return spec.takeIf { decoded == expected }
    }

    private class EncodedEpisode(val prefix: String, val text: String, val season: Int, val episode: Int) {
        val value: Double get() = text.toDouble()
        val integral: Boolean get() = '.' !in text
    }

    private fun episodePrefix(type: EpisodeType?): String = when (type) {
        EpisodeType.MainStory -> ""
        EpisodeType.SP -> "SP"
        EpisodeType.OP -> "OP"
        EpisodeType.ED -> "ED"
        EpisodeType.PV -> "PV"
        EpisodeType.MAD -> "MAD"
        EpisodeType.OVA, EpisodeType.OAD, null -> "O"
    }

    /**
     * 集号写法 (与 app 的 `TmdbEpisodeMap.keyOf` 一致): 能解析成数的写 `12` / `12.5`; 夹在两集之间的特别篇
     * `12.1` 这种 EpisodeSort 认不出来, 写存储时的原文. 写不成数的返回 null.
     */
    private fun sortText(info: EpisodeInfo): String? {
        val number = info.sort.number
        val text = if (number != null) {
            if (number == number.toInt().toFloat()) number.toInt().toString() else number.toString()
        } else {
            info.sort.toString()
        }
        return text.takeIf { NUMBER_TEXT.matches(it) }
    }

    /**
     * 压成编码 (与 app 的 `TmdbEpisodeMap` 对应): 本篇 (集号能解析成数的那些) 全部对上且一集接一集时只写起点 `S3E1`;
     * 否则按"集号逐个 +1、TMDB 集号也逐个 +1、同一季"切段 `1-12:S3E1`, 单集 `7:S3E8`; 其他类型带前缀 `SP1:S0E5`.
     */
    private fun encodeEpisodeMap(entries: List<EncodedEpisode>, episodes: List<EpisodeCollectionInfo>): String {
        val tokens = mutableListOf<String>()
        val continuationIds = episodes.map { it.episodeInfo }
            .filter { it.type == EpisodeType.MainStory && it.sort.number != null }
            .sortedBy { it.sort.number }
            .map { sortText(it) }
        val main = entries.filter { it.prefix == "" && it.text in continuationIds }.sortedBy { it.value }
        val continuation = main.isNotEmpty() && main.map { it.text } == continuationIds &&
            main.withIndex().all { (k, e) -> e.season == main[0].season && e.episode == main[0].episode + k }
        if (continuation) tokens += "S${main[0].season}E${main[0].episode}"
        val rest = if (continuation) entries.filter { it !in main } else entries
        for ((prefix, group) in rest.groupBy { it.prefix }) {
            val sorted = group.sortedBy { it.value }
            var i = 0
            while (i < sorted.size) {
                val start = sorted[i]
                var j = i
                while (start.integral && j + 1 < sorted.size) {
                    val cur = sorted[j]
                    val next = sorted[j + 1]
                    val consecutive = next.integral && next.value == cur.value + 1 &&
                        next.season == cur.season && next.episode == cur.episode + 1
                    if (consecutive) j++ else break
                }
                val range = if (j > i) "${start.text}-${sorted[j].text}" else start.text
                tokens += "$prefix$range:S${start.season}E${start.episode}"
                i = j + 1
            }
        }
        return tokens.joinToString(" ")
    }

    /** [encodeEpisodeMap] 的逆过程, 只给自检用 (app 那边是 `TmdbEpisodeMap`, 两边按同一份说明各写一份). */
    private fun decodeEpisodeMap(spec: String, episodes: List<EpisodeCollectionInfo>): Map<Int, Pair<Int, Int>> {
        val result = mutableMapOf<Int, Pair<Int, Int>>()
        val explicit = mutableMapOf<Pair<String, String>, Pair<Int, Int>>()
        for (token in spec.split(' ').filter { it.isNotEmpty() }) {
            val c = Regex("""^S(\d+)E(\d+)$""").matchEntire(token)
            if (c != null) {
                val (season, first) = c.destructured
                episodes.map { it.episodeInfo }
                    .filter { it.type == EpisodeType.MainStory && it.sort.number != null }
                    .sortedBy { it.sort.number }
                    .forEachIndexed { k, info -> result[info.episodeId] = season.toInt() to first.toInt() + k }
                continue
            }
            val m = Regex("""^([A-Z]*)(\d+(?:\.\d+)?)(?:-(\d+))?:S(\d+)E(\d+)$""").matchEntire(token) ?: continue
            val (prefix, from, to, season, first) = m.destructured
            if (to.isEmpty()) {
                explicit[prefix to from] = season.toInt() to first.toInt()
            } else {
                for (offset in 0..(to.toInt() - from.toInt())) {
                    explicit[prefix to (from.toInt() + offset).toString()] = season.toInt() to first.toInt() + offset
                }
            }
        }
        for (e in episodes) {
            val text = sortText(e.episodeInfo) ?: continue
            explicit[episodePrefix(e.episodeInfo.type) to text]?.let { result[e.episodeInfo.episodeId] = it }
        }
        return result
    }

    @Serializable
    private data class Out(
        val id: Int,
        val ok: Boolean,
        /** 背景图出处, 形如 `tv/65942`, `movie/9323`, `collection/404609` */
        val backdrop: String? = null,
        val backdropPath: String? = null,
        /** 有剧照的出处 (剧集的季、单集电影或合集), 形如 `tv/65942/season/3`; 只给核对页面看 */
        val stills: List<String> = emptyList(),
        /** 剧照链最后用的是哪部剧/电影, 写进对应表给客户端用, 见 [stillsSourceOf] */
        val stillsSource: String? = null,
        /** 每一集对应 TMDB 第几季第几集, 编码见 [encodeEpisodeMap]; 对不出来或不是剧集时为 null */
        val episodes: String? = null,
        val stillCount: Int = 0,
        /** backdrop (没有时取剧照出处) 那个 TMDB 条目的信息, 取自本任务已收到的响应, 人工核对用 */
        val tmdb: MapTmdbEntity? = null,
        /** 哪一次搜索把它搜了出来 (第一次出现的那次) */
        val hitQuery: MapSearchHit? = null,
        /** 本任务搜索结果里的其他条目 (按首次出现排序, 最多 6 个; 只留辨认用的字段), 人工修正时的备选 */
        val candidates: List<MapTmdbEntity> = emptyList(),
        val requests: Int = 0,
        val bgmCalls: Int = 0,
        val ms: Long = 0,
        /** 图片路径在本任务的 TMDB 响应里找不到出处的个数; 应当恒为 0 */
        val unresolvedPaths: Int = 0,
        val error: String? = null,
    )

    private companion object {
        val json = Json { ignoreUnknownKeys = true; encodeDefaults = false }

        /** 取分集数据用的语言, 与 app 在中文界面下一致 */
        const val STILLS_LANGUAGE = "zh-CN"

        val NUMBER_TEXT = Regex("""^\d+(?:\.\d+)?$""")

        /** 与 TmdbImageService 的 STILL_IMAGE_BASE_URL 一致 (分集数据按同一规则构造, 才比得上内容) */
        const val STILL_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/original"
    }
}

/** 全局限速: 所有任务共用, 按固定间隔放行. */
private class RateLimiter(rps: Double) {
    private val intervalNanos = (1_000_000_000 / rps).toLong()
    private val mutex = Mutex()
    private var next = System.nanoTime()
    val issued = AtomicInteger()

    suspend fun acquire() {
        val waitNanos = mutex.withLock {
            val now = System.nanoTime()
            val slot = maxOf(now, next)
            next = slot + intervalNanos
            slot - now
        }
        if (waitNanos > 0) delay(waitNanos / 1_000_000)
        issued.incrementAndGet()
    }
}

/**
 * 每个任务一份: 顶替一定会被请求的 [UserAgentFeatureHandler] (委托原实现), 在 [HttpSend] 最外层分流 ——
 * Bangumi 由导出合成回答, TMDB 限速重试并留下响应体, 其他域名一律拦下 (不依赖 bgm 与 TMDB 之外的服务).
 */
private class MapFeatureHandler(
    private val archive: BgmArchive,
    private val limiter: RateLimiter,
) : ScopedHttpClientFeatureHandler<ScopedHttpClientUserAgent>(UserAgentFeature) {
    /** 成功的 TMDB 响应, 供 [OriginIndex] 反查图片出处、取条目信息. */
    val tmdbBodies = ConcurrentLinkedQueue<MapTmdbBody>()
    val tmdbRequests = AtomicInteger()
    val bgmCalls = AtomicInteger()
    val failures = ConcurrentLinkedQueue<String>()
    val blocked = ConcurrentLinkedQueue<String>()

    override fun applyToConfig(config: HttpClientConfig<*>, value: ScopedHttpClientUserAgent) =
        UserAgentFeatureHandler.applyToConfig(config, value)

    override fun applyToClient(client: HttpClient, value: ScopedHttpClientUserAgent) {
        client.plugin(HttpSend).intercept { request ->
            val host = request.url.host
            when {
                host == "api.bgm.tv" || host == "next.bgm.tv" -> {
                    bgmCalls.incrementAndGet()
                    val url = request.url.build()
                    archive.call(url) ?: run {
                        blocked.add("bgm ${url.encodedPath}")
                        throw IOException("bangumi-tmdb-map: no archive answer for ${url.encodedPath}")
                    }
                }

                host.endsWith("tmdb.org") || host.endsWith("themoviedb.org") -> tmdb(request)
                else -> {
                    blocked.add(host)
                    throw IOException("bangumi-tmdb-map: blocked request to $host")
                }
            }
        }
    }

    private suspend fun Sender.tmdb(request: HttpRequestBuilder): HttpClientCall {
        val url = request.url.build()
        val path = url.encodedPath
        request.headers[HttpHeaders.UserAgent] = USER_AGENT
        var attempt = 0
        while (true) {
            attempt++
            limiter.acquire()
            tmdbRequests.incrementAndGet()
            val retryAfterMillis: Long
            try {
                val saved = execute(HttpRequestBuilder().takeFrom(request)).save()
                tmdbBodies.add(MapTmdbBody(path, url.parameters["query"], url.parameters["language"], saved.response.bodyAsText()))
                return saved
            } catch (e: CancellationException) {
                throw e
            } catch (e: ClientRequestException) {
                when (e.response.status.value) {
                    404 -> throw e // 季/条目不存在是正常结果
                    429 -> retryAfterMillis = (e.response.headers[HttpHeaders.RetryAfter]?.toLongOrNull() ?: 2) * 1000
                    else -> {
                        failures.add("$path -> ${e.response.status.value}")
                        throw e
                    }
                }
            } catch (e: ServerResponseException) {
                retryAfterMillis = 2000L * attempt
            } catch (e: ResponseException) {
                failures.add("$path -> ${e.response.status.value}")
                throw e
            } catch (e: Exception) {
                // 连不上 / 超时
                if (attempt >= MAX_ATTEMPTS) {
                    failures.add("$path -> ${e::class.simpleName}")
                    throw e
                }
                retryAfterMillis = 2000L * attempt
            }
            if (attempt >= MAX_ATTEMPTS) {
                failures.add("$path -> gave up after $attempt attempts")
                throw IOException("TMDB $path: gave up after $attempt attempts")
            }
            delay(retryAfterMillis)
        }
    }

    private companion object {
        const val MAX_ATTEMPTS = 4
        const val USER_AGENT = "bangumi-tmdb-map (+https://github.com/GrahamZen/bangumi-tmdb-map)"
    }
}

/**
 * Bangumi 官方数据导出 (经 prepare.py 精简) 合成的关联接口.
 *
 * 只收动画条目之间的关联: 匹配器对 v0 的结果先按类型过滤出动画, p1 的请求本身带 `type=2`.
 */
private class BgmArchive(
    private val anime: Map<Int, Slim>,
    private val relations: Map<Int, List<List<Int>>>,
) {
    @Serializable
    data class Slim(val id: Int, val name: String, val nameCN: String = "", val date: String = "", val eps: Int = 0, val nsfw: Boolean = false)

    @Serializable
    data class Rel(val id: Int, val rel: List<List<Int>>)

    private val client = HttpClient(
        MockEngine { request ->
            val (status, body) = answer(request.url) ?: NOT_FOUND
            respond(body, HttpStatusCode.fromValue(status), headersOf(HttpHeaders.ContentType, "application/json"))
        },
    ) {
        expectSuccess = false // 非 2xx 由 call() 按生产 client 的 expectSuccess 语义抛
        install(ContentNegotiation) {
            json(Json { ignoreUnknownKeys = true; isLenient = true }) // 与 createDefaultHttpClient 一致
        }
    }

    /** 导出答不了的路径返回 null (匹配器改了查法时会出现, 调用方记为失败). */
    suspend fun call(url: Url): HttpClientCall? {
        if (answer(url) == null) return null
        val response = client.request(HttpRequestBuilder().apply { url(url) })
        if (response.status.value in 400..499) throw ClientRequestException(response, response.bodyAsText())
        return response.call
    }

    private fun answer(url: Url): Pair<Int, String>? {
        val segments = url.encodedPath.trim('/').split('/')
        // /v0/subjects/{id}/subjects
        if (segments.size == 4 && segments[0] == "v0" && segments[1] == "subjects" && segments[3] == "subjects") {
            val id = segments[2].toIntOrNull() ?: return NOT_FOUND
            if (id !in anime) return NOT_FOUND
            val body = buildJsonArray {
                for ((relatedId, type) in edgesOf(id)) {
                    val related = anime[relatedId] ?: continue
                    add(
                        buildJsonObject {
                            put("id", related.id)
                            put("type", 2)
                            put("name", related.name)
                            put("name_cn", related.nameCN)
                            put("relation", RELATION_NAMES[type] ?: "其他")
                        },
                    )
                }
            }
            return 200 to body.toString()
        }
        // /p1/subjects/{id}/relations?type=2&limit=&offset=
        if (segments.size == 4 && segments[0] == "p1" && segments[1] == "subjects" && segments[3] == "relations") {
            val id = segments[2].toIntOrNull() ?: return NOT_FOUND
            if (id !in anime) return NOT_FOUND
            val edges = edgesOf(id).filter { it.first in anime }
            val offset = url.parameters["offset"]?.toIntOrNull() ?: 0
            val limit = url.parameters["limit"]?.toIntOrNull() ?: 20
            val page = edges.drop(offset).take(limit)
            val body = buildJsonObject {
                put(
                    "data",
                    buildJsonArray {
                        for ((relatedId, type) in page) {
                            val related = anime.getValue(relatedId)
                            add(
                                buildJsonObject {
                                    put("subject", related.toSlimJson())
                                    put(
                                        "relation",
                                        buildJsonObject {
                                            put("id", type)
                                            put("en", "")
                                            put("cn", RELATION_NAMES[type] ?: "其他")
                                            put("jp", "")
                                            put("desc", "")
                                        },
                                    )
                                    put("order", 0)
                                },
                            )
                        }
                    },
                )
                put("total", edges.size)
            }
            return 200 to body.toString()
        }
        return null
    }

    /** (关联条目 id, 关联类型), 已按 (order, id) 排好. */
    private fun edgesOf(id: Int): List<Pair<Int, Int>> = relations[id].orEmpty().map { it[0] to it[1] }

    private fun Slim.toSlimJson(): JsonObject = buildJsonObject {
        put("id", id)
        put("name", name)
        put("nameCN", nameCN)
        put("type", 2)
        put("info", slimInfo())
        put("metaTags", JsonArray(emptyList()))
        put(
            "rating",
            buildJsonObject {
                put("rank", 0)
                put("count", JsonArray(emptyList()))
                put("score", 0)
                put("total", 0)
            },
        )
        put("locked", false)
        put("nsfw", nsfw)
    }

    /** 与 p1 精简条目的 `info` 同格式的前两段: `26话 / 2004年7月4日`. */
    private fun Slim.slimInfo(): String {
        val parts = mutableListOf<String>()
        if (eps > 0) parts += "${eps}话"
        val d = PackedDate.parseFromDate(date)
        if (!d.isInvalid) parts += "${d.year}年${d.month}月${d.day}日"
        return parts.joinToString(" / ")
    }

    companion object {
        private val NOT_FOUND = 404 to """{"title":"Not Found"}"""

        /** bangumi/common subject_relations.yml 的动画关联表. */
        private val RELATION_NAMES = mapOf(
            1 to "改编", 2 to "前传", 3 to "续集", 4 to "总集篇", 5 to "全集", 6 to "番外篇", 7 to "角色出演",
            8 to "相同世界观", 9 to "不同世界观", 10 to "不同演绎", 11 to "衍生", 12 to "主线故事", 14 to "联动", 99 to "其他",
        )

        private val json = Json { ignoreUnknownKeys = true }

        fun load(dir: File): BgmArchive {
            val anime = File(dir, "anime.jsonl").useLines { lines ->
                lines.filter { it.isNotBlank() }.map { json.decodeFromString(Slim.serializer(), it) }.associateBy { it.id }
            }
            val relations = File(dir, "relations.jsonl").useLines { lines ->
                lines.filter { it.isNotBlank() }.map { json.decodeFromString(Rel.serializer(), it) }.associate { it.id to it.rel }
            }
            return BgmArchive(anime, relations)
        }
    }
}

/** 一个成功的 TMDB 响应. */
private class MapTmdbBody(val path: String, val query: String?, val language: String?, val body: String)

/** TMDB 条目的概要, 全部取自搜索结果/详情里本来就有的字段. */
@Serializable
private data class MapTmdbEntity(
    val ref: String,
    val name: String,
    val original: String? = null,
    val date: String? = null,
    val overview: String? = null,
    val lang: String? = null,
    val countries: List<String> = emptyList(),
    val genres: List<Int> = emptyList(),
    val vote: Double? = null,
    val votes: Int? = null,
    val poster: String? = null,
    val backdrop: String? = null,
    /** 以下只有取过详情的剧集才有 */
    val status: String? = null,
    val episodes: Int? = null,
    val seasons: List<MapTmdbSeason> = emptyList(),
)

@Serializable
private data class MapTmdbSeason(val n: Int, val name: String? = null, val date: String? = null, val eps: Int? = null)

@Serializable
private data class MapSearchHit(val kind: String, val query: String, val lang: String? = null, val rank: Int)

/**
 * 「图片路径 → 出处」: 从本任务收到的 TMDB 响应里收集. 同一路径可能出现在多处 (合集与其中的电影、
 * 搜索结果与详情), 按可信度排: 图片列表/分季 > 详情 > 搜索结果.
 *
 * 顺带整理各条目的概要与搜索记录 (响应里本来就有), 给人工核对用, 不为此多发请求.
 */
private class OriginIndex(bodies: Collection<MapTmdbBody>) {
    private val refs = mutableMapOf<String, MutableList<Pair<Int, String>>>()

    /** ref -> (可信度, 概要); 详情覆盖搜索结果 */
    private val entities = linkedMapOf<String, Pair<Int, MapTmdbEntity>>()

    /** 按请求顺序: (搜索记录, 该次结果里的 ref 列表) */
    private val searches = mutableListOf<Pair<MapSearchHit, List<String>>>()

    init {
        val parser = Json { ignoreUnknownKeys = true }
        for (b in bodies) {
            val rawPath = b.path
            val element = runCatching { parser.parseToJsonElement(b.body) }.getOrNull() as? JsonObject ?: continue
            // 形如 /3/tv/65942/season/3; 去掉版本号
            val seg = rawPath.trim('/').split('/').let { if (it.firstOrNull() == "3") it.drop(1) else it }
            when {
                seg.size == 2 && seg[0] == "search" -> {
                    val kind = seg[1]
                    val hits = mutableListOf<String>()
                    element.array("results").forEach { item ->
                        item.int("id")?.let { id ->
                            addPaths(item, "$kind/$id", PRIORITY_SEARCH)
                            addEntity(item, "$kind/$id", PRIORITY_SEARCH)
                            hits += "$kind/$id"
                        }
                    }
                    searches += MapSearchHit(kind, b.query.orEmpty(), b.language, rank = 0) to hits
                }

                seg.size == 2 && seg[0] in KINDS -> {
                    val ref = "${seg[0]}/${seg[1]}"
                    addPaths(element, ref, PRIORITY_DETAIL)
                    addEntity(element, ref, PRIORITY_DETAIL)
                    when (seg[0]) {
                        "tv" -> element.array("seasons").forEach { season ->
                            season.int("season_number")?.let { n -> addPaths(season, "$ref/season/$n", PRIORITY_DETAIL) }
                        }

                        "movie" -> (element["belongs_to_collection"] as? JsonObject)?.let { c ->
                            c.int("id")?.let { cid -> addPaths(c, "collection/$cid", PRIORITY_SEARCH) }
                        }

                        "collection" -> element.array("parts").forEach { part ->
                            // 合集当集表用时, 剧照就是各部电影的横图; 出处记合集本身
                            addPaths(part, ref, PRIORITY_DETAIL)
                            part.int("id")?.let { pid ->
                                addPaths(part, "movie/$pid", PRIORITY_SEARCH)
                                addEntity(part, "movie/$pid", PRIORITY_SEARCH)
                            }
                        }
                    }
                }

                seg.size == 3 && seg[0] in KINDS && seg[2] == "images" -> {
                    val ref = "${seg[0]}/${seg[1]}"
                    for (key in listOf("backdrops", "posters", "logos", "stills")) {
                        element.array(key).forEach { img -> img.string("file_path")?.let { add(it, ref, PRIORITY_IMAGES) } }
                    }
                }

                seg.size == 4 && seg[0] == "tv" && seg[2] == "season" -> {
                    val ref = "tv/${seg[1]}/season/${seg[3]}"
                    addPaths(element, ref, PRIORITY_IMAGES)
                    element.array("episodes").forEach { ep -> ep.string("still_path")?.let { add(it, ref, PRIORITY_IMAGES) } }
                }
            }
        }
    }

    private fun addPaths(obj: JsonElement, ref: String, priority: Int) {
        for (key in listOf("backdrop_path", "poster_path", "still_path")) {
            obj.string(key)?.let { add(it, ref, priority) }
        }
    }

    private fun addEntity(obj: JsonElement, ref: String, priority: Int) {
        val o = obj as? JsonObject ?: return
        val name = o.string("name") ?: o.string("title") ?: return
        val entity = MapTmdbEntity(
            ref = ref,
            name = name,
            original = (o.string("original_name") ?: o.string("original_title"))?.takeIf { it != name },
            date = o.string("first_air_date") ?: o.string("release_date"),
            overview = o.string("overview")?.let { if (it.length > OVERVIEW_MAX) it.take(OVERVIEW_MAX) + "…" else it },
            lang = o.string("original_language"),
            countries = o.array("origin_country").mapNotNull { (it as? JsonPrimitive)?.contentOrNull },
            genres = o.array("genre_ids").mapNotNull { (it as? JsonPrimitive)?.intOrNull } +
                o.array("genres").mapNotNull { it.int("id") },
            vote = (o["vote_average"] as? JsonPrimitive)?.contentOrNull?.toDoubleOrNull()?.takeIf { it > 0 },
            votes = o.int("vote_count")?.takeIf { it > 0 },
            poster = o.string("poster_path"),
            backdrop = o.string("backdrop_path"),
            status = o.string("status"),
            episodes = o.int("number_of_episodes"),
            seasons = o.array("seasons").mapNotNull { season ->
                season.int("season_number")?.let { n ->
                    MapTmdbSeason(n, season.string("name"), season.string("air_date"), season.int("episode_count"))
                }
            },
        )
        val existing = entities[ref]
        if (existing == null || priority > existing.first) entities[ref] = priority to entity
    }

    fun entityOf(ref: String): MapTmdbEntity? = entities[ref]?.second

    /** 第一次把 [ref] 搜出来的那次搜索, rank 从 1 起. */
    fun firstHitOf(ref: String): MapSearchHit? = searches.firstNotNullOfOrNull { (hit, results) ->
        results.indexOf(ref).takeIf { it >= 0 }?.let { hit.copy(rank = it + 1) }
    }

    /** 搜索结果里出现过的其他条目, 按首次出现排序. */
    fun candidates(exclude: String?, limit: Int): List<MapTmdbEntity> =
        searches.asSequence().flatMap { it.second }.distinct().filter { it != exclude }
            .mapNotNull { entityOf(it)?.copy(overview = null, poster = null, seasons = emptyList(), status = null, episodes = null) }
            .take(limit).toList()

    private fun add(path: String, ref: String, priority: Int) {
        val list = refs.getOrPut(path) { mutableListOf() }
        if (list.none { it.second == ref }) list += priority to ref
    }

    /** 按可信度从高到低 (同级按先后). */
    fun refsOf(path: String): List<String> =
        refs[path].orEmpty().withIndex().sortedWith(compareBy({ -it.value.first }, { it.index })).map { it.value.second }

    /**
     * 一组剧照的出处. 分季优先; 没有分季时, 多张图全都出自同一个合集就记合集 (合集当集表),
     * 否则记各自的电影 (单集条目取电影横图).
     */
    fun stillRefsOf(paths: List<String>): List<String> {
        if (paths.isEmpty()) return emptyList()
        val all = paths.associateWith { refsOf(it) }
        val result = mutableSetOf<String>()
        val rest = mutableListOf<String>()
        for ((path, refs) in all) {
            refs.firstOrNull { "/season/" in it }?.let { result += it } ?: rest.add(path)
        }
        if (rest.isEmpty()) return result.sorted()
        val sharedCollection = rest.map { p -> all.getValue(p).filter { it.startsWith("collection/") }.toSet() }
            .reduce { a, b -> a intersect b }.firstOrNull()
        if (rest.size > 1 && sharedCollection != null) {
            result += sharedCollection
        } else {
            for (path in rest) {
                val refs = all.getValue(path)
                (refs.firstOrNull { it.startsWith("movie/") } ?: refs.firstOrNull())?.let { result += it }
            }
        }
        return result.sorted()
    }

    private fun JsonElement.array(key: String): List<JsonElement> = ((this as? JsonObject)?.get(key) as? JsonArray).orEmpty()
    private fun JsonElement.string(key: String): String? =
        ((this as? JsonObject)?.get(key) as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() && it != "null" }

    private fun JsonElement.int(key: String): Int? = ((this as? JsonObject)?.get(key) as? JsonPrimitive)?.intOrNull

    private companion object {
        val KINDS = setOf("tv", "movie", "collection")
        const val PRIORITY_SEARCH = 1
        const val PRIORITY_DETAIL = 2
        const val PRIORITY_IMAGES = 3
        const val OVERVIEW_MAX = 160
    }
}
