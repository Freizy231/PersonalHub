"""Cached Simplified Chinese introductions; original GitHub text stays intact."""
import re
import time
from urllib.parse import quote

import requests

EMPTY_DESCRIPTION = "该项目暂未提供简介，请查看 GitHub 仓库了解详情。"
PENDING_DESCRIPTION = "中文简介待生成，请稍后刷新。"
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
TRANSLATE_PARAMS = {"client": "dict-chrome-ex", "sl": "auto", "tl": "zh-CN", "dt": "t"}
MARKER = re.compile(r"⟦(\d{6})⟧")
HAN = re.compile(r"[\u3400-\u9fff]")
FOREIGN_SCRIPT = re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")


class TranslationError(RuntimeError):
    pass


def local_description(source):
    """Only reuse clearly Chinese text; mixed English paragraphs still get translated."""
    source = (source or "").strip()
    if not source:
        return EMPTY_DESCRIPTION
    letters = [ch for ch in source if ch.isalpha()]
    if (letters and len(HAN.findall(source)) / len(letters) >= 0.5
            and not re.search(r"[\u3040-\u30ff\uac00-\ud7af]", source)):
        return source
    if not letters:
        return f"该项目的原始简介仅包含符号或数字：{source}"
    return None


def make_batches(sources):
    """Bound query size and mark each entry to detect missing/reordered results."""
    batch = []
    for source in dict.fromkeys(sources):
        if MARKER.search(source):
            raise TranslationError("简介包含翻译分隔标记，需人工核对")
        # Auto-detection applies to the whole query. A Chinese/Korean/etc. entry
        # must not cause English entries in the same batch to stay untranslated.
        if any(ch.isalpha() and not ch.isascii() for ch in source):
            if batch:
                yield batch
                batch = []
            yield [source]
            continue
        candidate = batch + [source]
        query = batch_query(candidate)
        if batch and (len(candidate) > 20 or len(quote(query, safe="")) > 6500):
            yield batch
            batch = [source]
        else:
            batch = candidate
    if batch:
        yield batch


def batch_query(sources):
    return "\n\n".join(f"⟦{100000 + index}⟧\n{source}" for index, source in enumerate(sources))


def parse_translation(payload, sources):
    try:
        text = "".join(part[0] for part in payload[0] if part[0] is not None)
    except (TypeError, IndexError, KeyError):
        raise TranslationError("翻译服务返回的数据格式异常") from None
    matches = list(MARKER.finditer(text))
    expected = [str(100000 + index) for index in range(len(sources))]
    if [match.group(1) for match in matches] != expected or text[:matches[0].start()].strip():
        raise TranslationError("翻译结果与项目无法一一对应，本批次未写入")
    results = {}
    for index, (source, match) in enumerate(zip(sources, matches)):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        translated = text[match.end():end].strip()
        if not translated or not HAN.search(translated) or FOREIGN_SCRIPT.search(translated):
            raise TranslationError("翻译结果缺少中文内容，本批次未写入")
        results[source] = translated
    return results


class ChineseTranslator:
    def __init__(self):
        # Dedicated session: never reuse GitHub Authorization headers.
        self.session = requests.Session()

    def close(self):
        self.session.close()

    def translate_many(self, sources):
        for attempt in range(3):
            try:
                params = {**TRANSLATE_PARAMS, "q": batch_query(sources)}
                if len(sources) == 1 and HAN.search(sources[0]) and not FOREIGN_SCRIPT.search(sources[0]):
                    # Translate the English portion of a bilingual description.
                    params["sl"] = "en"
                response = self.session.get(TRANSLATE_URL, params=params,
                                            timeout=(10, 30), allow_redirects=False)
                try:
                    if response.status_code != 200:
                        raise TranslationError(f"中文翻译服务 HTTP {response.status_code}")
                    return parse_translation(response.json(), sources)
                finally:
                    response.close()
            except (requests.RequestException, ValueError, TranslationError):
                if attempt == 2:
                    raise TranslationError("中文翻译暂不可用，已保留原文；下次采集会重试") from None
                time.sleep(2 ** attempt)


def save_translations(con, sources, results):
    """Use the exact source as a cache key; don't attach stale text after an edit."""
    with con:
        for source in sources:
            con.execute("""UPDATE repos SET description_zh=?,description_zh_source=?
                WHERE COALESCE(description,'')=?""", (results[source], source, source))


def pending_sources(con):
    return [row[0] for row in con.execute("""SELECT DISTINCT COALESCE(description,'') FROM repos
        WHERE description_zh IS NULL OR TRIM(description_zh)=''
           OR description_zh_source IS NOT COALESCE(description,'') ORDER BY id""")]


def sync_descriptions(con, run, translator_factory=ChineseTranslator):
    sources = pending_sources(con)
    done = 0
    run.checkpoint("translation", 0, len(sources), "", "生成中文项目简介")
    remote = []
    for source in sources:
        local = local_description(source)
        if local is None:
            remote.append(source)
        else:
            save_translations(con, [source], {source: local})
            done += 1
    # A public description may contain our own batch marker (it is attacker-controlled text).
    # Such a description cannot be translated safely, but it must not abort every other one.
    flagged = [source for source in remote if MARKER.search(source)]
    if flagged:
        remote = [source for source in remote if not MARKER.search(source)]
        run.error("translation", f"{len(flagged)} 条简介含翻译分隔标记，已跳过，需人工核对", TranslationError("简介包含翻译分隔标记，需人工核对"))
        done += len(flagged)
    translator = translator_factory()
    try:
        for batch in make_batches(remote):
            run.checkpoint(done=done, item="", message=f"翻译中文简介（本批 {len(batch)} 条）")
            try:
                results = translator.translate_many(batch)
                save_translations(con, batch, results)
            except TranslationError as error:
                run.error("translation", f"第 {done + 1}–{done + len(batch)} 条简介", error)
            done += len(batch)
            run.checkpoint(done=done, message="当前批次简介已处理")
    finally:
        translator.close()
    run.checkpoint(done=done, message="中文简介处理完成")
