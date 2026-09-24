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
import me.him188.ani.app.data.models.subject.SubjectInfo
import me.him188.ani.app.data.models.subject.toTmdbMatchHints
import me.him188.ani.app.data.network.mapper.toEntity
import me.him188.ani.app.data.persistent.MemoryDataStore
import me.him188.ani.app.domain.foundation.DefaultHttpClientProvider
import me.him188.ani.app.domain.foundation.ScopedHttpClientFeatureHandler
import me.him188.ani.app.domain.foundation.ScopedHttpClientUserAgent
import me.him188.ani.app.domain.foundation.ServerListFeatureHandler
import me.him188.ani.app.domain.foundation.UserAgentFeature
import me.him188.ani.app.domain.foundation.UserAgentFeatureHandler
import me.him188.ani.app.domain.settings.NoProxyProvider
import me.him188.ani.app.platform.currentAniBuildConfig
import me.him188.ani.datasources.api.PackedDate
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
                language = "zh-CN",
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
            return Out(
                id = job.id,
                ok = handler.failures.isEmpty() && handler.blocked.isEmpty(),
                backdrop = backdropPath?.let { backdropIndex.refsOf(it).firstOrNull() },
                backdropPath = backdropPath,
                stills = stillsIndex.stillRefsOf(stillPaths),
                stillCount = stillPaths.size,
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
    private data class Ep(val name: String = "", val airdate: String = "")

    @Serializable
    private data class Out(
        val id: Int,
        val ok: Boolean,
        /** 背景图出处, 形如 `tv/65942`, `movie/9323`, `collection/404609` */
        val backdrop: String? = null,
        val backdropPath: String? = null,
        /** 分集剧照出处, 形如 `tv/65942/season/3`; 单集电影与合集是 `movie/…` / `collection/…` */
        val stills: List<String> = emptyList(),
        val stillCount: Int = 0,
        val requests: Int = 0,
        val bgmCalls: Int = 0,
        val ms: Long = 0,
        /** 图片路径在本任务的 TMDB 响应里找不到出处的个数; 应当恒为 0 */
        val unresolvedPaths: Int = 0,
        val error: String? = null,
    )

    private companion object {
        val json = Json { ignoreUnknownKeys = true; encodeDefaults = false }
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
    /** (请求路径, 响应体): 只收成功的 TMDB 响应, 供 [OriginIndex] 反查图片出处. */
    val tmdbBodies = ConcurrentLinkedQueue<Pair<String, String>>()
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
        val path = request.url.build().encodedPath
        request.headers[HttpHeaders.UserAgent] = USER_AGENT
        var attempt = 0
        while (true) {
            attempt++
            limiter.acquire()
            tmdbRequests.incrementAndGet()
            val retryAfterMillis: Long
            try {
                val saved = execute(HttpRequestBuilder().takeFrom(request)).save()
                tmdbBodies.add(path to saved.response.bodyAsText())
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

/**
 * 「图片路径 → 出处」: 从本任务收到的 TMDB 响应里收集. 同一路径可能出现在多处 (合集与其中的电影、
 * 搜索结果与详情), 按可信度排: 图片列表/分季 > 详情 > 搜索结果.
 */
private class OriginIndex(bodies: Collection<Pair<String, String>>) {
    private val refs = mutableMapOf<String, MutableList<Pair<Int, String>>>()

    init {
        val parser = Json { ignoreUnknownKeys = true }
        for ((rawPath, body) in bodies) {
            val element = runCatching { parser.parseToJsonElement(body) }.getOrNull() as? JsonObject ?: continue
            // 形如 /3/tv/65942/season/3; 去掉版本号
            val seg = rawPath.trim('/').split('/').let { if (it.firstOrNull() == "3") it.drop(1) else it }
            when {
                seg.size == 2 && seg[0] == "search" -> {
                    val kind = seg[1]
                    element.array("results").forEach { item ->
                        item.int("id")?.let { id -> addPaths(item, "$kind/$id", PRIORITY_SEARCH) }
                    }
                }

                seg.size == 2 && seg[0] in KINDS -> {
                    val ref = "${seg[0]}/${seg[1]}"
                    addPaths(element, ref, PRIORITY_DETAIL)
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
                            part.int("id")?.let { pid -> addPaths(part, "movie/$pid", PRIORITY_SEARCH) }
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
    }
}
