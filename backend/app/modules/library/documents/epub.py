"""Bounded EPUB reader with no third-party dependency.

EPUB is an untrusted zip archive. Every limit below exists because a malformed
or hostile file must fail cheaply rather than exhaust disk, memory or CPU:

* entry count, per-entry and total uncompressed size, and a compression-ratio cap
  defend against zip bombs;
* absolute paths and `..` components are rejected outright;
* encrypted entries are refused instead of guessed at;
* any XML containing a DOCTYPE or ENTITY declaration is refused, which removes
  entity-expansion attacks without needing a hardened parser;
* only manifest items the OPF declares, in spine order, are read.
"""
import posixpath
import re
import zipfile
import zlib
from urllib.parse import unquote

from ....core.database import HubError

MAX_ENTRIES = 5000
MAX_ENTRY_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_RATIO = 200
MAX_TEXT = 16 * 1024 * 1024
MAX_CHAPTERS = 2000
MAX_SPINE_BYTES = 32 * 1024 * 1024

XML_UNSAFE = re.compile(rb'<!(?:DOCTYPE|ENTITY)', re.IGNORECASE)
BLOCK_TAGS = frozenset(('p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'br', 'tr', 'section', 'article', 'blockquote'))
SKIP_TAGS = frozenset(('script', 'style', 'head'))
ENTITIES = {'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"', 'apos': "'", 'nbsp': ' '}


def _safe(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise HubError('EPUB_UNSAFE', 422)
    if name.startswith('/') or posixpath.isabs(name):
        raise HubError('EPUB_UNSAFE', 422)
    parts = name.split('/')
    if any(part in ('', '.') and name != '' for part in parts[:-1]) or '..' in parts:
        raise HubError('EPUB_UNSAFE', 422)
    return name


def _open(path):
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise HubError('EPUB_INVALID', 422) from None
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        archive.close()
        raise HubError('EPUB_RESOURCE_BUDGET', 413)
    total = 0
    for info in infos:
        _safe(info.filename)
        if info.flag_bits & 0x1:
            archive.close()
            raise HubError('EPUB_ENCRYPTED', 422)
        if info.file_size > MAX_ENTRY_BYTES or info.file_size < 0:
            archive.close()
            raise HubError('EPUB_RESOURCE_BUDGET', 413)
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            archive.close()
            raise HubError('EPUB_RESOURCE_BUDGET', 413)
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            archive.close()
            raise HubError('EPUB_RESOURCE_BUDGET', 413)
    return archive, {info.filename for info in infos}


def _read(archive, names, name, limit=MAX_ENTRY_BYTES):
    name = _safe(name)
    if name not in names:
        raise HubError('EPUB_INVALID', 422)
    try:
        with archive.open(name) as handle:
            data = handle.read(limit + 1)
    except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError):
        # Bad CRC, truncated or unsupported compression: refuse this file, do not crash the worker.
        raise HubError('EPUB_INVALID', 422) from None
    if len(data) > limit:
        raise HubError('EPUB_RESOURCE_BUDGET', 413)
    return data


def _xml(data):
    if XML_UNSAFE.search(data):
        raise HubError('EPUB_XML_UNSAFE', 422)
    return data


def _unescape(text):
    def replace(match):
        body = match.group(1)
        if body.startswith('#'):
            try:
                code = int(body[2:], 16) if body[1:2].lower() == 'x' else int(body[1:])
            except ValueError:
                return ''
            # Lone surrogates cannot be encoded as UTF-8 and would abort the whole import.
            return chr(code) if 0 < code < 0x110000 and not 0xD800 <= code <= 0xDFFF else ''
        return ENTITIES.get(body.lower(), '')
    return re.sub(r'&(#?[A-Za-z0-9]+);', replace, text)


def _text(raw):
    """Visible text of an (X)HTML chapter, in one linear pass.

    Both the earlier regexes and `html.parser` go quadratic on markup such as
    thousands of unclosed `<a ` or `<!--` openers (40 KB took 12 s), and the
    work holds the interpreter lock. This scanner moves forward only: every
    character is looked at a bounded number of times, whatever the input.
    Unterminated constructs are treated as plain text up to the end.
    """
    parts, hidden, i, n = [], 0, 0, len(raw)
    find = raw.find
    while i < n:
        lt = find('<', i)
        if lt < 0:
            if not hidden:
                parts.append(raw[i:])
            break
        if lt > i and not hidden:
            parts.append(raw[i:lt])
        if raw.startswith('<!--', lt):
            end = find('-->', lt + 4)
            i = n if end < 0 else end + 3
            continue
        gt = find('>', lt + 1)
        if gt < 0:                      # unterminated tag: stop, nothing after it is markup we can trust
            if not hidden:
                parts.append(raw[lt:].replace('<', ' '))
            break
        body = raw[lt + 1:gt]
        closing = body.startswith('/')
        j = 1 if closing else 0
        k = j
        while k < len(body) and (body[k].isalnum() or body[k] == ':'):
            k += 1
        name = body[j:k].lower()
        if not name:
            if not hidden:
                parts.append(' ')       # "<" followed by something that is not a tag
            i = lt + 1 if not body.strip() else gt + 1
            if not body.strip():
                i = lt + 1
            continue
        if closing:
            if name in SKIP_TAGS:
                hidden = max(0, hidden - 1)
            elif name in BLOCK_TAGS:
                parts.append('\n')
        else:
            self_closing = body.rstrip().endswith('/')
            if name in SKIP_TAGS and not self_closing:
                hidden += 1
            elif name == 'br':
                parts.append('\n')
        i = gt + 1
    value = _unescape(''.join(parts))
    lines = [' '.join(line.split()) for line in value.splitlines()]
    return '\n'.join(line for line in lines if line)


def _attr(xml_text, pattern):
    match = re.search(pattern, xml_text, re.I)
    return match.group(1) if match else ''


def _container_path(archive, names):
    data = _xml(_read(archive, names, 'META-INF/container.xml'))
    text = data.decode('utf-8', 'replace')
    if 'full-path=' in text:
        path = _attr(text, r'full-path\s*=\s*["\']([^"\']+)["\']')
        if path:
            return path
    # Some files omit container.xml; fall back to any single OPF at the root.
    candidates = [name for name in names if name.lower().endswith('.opf')]
    if len(candidates) == 1:
        return candidates[0]
    raise HubError('EPUB_INVALID', 422)


def _manifest(opf_text):
    entries = {}
    for tag in re.findall(r'<item\b[^>]*>', opf_text, re.I):
        path = _attr(tag, r'\bhref\s*=\s*["\']([^"\']+)["\']')
        item_id = _attr(tag, r'\bid\s*=\s*["\']([^"\']+)["\']')
        media = _attr(tag, r'\bmedia-type\s*=\s*["\']([^"\']+)["\']')
        if path and item_id:
            entries[item_id] = (path, media.lower())
    return entries


def _spine(opf_text):
    return re.findall(r'<itemref\b[^>]*\bidref\s*=\s*["\']([^"\']+)["\']', opf_text, re.I)


def extract(path):
    archive, names = _open(path)
    try:
        opf_name = _container_path(archive, names)
        opf = _xml(_read(archive, names, opf_name)).decode('utf-8', 'replace')
        base = posixpath.dirname(opf_name)
        manifest = _manifest(opf)
        spine = _spine(opf)

        title = _attr(opf, r'<dc:title[^>]*>([^<]*)</dc:title>') or _attr(opf, r'<title[^>]*>([^<]*)</title>')
        author = _attr(opf, r'<dc:creator[^>]*>([^<]*)</dc:creator>')
        subject = _attr(opf, r'<dc:subject[^>]*>([^<]*)</dc:subject>')
        created = _attr(opf, r'<dc:date[^>]*>([^<]*)</dc:date>')
        language = _attr(opf, r'<dc:language[^>]*>([^<]*)</dc:language>')

        chapters, chunks, used, documents, spine_bytes = [], [], 0, 0, 0
        for ref in spine:
            item = manifest.get(ref)
            if not item:
                continue
            href, media = item
            if media and not (media in ('application/xhtml+xml', 'text/html') or media.endswith('xml')):
                continue
            target = unquote(href.split('#', 1)[0])
            if not target:
                continue
            relative = posixpath.normpath(posixpath.join(base, target)) if base else posixpath.normpath(target)
            if relative.startswith('..') or _safe(relative) != relative:
                raise HubError('EPUB_UNSAFE', 422)
            used += 1
            if used > MAX_CHAPTERS:
                raise HubError('EPUB_RESOURCE_BUDGET', 413)
            raw = _xml(_read(archive, names, relative))
            spine_bytes += len(raw)
            if spine_bytes > MAX_SPINE_BYTES:
                raise HubError('EPUB_RESOURCE_BUDGET', 413)
            documents += 1
            chunk = _text(raw.decode('utf-8', 'replace'))
            if chunk.strip():
                chapters.append(chunk)
            chunks.append(chunk)
            if sum(len(c) for c in chunks) > MAX_TEXT:
                raise HubError('EPUB_TEXT_BUDGET', 413)

        text = '\n\n'.join(chunks).strip()
        encoded = text.encode('utf-8')
        if len(encoded) > MAX_TEXT:
            raise HubError('EPUB_TEXT_BUDGET', 413)
        return {
            'page_count': max(1, documents),
            'title': _unescape(title).strip()[:2000],
            'author': _unescape(author).strip()[:2000],
            'subject': _unescape(subject).strip()[:2000],
            'created': _unescape(created).strip()[:2000],
            # The worker stores text as UTF-8 bytes, matching the PDF adapter.
            'text': encoded,
            'language': language.strip()[:64],
            'chapter_count': len(chapters),
        }
    finally:
        archive.close()
